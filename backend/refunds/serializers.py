from rest_framework import serializers

from .models import Order, OrderLine, Refund, RefundEvent, RefundLine


class OrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLine
        fields = [
            "id", "line_no", "sku", "title", "list_price", "is_gift", "gift_value",
            "allocated_discount", "allocated_paid", "refunded_amount", "status",
        ]


class RefundLineSerializer(serializers.ModelSerializer):
    line_no = serializers.IntegerField(source="order_line.line_no", read_only=True)
    title = serializers.CharField(source="order_line.title", read_only=True)

    class Meta:
        model = RefundLine
        fields = ["id", "line_no", "title", "amount"]


class RefundEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RefundEvent
        fields = ["id", "kind", "callback_id", "payload", "note", "created_at"]


class RefundSerializer(serializers.ModelSerializer):
    lines = RefundLineSerializer(many=True, read_only=True)
    events = RefundEventSerializer(many=True, read_only=True)
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = [
            "id", "refund_no", "status", "amount", "shipping_refund",
            "gift_deduction", "is_final", "reason", "gateway_txn",
            "calc_trace", "total_amount", "lines", "events",
            "created_at", "updated_at",
        ]

    def get_total_amount(self, obj):
        return obj.amount + obj.shipping_refund


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    succeeded_total = serializers.SerializerMethodField()
    held_total = serializers.SerializerMethodField()
    refundable_remaining = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "order_no", "title", "shipping_fee", "discount_total",
            "paid_total", "created_at", "lines", "refunds",
            "succeeded_total", "held_total", "refundable_remaining",
        ]

    def get_succeeded_total(self, obj):
        return sum(r.amount for r in obj.refunds.all() if r.status == Refund.STATUS_SUCCESS)

    def get_held_total(self, obj):
        return sum(
            r.amount
            for r in obj.refunds.all()
            if r.status in (Refund.STATUS_PENDING, Refund.STATUS_PENDING_VERIFICATION)
        )

    def get_refundable_remaining(self, obj):
        return obj.paid_total - self.get_succeeded_total(obj) - self.get_held_total(obj)


class RefundCreateSerializer(serializers.Serializer):
    line_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(max_length=64)
    simulate = serializers.ChoiceField(
        choices=["random", "success", "failure", "unknown"], default="random"
    )


class CallbackSerializer(serializers.Serializer):
    callback_id = serializers.CharField(max_length=64)
    result = serializers.ChoiceField(choices=["SUCCESS", "FAILURE", "UNKNOWN"])


class RecheckSerializer(serializers.Serializer):
    force = serializers.ChoiceField(
        choices=["random", "success", "failure", "unknown"], default="random"
    )
