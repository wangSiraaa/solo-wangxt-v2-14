from rest_framework import serializers

from .models import Order, OrderItem, PaymentEvent, Refund, RefundItem
from .services import occupied_by_item, order_occupied_total


class OrderItemSerializer(serializers.ModelSerializer):
    occupied_qty = serializers.SerializerMethodField()
    occupied_amount_cents = serializers.SerializerMethodField()
    avail_qty = serializers.SerializerMethodField()
    avail_amount_cents = serializers.SerializerMethodField()
    unit_cents = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id", "line_no", "sku", "title", "unit_price_cents", "qty", "is_gift",
            "list_total_cents", "weight",
            "discount_alloc_cents", "shipping_alloc_cents", "paid_alloc_cents",
            "occupied_qty", "occupied_amount_cents", "avail_qty",
            "avail_amount_cents", "unit_cents",
        ]

    def _occupied(self):
        return self.context.setdefault("occupied", occupied_by_item(self.context["order"]))

    def _occ(self, obj):
        return self._occupied().get(obj.id, (0, 0))

    def get_occupied_qty(self, obj):
        return self._occ(obj)[0]

    def get_occupied_amount_cents(self, obj):
        return self._occ(obj)[1]

    def get_avail_qty(self, obj):
        return obj.qty - self._occ(obj)[0]

    def get_avail_amount_cents(self, obj):
        return obj.paid_alloc_cents - self._occ(obj)[1]

    def get_unit_cents(self, obj):
        return obj.paid_alloc_cents // obj.qty if obj.qty else 0


class OrderListSerializer(serializers.ModelSerializer):
    refunded_cents = serializers.SerializerMethodField()
    pending_cents = serializers.SerializerMethodField()
    avail_cents = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "order_no", "customer_name", "bundle_name",
            "total_list_cents", "discount_cents", "shipping_cents", "paid_cents",
            "refunded_cents", "pending_cents", "avail_cents",
            "item_count", "created_at",
        ]

    def _sums(self, obj):
        cache = self.context.setdefault("sums", {})
        if obj.id not in cache:
            from django.db.models import Sum
            from .models import Refund
            rows = {
                r["status"]: r["t"] or 0
                for r in Refund.objects.filter(order=obj)
                .values("status").annotate(t=Sum("amount_cents"))
            }
            cache[obj.id] = rows
        return cache[obj.id]

    def get_refunded_cents(self, obj):
        return self._sums(obj).get(Refund.STATUS_SUCCESS, 0)

    def get_pending_cents(self, obj):
        return self._sums(obj).get(Refund.STATUS_PENDING, 0)

    def get_avail_cents(self, obj):
        s = self._sums(obj)
        return obj.paid_cents - s.get(Refund.STATUS_SUCCESS, 0) - s.get(Refund.STATUS_PENDING, 0)

    def get_item_count(self, obj):
        return obj.items.count()


class OrderDetailSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    refunded_cents = serializers.SerializerMethodField()
    pending_cents = serializers.SerializerMethodField()
    avail_cents = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "order_no", "customer_name", "bundle_name",
            "total_list_cents", "discount_cents", "shipping_cents", "paid_cents",
            "refunded_cents", "pending_cents", "avail_cents",
            "snapshot", "items", "created_at",
        ]

    def get_items(self, obj):
        ctx = {"order": obj, "occupied": occupied_by_item(obj)}
        return OrderItemSerializer(obj.items.all(), many=True, context=ctx).data

    def get_refunded_cents(self, obj):
        from django.db.models import Sum
        return (Refund.objects.filter(order=obj, status=Refund.STATUS_SUCCESS)
                .aggregate(t=Sum("amount_cents"))["t"] or 0)

    def get_pending_cents(self, obj):
        from django.db.models import Sum
        return (Refund.objects.filter(order=obj, status=Refund.STATUS_PENDING)
                .aggregate(t=Sum("amount_cents"))["t"] or 0)

    def get_avail_cents(self, obj):
        return obj.paid_cents - order_occupied_total(obj)


class RefundItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="order_item.sku")
    title = serializers.CharField(source="order_item.title")
    line_no = serializers.IntegerField(source="order_item.line_no")
    is_gift = serializers.BooleanField(source="order_item.is_gift")

    class Meta:
        model = RefundItem
        fields = ["id", "order_item_id", "line_no", "sku", "title", "is_gift",
                  "qty", "amount_cents", "calc_detail"]


class PaymentEventSerializer(serializers.ModelSerializer):
    result_display = serializers.CharField(source="get_result_display")

    class Meta:
        model = PaymentEvent
        fields = ["id", "event_id", "transaction_id", "gateway_status",
                  "result", "result_display", "payload", "created_at"]


class RefundSerializer(serializers.ModelSerializer):
    items = RefundItemSerializer(many=True, read_only=True)
    events = PaymentEventSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display")
    order_no = serializers.CharField(source="order.order_no")

    class Meta:
        model = Refund
        fields = [
            "id", "refund_no", "order_id", "order_no", "idempotency_key",
            "amount_cents", "status", "status_display", "transaction_id",
            "failure_reason", "trace", "items", "events",
            "created_at", "finished_at",
        ]
