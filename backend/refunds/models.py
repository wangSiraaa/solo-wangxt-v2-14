from django.db import models


class Order(models.Model):
    """订单快照：下单时的金额在创建后不再变化，所有分摊以快照为准。"""

    order_no = models.CharField("订单号", max_length=32, unique=True)
    title = models.CharField("订单标题", max_length=128)
    shipping_fee = models.IntegerField("运费(分)", default=0)
    discount_total = models.IntegerField("订单总优惠(分)", default=0)
    # 实付 = Σ标价 - 总优惠（运费单独收取、不参与优惠分摊）
    paid_total = models.IntegerField("商品实付(分)", default=0)
    created_at = models.DateTimeField("下单时间", auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.order_no


class OrderLine(models.Model):
    """订单行快照。allocated_discount / allocated_paid 在下单时按权重一次性分摊落库。"""

    STATUS_NORMAL = "NORMAL"
    STATUS_LOCKED = "LOCKED"  # 有进行中的退款占用
    STATUS_RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (STATUS_NORMAL, "正常"),
        (STATUS_LOCKED, "退款中"),
        (STATUS_RETURNED, "已退"),
    ]

    order = models.ForeignKey(Order, related_name="lines", on_delete=models.CASCADE)
    line_no = models.IntegerField("行号")  # 稳定排序键：尾差分配的决胜依据
    sku = models.CharField("SKU", max_length=32)
    title = models.CharField("商品名", max_length=128)
    list_price = models.IntegerField("标价(分)")  # 分摊权重
    is_gift = models.BooleanField("是否赠品", default=False)
    gift_value = models.IntegerField("赠品价值(分)", default=0)  # 未退回时末次退款扣除
    allocated_discount = models.IntegerField("分摊优惠(分)", default=0)
    allocated_paid = models.IntegerField("实付分摊(分)", default=0)
    refunded_amount = models.IntegerField("累计已退(分)", default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_NORMAL)

    class Meta:
        ordering = ["line_no"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_no"], name="uniq_order_line_no")
        ]

    def __str__(self):
        return f"{self.order.order_no}#{self.line_no} {self.title}"


class Refund(models.Model):
    """退款单。创建即占用额度；未知结果保持 PENDING_VERIFICATION，不释放占用。"""

    STATUS_PENDING = "PENDING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUS_PENDING_VERIFICATION = "PENDING_VERIFICATION"
    STATUS_CHOICES = [
        (STATUS_PENDING, "待提交"),
        (STATUS_SUCCESS, "退款成功"),
        (STATUS_FAILED, "退款失败"),
        (STATUS_PENDING_VERIFICATION, "待核实"),
    ]

    refund_no = models.CharField("退款单号", max_length=32, unique=True)
    order = models.ForeignKey(Order, related_name="refunds", on_delete=models.PROTECT)
    idempotency_key = models.CharField("幂等键", max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    amount = models.IntegerField("退款金额(分)", default=0)
    shipping_refund = models.IntegerField("退运费(分)", default=0)
    gift_deduction = models.IntegerField("赠品扣款(分)", default=0)
    is_final = models.BooleanField("是否末次结清", default=False)
    reason = models.CharField("退款原因", max_length=256, blank=True, default="")
    gateway_txn = models.CharField("支付网关流水", max_length=64, blank=True, default="")
    calc_trace = models.JSONField("计算依据", default=dict)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.refund_no


class RefundLine(models.Model):
    """退款行：记录每笔退款针对哪个订单行、分摊了多少。"""

    refund = models.ForeignKey(Refund, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(OrderLine, related_name="refund_lines", on_delete=models.PROTECT)
    amount = models.IntegerField("本行退款(分)", default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["refund", "order_line"], name="uniq_refund_order_line"
            )
        ]


class RefundEvent(models.Model):
    """退款事件流：网关回调、人工核实的完整留痕。重复回调按 callback_id 幂等。"""

    KIND_CALLBACK = "CALLBACK"
    KIND_RECHECK = "RECHECK"
    KIND_SUBMIT = "SUBMIT"
    KIND_CHOICES = [(KIND_CALLBACK, "网关回调"), (KIND_RECHECK, "主动核实"), (KIND_SUBMIT, "提交网关")]

    refund = models.ForeignKey(Refund, related_name="events", on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    callback_id = models.CharField("回调幂等号", max_length=64, blank=True, default="")
    payload = models.JSONField("报文", default=dict)
    note = models.CharField("处理结果", max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["refund", "callback_id"],
                condition=~models.Q(callback_id=""),
                name="uniq_refund_callback",
            )
        ]
