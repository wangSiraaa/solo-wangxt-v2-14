"""构造演示数据：

  ORD-A  春季焕新套装：3 件商品 + 1 赠品，全新订单，供用户实际提交退款
  ORD-B  夏日出行套装：已发生两次跨日部分退款，剩最后一件 + 赠品，
         用于演示「末次结清尾差 + 退运费 + 赠品未退扣款」
  ORD-C  秋冬保暖套装：一笔退款因网关返回未知结果处于「待核实」，
         且留有重复回调留痕，用于演示额度占用与幂等
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from refunds.models import Order, Refund
from refunds.services import create_order_snapshot, create_refund, handle_gateway_callback


class Command(BaseCommand):
    help = "生成拆退演示订单"

    def handle(self, *args, **options):
        # 幂等：已存在则跳过
        if Order.objects.filter(order_no="ORD-A-20260918").exists():
            self.stdout.write("演示数据已存在，跳过。如需重建请先清空数据库。")
            return

        # ---- ORD-A：全新套装订单 --------------------------------------
        create_order_snapshot(
            order_no="ORD-A-20260918",
            title="春季焕新三件套（含赠品）",
            lines=[
                {"sku": "HOO-01", "title": "连帽卫衣", "list_price": 39900},
                {"sku": "JEA-01", "title": "直筒牛仔裤", "list_price": 29900},
                {"sku": "BAG-01", "title": "帆布托特包", "list_price": 19900},
                {"sku": "GFT-01", "title": "品牌中筒袜（赠品）", "list_price": 0,
                 "is_gift": True, "gift_value": 2900},
            ],
            discount_total=10000,   # 满减 ¥100
            shipping_fee=1200,      # 运费 ¥12
        )

        # ---- ORD-B：两次跨日部分退款 ----------------------------------
        order_b = create_order_snapshot(
            order_no="ORD-B-20260910",
            title="夏日出行套装（含赠品）",
            lines=[
                {"sku": "SUN-01", "title": "防晒衣", "list_price": 25900},
                {"sku": "PAN-01", "title": "速干长裤", "list_price": 18900},
                {"sku": "HAT-01", "title": "渔夫帽", "list_price": 9900},
                {"sku": "GFT-02", "title": "防晒冰袖（赠品）", "list_price": 0,
                 "is_gift": True, "gift_value": 1900},
            ],
            discount_total=8000,
            shipping_fee=1000,
        )
        Order.objects.filter(pk=order_b.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )
        lid = {l.sku: l.id for l in order_b.lines.all()}
        r1, _ = create_refund(order_b.pk, [lid["SUN-01"]], "防晒衣尺码不合",
                              "seed-b-1", simulate="success")
        r2, _ = create_refund(order_b.pk, [lid["PAN-01"]], "裤子起球",
                              "seed-b-2", simulate="success")
        Refund.objects.filter(pk=r1.pk).update(created_at=timezone.now() - timedelta(days=5))
        Refund.objects.filter(pk=r2.pk).update(created_at=timezone.now() - timedelta(days=2))

        # ---- ORD-C：未知结果 -> 待核实 + 重复回调 ----------------------
        order_c = create_order_snapshot(
            order_no="ORD-C-20260915",
            title="秋冬保暖套装（含赠品）",
            lines=[
                {"sku": "DOW-01", "title": "轻薄羽绒服", "list_price": 89900},
                {"sku": "SWE-01", "title": "羊毛毛衣", "list_price": 35900},
                {"sku": "SCA-01", "title": "格纹围巾", "list_price": 12900},
                {"sku": "GFT-03", "title": "暖手宝（赠品）", "list_price": 0,
                 "is_gift": True, "gift_value": 3900},
            ],
            discount_total=20000,
            shipping_fee=1500,
        )
        Order.objects.filter(pk=order_c.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        lid = {l.sku: l.id for l in order_c.lines.all()}
        r3, _ = create_refund(order_c.pk, [lid["SWE-01"]], "毛衣色差",
                              "seed-c-1", simulate="unknown")
        # 网关回调到达：结果仍未知；同一回调重复推送一次 -> 幂等忽略
        handle_gateway_callback(r3.pk, "CB-20260917-001", "UNKNOWN")
        handle_gateway_callback(r3.pk, "CB-20260917-001", "UNKNOWN")  # 重复回调
        Refund.objects.filter(pk=r3.pk).update(created_at=timezone.now() - timedelta(days=1))

        self.stdout.write(self.style.SUCCESS("演示数据已生成：ORD-A / ORD-B / ORD-C"))
