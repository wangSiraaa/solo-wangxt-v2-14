"""退款核算核心服务。

金额单位一律为「分」。所有写操作均在事务内并对订单行加锁（select_for_update），
保证并发下「累计退款 <= 实付」这一不变量。
"""
import uuid

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .allocation import allocate_cents, unit_refund_cents
from .models import Order, OrderItem, PaymentEvent, Refund, RefundItem

ACTIVE_STATUSES = (Refund.STATUS_PENDING, Refund.STATUS_SUCCESS)


class RefundError(Exception):
    """业务校验失败（如超退）。message 面向用户。"""


# ---------------------------------------------------------------- 订单与分摊

def create_order_snapshot(*, order_no, customer_name, bundle_name, lines,
                          discount_cents, shipping_cents):
    """按下单数据创建订单：按权重分摊优惠与运费，结果连同快照一起落库。

    lines: [{sku, title, unit_price_cents, qty, is_gift}]
    分摊规则：权重 = 行原价（赠品为 0）；尾差按稳定排序分配（见 allocation）。
    """
    total_list = sum(l["unit_price_cents"] * l["qty"] for l in lines)
    paid = total_list - discount_cents + shipping_cents
    if paid < 0:
        raise RefundError("优惠金额不能大于原价合计")

    weights = [0 if l.get("is_gift") else l["unit_price_cents"] * l["qty"] for l in lines]
    discount_allocs = allocate_cents(discount_cents, weights)
    shipping_allocs = allocate_cents(shipping_cents, weights)

    with transaction.atomic():
        order = Order.objects.create(
            order_no=order_no,
            customer_name=customer_name,
            bundle_name=bundle_name,
            total_list_cents=total_list,
            discount_cents=discount_cents,
            shipping_cents=shipping_cents,
            paid_cents=paid,
        )
        items = []
        for idx, (line, disc, ship) in enumerate(zip(lines, discount_allocs, shipping_allocs), start=1):
            list_total = line["unit_price_cents"] * line["qty"]
            paid_alloc = list_total - disc + ship
            items.append(OrderItem(
                order=order, line_no=idx, sku=line["sku"], title=line["title"],
                unit_price_cents=line["unit_price_cents"], qty=line["qty"],
                is_gift=bool(line.get("is_gift")),
                list_total_cents=list_total, weight=weights[idx - 1],
                discount_alloc_cents=disc, shipping_alloc_cents=ship,
                paid_alloc_cents=paid_alloc,
            ))
        OrderItem.objects.bulk_create(items)

        # 下单快照：原始输入 + 分摊结果，永久留档，供逐笔追溯
        order.snapshot = {
            "order_no": order_no,
            "customer_name": customer_name,
            "bundle_name": bundle_name,
            "totals": {
                "total_list_cents": total_list,
                "discount_cents": discount_cents,
                "shipping_cents": shipping_cents,
                "paid_cents": paid,
            },
            "allocation_rule": "优惠/运费按行原价权重分摊；分币尾差按「余数大者优先、行号小者优先」稳定分配",
            "lines": [
                {
                    "line_no": it.line_no, "sku": it.sku, "title": it.title,
                    "unit_price_cents": it.unit_price_cents, "qty": it.qty,
                    "is_gift": it.is_gift, "list_total_cents": it.list_total_cents,
                    "weight": it.weight,
                    "discount_alloc_cents": it.discount_alloc_cents,
                    "shipping_alloc_cents": it.shipping_alloc_cents,
                    "paid_alloc_cents": it.paid_alloc_cents,
                }
                for it in items
            ],
        }
        order.save(update_fields=["snapshot"])
    return order


# ---------------------------------------------------------------- 占用查询

def occupied_by_item(order):
    """返回 {order_item_id: (占用数量, 占用金额分)}。PENDING+SUCCESS 均视为占用。"""
    occupied = {item.id: [0, 0] for item in order.items.all()}
    rows = (
        RefundItem.objects
        .filter(refund__order=order, refund__status__in=ACTIVE_STATUSES)
        .values("order_item_id")
        .annotate(q=Sum("qty"), a=Sum("amount_cents"))
    )
    for row in rows:
        occupied[row["order_item_id"]] = [row["q"], row["a"]]
    return occupied


def order_occupied_total(order):
    """订单级已占用（已退 + 待核实）金额。"""
    return (
        Refund.objects
        .filter(order=order, status__in=ACTIVE_STATUSES)
        .aggregate(t=Sum("amount_cents"))["t"] or 0
    )


# ---------------------------------------------------------------- 退款试算

