from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, RefundViewSet

router = DefaultRouter()
router.register("orders", OrderViewSet, basename="order")
router.register("refunds", RefundViewSet, basename="refund")

urlpatterns = router.urls
