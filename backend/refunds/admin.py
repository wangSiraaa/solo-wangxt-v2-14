from django.contrib import admin

from .models import Order, OrderLine, Refund, RefundEvent, RefundLine

admin.site.register([Order, OrderLine, Refund, RefundLine, RefundEvent])