def compute_quote(*, order, selections):
    """试算退款。selections: {order_item_id: qty}。

    行级规则：
      - 单件可退 = 行实付分摊 // 行数量（向下取整）；
      - 退到该行最后一件时结清：行退款 = 行实付分摊 - 该行已占用金额（尾差随尾件结清）。
    订单级规则：
      - 本次退完全单所有剩余件数时，整单结清：总额 = 实付 - 已占用总额；
      - 任意情况下 本次 + 已占用 <= 实付。
    返回 (lines, total_cents, trace)。不落库。
    """
    selections = {int(k): int(v) for k, v in (selections or {}).items()}
    items = list(order.items.all())
    occupied = occupied_by_item(order)
    occupied_total = order_occupied_total(order)

    lines = []
    remaining_after = 0  # 本次之后全单剩余可退件数
    for item in items:
        occ_qty, occ_amt = occupied.get(item.id, (0, 0))
        avail_qty = item.qty - occ_qty
        q = int(selections.get(item.id, 0) or 0)
        if q < 0:
            raise RefundError(f"「{item.title}」退货数量不能为负数")
        if q > avail_qty:
            raise RefundError(
                f"「{item.title}」最多还可退 {avail_qty} 件"
                f"（下单 {item.qty} 件，已退/待核实占用 {occ_qty} 件）"
            )
        remaining_after += avail_qty - q
        if q == 0:
            continue

        unit = unit_refund_cents(item.paid_alloc_cents, item.qty)
        is_last = (occ_qty + q == item.qty)
        if item.is_gift or item.paid_alloc_cents == 0:
            amount = 0
            explain = (f"赠品「{item.title}」实付分摊 ¥0.00（权重为 0 不参与优惠/运费分摊），"
                       f"退货退款 ¥0.00")
        elif is_last:
            amount = item.paid_alloc_cents - occ_amt
            explain = (
                f"退到该行最后 {q} 件，结清行尾差："
                f"行实付分摊 ¥{item.paid_alloc_cents / 100:.2f}"
                f" − 已退/占用 ¥{occ_amt / 100:.2f}"
                f" = ¥{amount / 100:.2f}"
            )
        else:
            amount = unit * q
            explain = (
                f"单件可退 ¥{unit / 100:.2f}"
                f"（= 行实付分摊 ¥{item.paid_alloc_cents / 100:.2f} ÷ {item.qty} 件，向下取整）"
                f" × 退 {q} 件 = ¥{amount / 100:.2f}"
            )
        lines.append({
            "item": item, "qty": q, "amount_cents": amount,
            "detail": {
                "line_no": item.line_no, "sku": item.sku, "title": item.title,
                "is_gift": item.is_gift,
                "qty_requested": q,
                "line_paid_alloc_cents": item.paid_alloc_cents,
                "unit_cents": unit,
                "occupied_qty_before": occ_qty,
                "occupied_amount_before_cents": occ_amt,
                "is_last_unit": is_last,
                "amount_cents": amount,
                "explain": explain,
            },
        })

    if not lines:
        raise RefundError("请至少选择一件退货商品")

    settles_order = remaining_after == 0
    total = sum(l["amount_cents"] for l in lines)
    if settles_order:
        # 整单结清：以订单实付为最终基准（数学上恒等于逐行结清之和，这里以订单级为准）
        total = order.paid_cents - occupied_total

    if occupied_total + total > order.paid_cents:
        raise RefundError(
            f"累计退款将超过实付金额：已退/待核实 ¥{occupied_total / 100:.2f}"
            f" + 本次 ¥{total / 100:.2f} > 实付 ¥{order.paid_cents / 100:.2f}"
        )

    trace = {
        "order": {
            "order_no": order.order_no,
            "paid_cents": order.paid_cents,
            "occupied_before_cents": occupied_total,
            "remaining_after_cents": order.paid_cents - occupied_total - total,
        },
        "lines": [l["detail"] for l in lines],
        "order_level": {
            "settles_order": settles_order,
            "total_cents": total,
            "formula": (
                f"整单结清：实付 ¥{order.paid_cents / 100:.2f}"
                f" − 已退/占用 ¥{occupied_total / 100:.2f}"
                f" = ¥{total / 100:.2f}"
                if settles_order else
                f"部分退货：逐行求和 = ¥{total / 100:.2f}；"
                f"校验 已占用 ¥{occupied_total / 100:.2f} + 本次 ¥{total / 100:.2f}"
                f" ≤ 实付 ¥{order.paid_cents / 100:.2f}"
            ),
        },
    }
    return lines, total, trace


# ---------------------------------------------------------------- 创建退款

