from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, RefundViewSet, payment_callback

router = DefaultRouter()
router.register("orders", OrderViewSet, basename="order")
router.register("refunds", RefundViewSet, basename="refund")

urlpatterns = [
    path("", include(router.urls)),
    path("payments/callback/", payment_callback, name="payment-callback"),
]
