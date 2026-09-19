from django.db import models


class Order(models.Model):
    """订单（含下单时快照）。金额单位：分。"""

    order_no = models.CharField("订单号", max_length=32, unique=True)
    customer_name = models.CharField("顾客", max_length=64)
    bundle_name = models.CharField("套装名称", max_length=128)

    total_list_cents = models.IntegerField("原价合计(分)")
    discount_cents = models.IntegerField("优惠总额(分)")
    shipping_cents = models.IntegerField("运费(分)")
    paid_cents = models.IntegerField("实付(分)")

    snapshot = models.JSONField("下单快照", default=dict)
    created_at = models.DateTimeField("下单时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_no} {self.bundle_name}"


class OrderItem(models.Model):
    """订单行。分摊结果在下单时计算并落库，作为后续一切退款计算的基准。"""

    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    line_no = models.IntegerField("行号")  # 稳定排序依据
    sku = models.CharField("SKU", max_length=32)
    title = models.CharField("商品名", max_length=128)
    unit_price_cents = models.IntegerField("单价(分)")
    qty = models.IntegerField("数量")
    is_gift = models.BooleanField("赠品", default=False)

    list_total_cents = models.IntegerField("行原价(分)")
    weight = models.IntegerField("分摊权重")  # = 行原价，赠品为 0
    discount_alloc_cents = models.IntegerField("优惠分摊(分)")
    shipping_alloc_cents = models.IntegerField("运费分摊(分)")
    paid_alloc_cents = models.IntegerField("实付分摊(分)")

    class Meta:
        ordering = ["line_no"]
        unique_together = [("order", "line_no")]

    def __str__(self):
        return f"{self.order.order_no}#{self.line_no} {self.title}"


class Refund(models.Model):
    """退款单。

    状态机：PENDING(待核实/占用中) -> SUCCESS / FAILED
    PENDING 与 SUCCESS 均占用订单可退额度；FAILED 释放占用。
    支付网关结果未知时保持 PENDING，绝不释放额度。
    """

    STATUS_PENDING = "PENDING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待核实"),
        (STATUS_SUCCESS, "退款成功"),
        (STATUS_FAILED, "支付失败"),
    ]

    refund_no = models.CharField("退款单号", max_length=32, unique=True)
    order = models.ForeignKey(Order, related_name="refunds", on_delete=models.PROTECT)
    idempotency_key = models.CharField("幂等键", max_length=64, unique=True)
    amount_cents = models.IntegerField("应退金额(分)")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    transaction_id = models.CharField("支付流水号", max_length=64, unique=True, null=True, blank=True)
    failure_reason = models.CharField("失败原因", max_length=256, blank=True, default="")

    trace = models.JSONField("计算依据快照", default=dict)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    finished_at = models.DateTimeField("完结时间", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.refund_no}({self.get_status_display()} ¥{self.amount_cents / 100:.2f})"


class RefundItem(models.Model):
    """退款明细行：退哪个订单行、多少件、多少钱、怎么算出来的。"""

    refund = models.ForeignKey(Refund, related_name="items", on_delete=models.CASCADE)
    order_item = models.ForeignKey(OrderItem, related_name="refund_items", on_delete=models.PROTECT)
    qty = models.IntegerField("退货数量")
    amount_cents = models.IntegerField("行退款金额(分)")
    calc_detail = models.JSONField("行计算依据", default=dict)

    class Meta:
        ordering = ["order_item__line_no"]


class PaymentEvent(models.Model):
    """支付网关回调事件流水。event_id 唯一，保证重复回调幂等。"""

    RESULT_ACCEPTED = "accepted"      # 受理并推动状态
    RESULT_HELD = "held"              # 结果未知，保持待核实
    RESULT_DUPLICATE = "duplicate"    # 重复回调，幂等忽略
    RESULT_CONFLICT = "conflict"      # 与已完结状态冲突，拒绝
    RESULT_CHOICES = [
        (RESULT_ACCEPTED, "已受理"),
        (RESULT_HELD, "结果未知-保持待核实"),
        (RESULT_DUPLICATE, "重复回调-幂等忽略"),
        (RESULT_CONFLICT, "状态冲突-已拒绝"),
    ]

    refund = models.ForeignKey(Refund, related_name="events", on_delete=models.CASCADE)
    event_id = models.CharField("回调事件号", max_length=64, unique=True)
    transaction_id = models.CharField("支付流水号", max_length=64)
    gateway_status = models.CharField("网关状态", max_length=16)
    result = models.CharField("处理结果", max_length=16, choices=RESULT_CHOICES)
    payload = models.JSONField("回调报文", default=dict)
    created_at = models.DateTimeField("接收时间", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