@transaction.atomic
def create_refund(*, order_id, selections, idempotency_key):
    """创建退款单（PENDING，立即占用额度）。幂等：同一 idempotency_key 返回原单。"""
    if not idempotency_key:
        raise RefundError("缺少幂等键 Idempotency-Key")

    existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing, False

    order = Order.objects.select_for_update().get(pk=order_id)
    lines, total, trace = compute_quote(order=order, selections=selections)

    refund = Refund(
        refund_no=f"RF{timezone.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
        order=order,
        idempotency_key=idempotency_key,
        amount_cents=total,
        status=Refund.STATUS_PENDING,
        trace=trace,
    )
    try:
        refund.save()
    except IntegrityError:
        # 并发下同幂等键已落库，返回已存在的那一单
        return Refund.objects.get(idempotency_key=idempotency_key), False

    RefundItem.objects.bulk_create([
        RefundItem(refund=refund, order_item=l["item"], qty=l["qty"],
                   amount_cents=l["amount_cents"], calc_detail=l["detail"])
        for l in lines
    ])
    return refund, True


# ---------------------------------------------------------------- 支付与回调

@transaction.atomic
def simulate_payment(*, refund_id, outcome):
    """支付模拟器：模拟网关对一笔 PENDING 退款的处理。

    outcome:
      success -> 网关回调 SUCCESS
      failed  -> 网关回调 FAILED（释放占用额度）
      unknown -> 网关返回未知：退款保持 PENDING（待核实），额度继续占用，不得再次退款
    """
    refund = Refund.objects.select_for_update().get(pk=refund_id)
    if refund.status != Refund.STATUS_PENDING:
        raise RefundError(f"退款单 {refund.refund_no} 已是「{refund.get_status_display()}」，无需支付")
    if not refund.transaction_id:
        refund.transaction_id = f"TXN-{refund.refund_no}"
        refund.save(update_fields=["transaction_id"])

    if outcome == "unknown":
        PaymentEvent.objects.create(
            refund=refund, event_id=f"evt-{uuid.uuid4().hex[:16]}",
            transaction_id=refund.transaction_id, gateway_status="UNKNOWN",
            result=PaymentEvent.RESULT_HELD,
            payload={"simulated": True, "note": "网关超时/结果未知"},
        )
        return refund, "网关返回未知：退款保持「待核实」，额度继续占用，不能再次退款"

    status = "SUCCESS" if outcome == "success" else "FAILED"
    _, result, _ = handle_callback(
        event_id=f"evt-{uuid.uuid4().hex[:16]}",
        transaction_id=refund.transaction_id,
        status=status,
        payload={"simulated": True},
    )
    return Refund.objects.get(pk=refund.pk), result


@transaction.atomic
def handle_callback(*, event_id, transaction_id, status, payload=None):
    """处理支付网关回调。幂等设计：

    - 同一 event_id 重复投递      -> duplicate，直接返回当前状态；
    - 退款已完结后再收同结果通知   -> duplicate（不同 event_id 的重复通知）；
    - 退款已完结后收到相反结果     -> conflict，拒绝，保持原状态；
    - PENDING 收到 UNKNOWN        -> held，保持待核实，不释放额度。
    返回 (refund, result, http_status)。
    """
    payload = payload or {}

    dup = PaymentEvent.objects.filter(event_id=event_id).select_related("refund").first()
    if dup:
        return dup.refund, PaymentEvent.RESULT_DUPLICATE, 200

    try:
        refund = Refund.objects.select_for_update().get(transaction_id=transaction_id)
    except Refund.DoesNotExist:
        return None, "unknown_transaction", 404

    if refund.status != Refund.STATUS_PENDING:
        # 已完结：同结果视为重复通知（幂等成功），相反结果视为冲突
        same = refund.status == status
        result = PaymentEvent.RESULT_DUPLICATE if same else PaymentEvent.RESULT_CONFLICT
        PaymentEvent.objects.create(
            refund=refund, event_id=event_id, transaction_id=transaction_id,
            gateway_status=status, result=result, payload=payload,
        )
        return refund, result, 200 if same else 409

    if status == Refund.STATUS_SUCCESS:
        refund.status = Refund.STATUS_SUCCESS
        refund.finished_at = timezone.now()
        refund.save(update_fields=["status", "finished_at"])
        result = PaymentEvent.RESULT_ACCEPTED
    elif status == Refund.STATUS_FAILED:
        refund.status = Refund.STATUS_FAILED
        refund.failure_reason = payload.get("reason", "支付网关返回失败")
        refund.finished_at = timezone.now()
        refund.save(update_fields=["status", "failure_reason", "finished_at"])
        result = PaymentEvent.RESULT_ACCEPTED
    else:  # UNKNOWN 或其它非终态
        result = PaymentEvent.RESULT_HELD

    PaymentEvent.objects.create(
        refund=refund, event_id=event_id, transaction_id=transaction_id,
        gateway_status=status, result=result, payload=payload,
    )
    return refund, result, 200
