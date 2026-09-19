import uuid

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from .models import Order, PaymentEvent, Refund
from .serializers import (OrderDetailSerializer, OrderListSerializer,
                          RefundSerializer)
from .services import (RefundError, create_refund, handle_callback,
                       simulate_payment)


def _error(message, http=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": message}, status=http)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Order.objects.prefetch_related("items").all()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return OrderDetailSerializer
        return OrderListSerializer

    @action(detail=True, methods=["post"], url_path="refunds/quote")
    def quote_refund(self, request, pk=None):
        """退款试算：不落库，返回逐行计算依据。"""
        from .services import compute_quote
        order = self.get_object()
        try:
            _, total, trace = compute_quote(order=order,
                                            selections=request.data.get("lines", {}))
        except RefundError as exc:
            return _error(str(exc))
        return Response({"total_cents": total, "trace": trace})

    @action(detail=True, methods=["post"], url_path="refunds")
    def create_refund(self, request, pk=None):
        """创建退款单（PENDING，占用额度）。幂等：重复提交返回原单。"""
        idem = request.headers.get("Idempotency-Key") or request.data.get("idempotency_key")
        order = self.get_object()
        try:
            refund, created = create_refund(
                order_id=order.id,
                selections=request.data.get("lines", {}),
                idempotency_key=idem,
            )
        except RefundError as exc:
            return _error(str(exc))
        return Response(RefundSerializer(refund).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="ledger")
    def ledger(self, request, pk=None):
        """订单台账：全部分退款单 + 支付事件，供逐笔追溯。"""
        order = self.get_object()
        refunds = (Refund.objects.filter(order=order)
                   .prefetch_related("items__order_item", "events"))
        return Response({
            "order_no": order.order_no,
            "paid_cents": order.paid_cents,
            "refunds": RefundSerializer(refunds, many=True).data,
        })


class RefundViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RefundSerializer

    def get_queryset(self):
        return (Refund.objects.select_related("order")
                .prefetch_related("items__order_item", "events").all())

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        """支付模拟器：对 PENDING 退款模拟网关处理。

        body: {"outcome": "success" | "failed" | "unknown"}
        """
        outcome = request.data.get("outcome")
        if outcome not in ("success", "failed", "unknown"):
            return _error("outcome 必须是 success / failed / unknown")
        try:
            refund, note = simulate_payment(refund_id=pk, outcome=outcome)
        except RefundError as exc:
            return _error(str(exc))
        return Response({"note": note, "refund": RefundSerializer(refund).data})


@api_view(["POST"])
def payment_callback(request):
    """支付网关回调入口（幂等）。

    body: {transaction_id, status: SUCCESS|FAILED|UNKNOWN, event_id?, replay_last?}
      - event_id 缺省时自动生成（视为新一次通知）；
      - replay_last=true 时复用该流水最近一次事件的 event_id，模拟「同一回调重复投递」。
    """
    txn = request.data.get("transaction_id")
    gw_status = (request.data.get("status") or "").upper()
    if not txn or gw_status not in ("SUCCESS", "FAILED", "UNKNOWN"):
        return _error("需要 transaction_id 与 status(SUCCESS/FAILED/UNKNOWN)")

    event_id = request.data.get("event_id")
    if request.data.get("replay_last"):
        last = (PaymentEvent.objects.filter(transaction_id=txn)
                .order_by("-id").first())
        if not last:
            return _error("该流水尚无可重放的回调事件", status.HTTP_404_NOT_FOUND)
        event_id = last.event_id
    if not event_id:
        event_id = f"evt-{uuid.uuid4().hex[:16]}"

    refund, result, http = handle_callback(
        event_id=event_id, transaction_id=txn, status=gw_status,
        payload=request.data.get("payload") or {},
    )
    if refund is None:
        return _error(f"未知支付流水号 {txn}", status.HTTP_404_NOT_FOUND)
    return Response(
        {"result": result, "event_id": event_id,
         "refund": RefundSerializer(refund).data},
        status=http,
    )
