from django.contrib import admin

from .models import Order, OrderItem, PaymentEvent, Refund, RefundItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_no", "customer_name", "bundle_name", "paid_cents", "created_at"]
    inlines = [OrderItemInline]


class RefundItemInline(admin.TabularInline):
    model = RefundItem
    extra = 0


class PaymentEventInline(admin.TabularInline):
    model = PaymentEvent
    extra = 0


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ["refund_no", "order", "amount_cents", "status", "created_at"]
    list_filter = ["status"]
    inlines = [RefundItemInline, PaymentEventInline]
