"""拆退核算核心逻辑。

金额单位一律为「分」(int)。关键不变量：
  1. Σ allocated_discount == order.discount_total（尾差按稳定排序逐分分配）
  2. Σ allocated_paid    == order.paid_total
  3. 任意时刻：Σ(成功退款) + Σ(进行中占用) <= paid_total (+ 末次退运费)
  4. 未知结果的退款保持 PENDING_VERIFICATION，占用不释放，不可重复退
"""
import uuid
from decimal import Decimal, ROUND_FLOOR

from django.db import transaction
from django.db.models import Q, Sum

from .models import Order, OrderLine, Refund, RefundEvent, RefundLine


class RefundError(Exception):
    """业务校验失败，返回 400。"""


# ---------------------------------------------------------------- 分摊算法

def allocate_discount(weights, total_discount):
    """按权重分摊总优惠，尾差用「最大余数法 + 稳定排序」逐分分配。

    返回与 weights 等长的分摊列表。决胜规则：
      余数大的先分；余数相同按下标(行号)升序 —— 完全确定、可复现。
    """
    total_weight = sum(weights)
    n = len(weights)
    if n == 0:
        return []
    if total_weight <= 0:
        # 全部为赠品/零价行：优惠无承担对象，按行号稳定均分兜底
        base, rem = divmod(total_discount, n)
        return [base + (1 if i < rem else 0) for i in range(n)]

    exact = [Decimal(total_discount) * w / total_weight for w in weights]
    floors = [int(e.to_integral_value(rounding=ROUND_FLOOR)) for e in exact]
    remainder = total_discount - sum(floors)
    # 稳定排序：(-余数, 行号)。余数用 Decimal 比较避免浮点误差。
    order = sorted(range(n), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def create_order_snapshot(order_no, title, lines, discount_total, shipping_fee):
    """下单快照：一次性算好每行分摊并落库，之后不再重算。

    lines: [{sku, title, list_price, is_gift, gift_value}]
    """
    weights = [0 if l.get("is_gift") else l["list_price"] for l in lines]
    paid_total = sum(weights) - discount_total
    if paid_total < 0:
        raise RefundError("优惠总额不能超过商品总价")
    allocs = allocate_discount(weights, discount_total)

    with transaction.atomic():
        order = Order.objects.create(
            order_no=order_no,
            title=title,
            discount_total=discount_total,
            shipping_fee=shipping_fee,
            paid_total=paid_total,
        )
        for idx, (line, alloc) in enumerate(zip(lines, allocs), start=1):
            w = 0 if line.get("is_gift") else line["list_price"]
            OrderLine.objects.create(
                order=order,
                line_no=idx,
                sku=line["sku"],
                title=line["title"],
                list_price=line["list_price"],
                is_gift=line.get("is_gift", False),
                gift_value=line.get("gift_value", 0),
                allocated_discount=alloc,
                allocated_paid=w - alloc,
            )
    return order


# ---------------------------------------------------------------- 退款核算

def _order_totals(order):
    """返回 (已成功退款商品金额, 进行中占用金额, 已退运费)。"""
    agg = order.refunds.aggregate(
        success=Sum("amount", filter=Q(status=Refund.STATUS_SUCCESS)),
        held=Sum(
            "amount",
            filter=Q(status__in=[Refund.STATUS_PENDING, Refund.STATUS_PENDING_VERIFICATION]),
        ),
        ship=Sum("shipping_refund", filter=Q(status=Refund.STATUS_SUCCESS)),
    )
    return agg["success"] or 0, agg["held"] or 0, agg["ship"] or 0


def build_calc_trace(order, selected, succeeded, held):
    """生成可逐笔追溯的计算依据（落库到 Refund.calc_trace）。"""
    remaining_lines = [
        l for l in order.lines.all()
        if l.status == OrderLine.STATUS_NORMAL and not l.is_gift
    ]
    is_final = len(remaining_lines) == len(selected) and len(selected) > 0
    unreturned_gifts = [
        l for l in order.lines.all()
        if l.is_gift and l.status != OrderLine.STATUS_RETURNED
        and l not in selected
    ]

    lines_base = sum(l.allocated_paid for l in selected)
    shipping_refund = order.shipping_fee if is_final else 0
    gift_deduction = sum(l.gift_value for l in unreturned_gifts) if is_final else 0

    if is_final:
        # 末次结清：可退余额全部结清，消除历次分摊尾差
        goods_amount = order.paid_total - succeeded - held
    else:
        goods_amount = lines_base
    goods_amount = max(goods_amount - gift_deduction, 0)

    trace = {
        "formula": "末次结清: 实付总额 - 已退 - 占用 - 赠品扣款 + 运费; 非末次: Σ行实付分摊",
        "is_final": is_final,
        "paid_total": order.paid_total,
        "succeeded_before": succeeded,
        "held_before": held,
        "selected_lines": [
            {
                "line_no": l.line_no,
                "title": l.title,
                "list_price": l.list_price,
                "allocated_discount": l.allocated_discount,
                "allocated_paid": l.allocated_paid,
            }
            for l in selected
        ],
        "lines_base": lines_base,
        "unreturned_gifts": [
            {"line_no": g.line_no, "title": g.title, "gift_value": g.gift_value}
            for g in unreturned_gifts
        ],
        "gift_deduction": gift_deduction,
        "shipping_refund": shipping_refund,
        "goods_amount": goods_amount,
    }
    return trace, goods_amount, shipping_refund, gift_deduction, is_final


@transaction.atomic
def create_refund(order_id, line_ids, reason, idempotency_key, simulate="random"):
    """创建退款并提交支付模拟器。幂等：相同 idempotency_key 直接返回原单。"""
    existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing, True

    order = Order.objects.select_for_update().get(pk=order_id)
    lines = list(order.lines.all())
    by_id = {l.id: l for l in lines}

    if not line_ids:
        raise RefundError("请选择要退的商品行")
    selected = []
    for lid in line_ids:
        line = by_id.get(lid)
        if line is None:
            raise RefundError(f"订单行 {lid} 不属于该订单")
        if line.status == OrderLine.STATUS_RETURNED:
            raise RefundError(f"「{line.title}」已退，不能重复退款")
        if line.status == OrderLine.STATUS_LOCKED:
            raise RefundError(f"「{line.title}」有待核实的退款，额度被占用，不能再次退款")
        selected.append(line)
    if any(l.is_gift for l in selected):
        raise RefundError("赠品无需单独退款，随末次退货自动结算")

    succeeded, held, _ = _order_totals(order)
    trace, goods_amount, shipping_refund, gift_deduction, is_final = build_calc_trace(
        order, selected, succeeded, held
    )
    total = goods_amount + shipping_refund
    if total <= 0:
        raise RefundError("可退金额为 0")
    # 累计上限校验：成功 + 占用 + 本次 <= 实付 (+末次运费)
    if succeeded + held + goods_amount > order.paid_total:
        raise RefundError("累计退款将超过实付金额，已拦截")

    refund = Refund.objects.create(
        refund_no="R" + uuid.uuid4().hex[:16].upper(),
        order=order,
        idempotency_key=idempotency_key,
        status=Refund.STATUS_PENDING,
        amount=goods_amount,
        shipping_refund=shipping_refund,
        gift_deduction=gift_deduction,
        is_final=is_final,
        reason=reason,
        calc_trace=trace,
    )
    for l in selected:
        RefundLine.objects.create(refund=refund, order_line=l, amount=l.allocated_paid)
        l.status = OrderLine.STATUS_LOCKED  # 占用额度
        l.save(update_fields=["status"])

    # 提交支付模拟器
    result, txn = PaymentGateway.refund(refund, simulate=simulate)
    refund.gateway_txn = txn
    RefundEvent.objects.create(
        refund=refund, kind=RefundEvent.KIND_SUBMIT,
        payload={"simulate": simulate, "gateway_txn": txn, "result": result},
        note=f"提交网关，同步结果: {result}",
    )
    _apply_gateway_result(refund, result)
    refund.save()
    return refund, False


def _apply_gateway_result(refund, result):
    """根据网关结果迁移状态。未知结果保持占用，绝不释放。"""
    if result == PaymentGateway.RESULT_SUCCESS:
        refund.status = Refund.STATUS_SUCCESS
        for rl in refund.lines.select_related("order_line"):
            line = rl.order_line
            line.status = OrderLine.STATUS_RETURNED
            line.refunded_amount = line.allocated_paid
            line.save(update_fields=["status", "refunded_amount"])
    elif result == PaymentGateway.RESULT_FAILURE:
        refund.status = Refund.STATUS_FAILED
        for rl in refund.lines.select_related("order_line"):
            line = rl.order_line
            line.status = OrderLine.STATUS_NORMAL  # 失败释放占用
            line.save(update_fields=["status"])
    else:  # UNKNOWN -> 待核实，行保持 LOCKED，额度继续占用
        refund.status = Refund.STATUS_PENDING_VERIFICATION


@transaction.atomic
def handle_gateway_callback(refund_id, callback_id, result):
    """处理网关异步回调。按 (refund, callback_id) 唯一约束幂等：重复回调直接返回。"""
    refund = Refund.objects.select_for_update().get(pk=refund_id)
    dup = RefundEvent.objects.filter(refund=refund, callback_id=callback_id).first()
    if dup:
        return refund, f"重复回调 {callback_id}，已忽略（幂等）"

    if refund.status in (Refund.STATUS_SUCCESS, Refund.STATUS_FAILED):
        note = f"退款已是终态 {refund.status}，回调仅留痕"
        RefundEvent.objects.create(
            refund=refund, kind=RefundEvent.KIND_CALLBACK, callback_id=callback_id,
            payload={"result": result}, note=note,
        )
        return refund, note

    _apply_gateway_result(refund, result)
    refund.save()
    note = f"回调受理: {result} -> {refund.status}"
    RefundEvent.objects.create(
        refund=refund, kind=RefundEvent.KIND_CALLBACK, callback_id=callback_id,
        payload={"result": result}, note=note,
    )
    return refund, note


@transaction.atomic
def recheck_refund(refund_id, force="random"):
    """财务主动核实待核实退款（查询网关最新状态）。"""
    refund = Refund.objects.select_for_update().get(pk=refund_id)
    if refund.status != Refund.STATUS_PENDING_VERIFICATION:
        return refund, f"当前状态 {refund.status}，无需核实"
    result = PaymentGateway.query(refund, force=force)
    if result != PaymentGateway.RESULT_UNKNOWN:
        _apply_gateway_result(refund, result)
        refund.save()
    note = f"核实结果: {result} -> {refund.status}"
    RefundEvent.objects.create(
        refund=refund, kind=RefundEvent.KIND_RECHECK,
        payload={"force": force, "result": result}, note=note,
    )
    return refund, note


# ---------------------------------------------------------------- 支付模拟器

class PaymentGateway:
    """支付网关模拟器。

    同步退款接口可能返回 SUCCESS / FAILURE / UNKNOWN。
    UNKNOWN 表示网关未给出确定结果（网络超时等），此时必须保持占用、
    等待异步回调或主动核实，绝不能当作失败释放额度。
    """

    RESULT_SUCCESS = "SUCCESS"
    RESULT_FAILURE = "FAILURE"
    RESULT_UNKNOWN = "UNKNOWN"

    @classmethod
    def refund(cls, refund, simulate="random"):
        txn = "TXN" + uuid.uuid4().hex[:12].upper()
        if simulate in (cls.RESULT_SUCCESS.lower(), "success"):
            return cls.RESULT_SUCCESS, txn
        if simulate in (cls.RESULT_FAILURE.lower(), "failure"):
            return cls.RESULT_FAILURE, txn
        if simulate in (cls.RESULT_UNKNOWN.lower(), "unknown"):
            return cls.RESULT_UNKNOWN, txn
        # random：以退款单号哈希做确定性伪随机，便于演示复现
        bucket = int(refund.refund_no[1:], 16) % 10
        if bucket < 6:
            return cls.RESULT_SUCCESS, txn
        if bucket < 8:
            return cls.RESULT_FAILURE, txn
        return cls.RESULT_UNKNOWN, txn

    @classmethod
    def query(cls, refund, force="random"):
        if force in ("success", cls.RESULT_SUCCESS.lower()):
            return cls.RESULT_SUCCESS
        if force in ("failure", cls.RESULT_FAILURE.lower()):
            return cls.RESULT_FAILURE
        if force in ("unknown", cls.RESULT_UNKNOWN.lower()):
            return cls.RESULT_UNKNOWN
        bucket = int(refund.refund_no[1:], 16) % 10
        return cls.RESULT_SUCCESS if bucket < 7 else cls.RESULT_FAILURE
