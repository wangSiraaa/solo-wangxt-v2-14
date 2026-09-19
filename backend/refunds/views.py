from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Order, Refund
from .serializers import (
    CallbackSerializer,
    OrderSerializer,
    RecheckSerializer,
    RefundCreateSerializer,
    RefundSerializer,
)
from .services import RefundError, create_refund, handle_gateway_callback, recheck_refund


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Order.objects.prefetch_related("lines", "refunds__lines__order_line", "refunds__events")
    serializer_class = OrderSerializer

    @action(detail=True, methods=["post"], url_path="refunds")
    def create_refund(self, request, pk=None):
        """客服提交拆退申请。幂等键重复时返回原退款单。"""
        s = RefundCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            refund, reused = create_refund(
                order_id=pk,
                line_ids=s.validated_data["line_ids"],
                reason=s.validated_data["reason"],
                idempotency_key=s.validated_data["idempotency_key"],
                simulate=s.validated_data["simulate"],
            )
        except RefundError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        out = RefundSerializer(refund)
        return Response(
            {"refund": out.data, "idempotent_replay": reused},
            status=status.HTTP_200_OK if reused else status.HTTP_201_CREATED,
        )


class RefundViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Refund.objects.prefetch_related("lines__order_line", "events")
    serializer_class = RefundSerializer

    @action(detail=True, methods=["post"], url_path="callback")
    def callback(self, request, pk=None):
        """模拟支付网关异步回调。相同 callback_id 重复推送只会处理一次。"""
        s = CallbackSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        refund, note = handle_gateway_callback(
            pk, s.validated_data["callback_id"], s.validated_data["result"]
        )
        return Response({"refund": RefundSerializer(refund).data, "note": note})

    @action(detail=True, methods=["post"], url_path="recheck")
    def recheck(self, request, pk=None):
        """财务对「待核实」退款发起主动核实。"""
        s = RecheckSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        refund, note = recheck_refund(pk, force=s.validated_data["force"])
        return Response({"refund": RefundSerializer(refund).data, "note": note})
