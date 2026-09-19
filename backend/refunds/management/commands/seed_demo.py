"""构造演示数据：

订单 A（含赠品 + 支付结果未知）：护肤套装，已退 1 件洁面（成功），
        精华退款被模拟器返回「未知」-> 保持待核实、额度占用中。
订单 B（跨次数退件 + 分币尾差）：3 件 T 恤分两次各退 1 件（成功），
        剩最后 1 件留给用户操作，体验「最后一次退货结清可退尾差」。
订单 C（重复回调）：耳机套装退保护套成功，网关回调被重复投递
        （同一 event_id 重放 + 不同 event_id 重复通知），均被幂等处理。
订单 D（全新未退）：咖啡礼盒，含赠品，留给用户完整走一遍流程。
"""
import uuid

from django.core.management.base import BaseCommand

from refunds.models import Order, PaymentEvent, Refund
from refunds.services import (create_order_snapshot, create_refund,
                              handle_callback, simulate_payment)


class Command(BaseCommand):
    help = "清空并重建演示订单数据"

    def handle(self, *args, **options):
        Refund.objects.all().delete()
        Order.objects.all().delete()

        self._order_a()
        self._order_b()
        self._order_c()
        self._order_d()
        self.stdout.write(self.style.SUCCESS("演示数据已就绪：订单 A/B/C/D"))

    # ---------------------------------------------------------------- A
    def _order_a(self):
        """含赠品；一笔成功退款 + 一笔支付结果未知（待核实）。"""
        order = create_order_snapshot(
            order_no="SO20260901001",
            customer_name="林小满",
            bundle_name="焕采护肤三件套",
            lines=[
                {"sku": "CLN-01", "title": "氨基酸洁面乳", "unit_price_cents": 12000, "qty": 2},
                {"sku": "SER-01", "title": "玻尿酸精华液", "unit_price_cents": 39900, "qty": 1},
                {"sku": "GFT-BAG", "title": "定制化妆包（赠品）", "unit_price_cents": 0, "qty": 1, "is_gift": True},
            ],
            discount_cents=13900,   # 套装立减 ¥139.00
            shipping_cents=1000,    # 运费 ¥10.00
        )  # 实付 63900 - 13900 + 1000 = 51000
        items = {it.sku: it for it in order.items.all()}

        # 第一次：退 1 件洁面乳，支付成功
        r1, _ = create_refund(order_id=order.id,
                              selections={items["CLN-01"].id: 1},
                              idempotency_key="seed-a-r1")
        simulate_payment(refund_id=r1.id, outcome="success")

        # 第二次：退精华液，模拟器返回未知 -> 保持待核实，额度占用
        r2, _ = create_refund(order_id=order.id,
                              selections={items["SER-01"].id: 1},
                              idempotency_key="seed-a-r2")
        simulate_payment(refund_id=r2.id, outcome="unknown")

    # ---------------------------------------------------------------- B
    def _order_b(self):
        """同款 3 件跨次数退件：实付分摊 27100 分不能整除 3，尾差 1 分随最后一件结清。"""
        order = create_order_snapshot(
            order_no="SO20260905002",
            customer_name="陈默",
            bundle_name="基础款纯棉T恤（3件装）",
            lines=[
                {"sku": "TEE-01", "title": "基础款纯棉T恤", "unit_price_cents": 9999, "qty": 3},
            ],
            discount_cents=2897,   # ¥28.97
            shipping_cents=0,
        )  # 实付 29997 - 2897 = 27100；单件 9033，尾差 1 分
        item = order.items.get(sku="TEE-01")

        r1, _ = create_refund(order_id=order.id, selections={item.id: 1},
                              idempotency_key="seed-b-r1")
        simulate_payment(refund_id=r1.id, outcome="success")
        r2, _ = create_refund(order_id=order.id, selections={item.id: 1},
                              idempotency_key="seed-b-r2")
        simulate_payment(refund_id=r2.id, outcome="success")
        # 剩最后 1 件（应退 90.34，含 1 分尾差），留给用户操作

    # ---------------------------------------------------------------- C
    def _order_c(self):
        """重复回调：同一 event_id 重放 + 不同 event_id 重复通知，均幂等。"""
        order = create_order_snapshot(
            order_no="SO20260908003",
            customer_name="赵一鸣",
            bundle_name="降噪耳机出行套装",
            lines=[
                {"sku": "EAR-01", "title": "主动降噪耳机", "unit_price_cents": 89900, "qty": 1},
                {"sku": "CASE-01", "title": "硅胶保护套", "unit_price_cents": 9900, "qty": 1},
                {"sku": "GFT-TIP", "title": "替换耳帽（赠品）", "unit_price_cents": 0, "qty": 1, "is_gift": True},
            ],
            discount_cents=9800,   # ¥98.00
            shipping_cents=0,
        )  # 实付 99800 - 9800 = 90000
        case = order.items.get(sku="CASE-01")

        refund, _ = create_refund(order_id=order.id, selections={case.id: 1},
                                  idempotency_key="seed-c-r1")
        simulate_payment(refund_id=refund.id, outcome="success")
        refund.refresh_from_db()
        txn = refund.transaction_id
        last_event = refund.events.order_by("-id").first()

        # 1) 同一 event_id 重复投递 -> duplicate
        handle_callback(event_id=last_event.event_id, transaction_id=txn, status="SUCCESS")
        # 2) 不同 event_id 的重复通知 -> duplicate
        handle_callback(event_id=f"evt-{uuid.uuid4().hex[:16]}", transaction_id=txn, status="SUCCESS")
        # 3) 冲突通知（网关误报 FAILED）-> conflict，保持 SUCCESS 不变
        handle_callback(event_id=f"evt-{uuid.uuid4().hex[:16]}", transaction_id=txn, status="FAILED",
                        payload={"reason": "网关误报"})

    # ---------------------------------------------------------------- D
    def _order_d(self):
        """全新未退订单，含赠品，供用户完整体验。"""
        create_order_snapshot(
            order_no="SO20260912004",
            customer_name="孙可",
            bundle_name="手冲咖啡礼盒",
            lines=[
                {"sku": "BEAN-01", "title": "耶加雪菲咖啡豆 200g", "unit_price_cents": 15800, "qty": 2},
                {"sku": "POT-01", "title": "鹤嘴手冲壶", "unit_price_cents": 22900, "qty": 1},
                {"sku": "GFT-FLT", "title": "V60 滤纸（赠品）", "unit_price_cents": 0, "qty": 1, "is_gift": True},
            ],
            discount_cents=4500,   # ¥45.00
            shipping_cents=1200,   # ¥12.00
        )  # 实付 54500 - 4500 + 1200 = 51200
