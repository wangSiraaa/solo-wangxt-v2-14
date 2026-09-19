from django.test import TestCase

from .allocation import allocate_cents
from .models import PaymentEvent, Refund
from .services import (RefundError, compute_quote, create_order_snapshot,
                       create_refund, handle_callback, order_occupied_total,
                       simulate_payment)


def make_order(**kw):
    defaults = dict(
        order_no="T001", customer_name="测试", bundle_name="测试套装",
        lines=[
            {"sku": "A", "title": "商品A", "unit_price_cents": 9999, "qty": 3},
            {"sku": "G", "title": "赠品", "unit_price_cents": 0, "qty": 1, "is_gift": True},
        ],
        discount_cents=2897, shipping_cents=0,
    )
    defaults.update(kw)
    return create_order_snapshot(**defaults)


class AllocateTests(TestCase):
    def test_sum_and_stable_remainder(self):
        # 27100 按权重 [29997] 单行；多行场景验证尾差稳定分配
        result = allocate_cents(100, [1, 1, 1])
        self.assertEqual(sum(result), 100)
        self.assertEqual(result, [34, 33, 33])  # 尾差给行号小者（余数相同）

    def test_zero_total_and_zero_weight(self):
        self.assertEqual(allocate_cents(0, [5, 5]), [0, 0])
        self.assertEqual(allocate_cents(7, [0, 0, 0]), [3, 2, 2])
        self.assertEqual(allocate_cents(10, [0, 4, 6]), [0, 4, 6])

    def test_order_allocation_totals(self):
        order = make_order()
        self.assertEqual(order.paid_cents, 29997 - 2897)
        item = order.items.get(sku="A")
        self.assertEqual(item.paid_alloc_cents, 27100)
        gift = order.items.get(sku="G")
        self.assertEqual(gift.paid_alloc_cents, 0)


class RefundFlowTests(TestCase):
    def setUp(self):
        self.order = make_order()
        self.item = self.order.items.get(sku="A")
        self.gift = self.order.items.get(sku="G")

    def test_partial_refunds_and_last_unit_settles_remainder(self):
        # 27100 // 3 = 9033，前两次各 9033，最后一次结清 9034
        r1, _ = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="k1")
        self.assertEqual(r1.amount_cents, 9033)
        simulate_payment(refund_id=r1.id, outcome="success")

        r2, _ = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="k2")
        self.assertEqual(r2.amount_cents, 9033)
        simulate_payment(refund_id=r2.id, outcome="success")

        r3, _ = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="k3")
        self.assertEqual(r3.amount_cents, 9034)  # 结清尾差
        simulate_payment(refund_id=r3.id, outcome="success")

        total = sum(Refund.objects.filter(order=self.order, status="SUCCESS")
                    .values_list("amount_cents", flat=True))
        self.assertEqual(total, self.order.paid_cents)  # 累计恰等于实付

        # 已全部退完，再退被拒
        with self.assertRaises(RefundError):
            create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="k4")

    def test_gift_refunds_zero(self):
        refund, _ = create_refund(order_id=self.order.id,
                                  selections={self.gift.id: 1}, idempotency_key="g1")
        self.assertEqual(refund.amount_cents, 0)

    def test_pending_occupies_quota_and_unknown_keeps_pending(self):
        # 退 3 件（全部），支付未知 -> 保持 PENDING，额度被占，不能再退
        r, _ = create_refund(order_id=self.order.id, selections={self.item.id: 3}, idempotency_key="u1")
        simulate_payment(refund_id=r.id, outcome="unknown")
        r.refresh_from_db()
        self.assertEqual(r.status, "PENDING")
        self.assertEqual(order_occupied_total(self.order), 27100)
        with self.assertRaises(RefundError):
            create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="u2")

    def test_failed_payment_releases_quota(self):
        r, _ = create_refund(order_id=self.order.id, selections={self.item.id: 3}, idempotency_key="f1")
        simulate_payment(refund_id=r.id, outcome="failed")
        r.refresh_from_db()
        self.assertEqual(r.status, "FAILED")
        self.assertEqual(order_occupied_total(self.order), 0)
        # 释放后可重新退款
        r2, _ = create_refund(order_id=self.order.id, selections={self.item.id: 3}, idempotency_key="f2")
        self.assertEqual(r2.amount_cents, 27100)

    def test_idempotent_create(self):
        r1, created1 = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="same")
        r2, created2 = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="same")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(Refund.objects.count(), 1)

    def test_duplicate_callbacks(self):
        r, _ = create_refund(order_id=self.order.id, selections={self.item.id: 1}, idempotency_key="c1")
        simulate_payment(refund_id=r.id, outcome="success")
        r.refresh_from_db()
        txn = r.transaction_id
        evt = r.events.order_by("-id").first().event_id

        # 同一 event_id 重放
        _, result, http = handle_callback(event_id=evt, transaction_id=txn, status="SUCCESS")
        self.assertEqual(result, PaymentEvent.RESULT_DUPLICATE)
        self.assertEqual(http, 200)
        # 不同 event_id 同结果
        _, result, _ = handle_callback(event_id="evt-new-1", transaction_id=txn, status="SUCCESS")
        self.assertEqual(result, PaymentEvent.RESULT_DUPLICATE)
        # 冲突结果
        _, result, http = handle_callback(event_id="evt-new-2", transaction_id=txn, status="FAILED")
        self.assertEqual(result, PaymentEvent.RESULT_CONFLICT)
        self.assertEqual(http, 409)
        r.refresh_from_db()
        self.assertEqual(r.status, "SUCCESS")  # 冲突不改变终态

    def test_quote_does_not_persist(self):
        _, total, trace = compute_quote(order=self.order, selections={self.item.id: 2})
        self.assertEqual(total, 9033 * 2)
        self.assertEqual(Refund.objects.count(), 0)
        self.assertIn("逐行求和", trace["order_level"]["formula"])
