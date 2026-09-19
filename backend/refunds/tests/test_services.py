from django.test import TestCase

from refunds.models import OrderLine, Refund
from refunds.services import (
    RefundError,
    allocate_discount,
    create_order_snapshot,
    create_refund,
    handle_gateway_callback,
    recheck_refund,
)


def make_order(**kw):
    defaults = dict(
        order_no="T-001",
        title="测试套装",
        lines=[
            {"sku": "A", "title": "商品A", "list_price": 39900},
            {"sku": "B", "title": "商品B", "list_price": 29900},
            {"sku": "C", "title": "商品C", "list_price": 19900},
            {"sku": "G", "title": "赠品", "list_price": 0, "is_gift": True, "gift_value": 2900},
        ],
        discount_total=10000,
        shipping_fee=1200,
    )
    defaults.update(kw)
    return create_order_snapshot(**defaults)


class AllocateTest(TestCase):
    def test_sum_equals_total_and_stable(self):
        # 100 元按 399/299/199 分摊：4448/3333/2219，尾差 1 分给余数最大的 C
        got = allocate_discount([39900, 29900, 19900], 10000)
        self.assertEqual(got, [4448, 3333, 2219])
        self.assertEqual(sum(got), 10000)

    def test_tie_break_by_index(self):
        # 余数相同 -> 行号小者优先（稳定）
        got = allocate_discount([100, 100, 100], 2)
        self.assertEqual(got, [1, 1, 0])

    def test_zero_weight_gets_nothing(self):
        got = allocate_discount([100, 0], 30)
        self.assertEqual(got, [30, 0])

    def test_snapshot_invariant(self):
        order = make_order()
        lines = list(order.lines.all())
        self.assertEqual(sum(l.allocated_discount for l in lines), 10000)
        self.assertEqual(sum(l.allocated_paid for l in lines), order.paid_total)
        self.assertEqual(lines[3].allocated_paid, 0)  # 赠品


class RefundFlowTest(TestCase):
    def setUp(self):
        self.order = make_order()
        self.lid = {l.sku: l.id for l in self.order.lines.all()}

    def test_partial_refund_amount(self):
        r, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="success")
        self.assertEqual(r.amount, 39900 - 4448)  # 35452
        self.assertEqual(r.shipping_refund, 0)    # 非末次不退运费

    def test_cumulative_cannot_exceed_paid(self):
        create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="success")
        create_refund(self.order.pk, [self.lid["B"]], "", "k2", simulate="success")
        create_refund(self.order.pk, [self.lid["C"]], "", "k3", simulate="success")
        with self.assertRaises(RefundError):
            create_refund(self.order.pk, [self.lid["A"]], "", "k4", simulate="success")

    def test_final_refund_settles_remainder_and_shipping_and_gift(self):
        create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="success")
        create_refund(self.order.pk, [self.lid["B"]], "", "k2", simulate="success")
        # 末次：退 C，赠品未退回 -> 扣 2900，退运费 1200
        r, _ = create_refund(self.order.pk, [self.lid["C"]], "", "k3", simulate="success")
        self.assertTrue(r.is_final)
        self.assertEqual(r.gift_deduction, 2900)
        self.assertEqual(r.shipping_refund, 1200)
        self.assertEqual(r.amount, self.order.paid_total - 35452 - 26567 - 2900)
        succeeded = sum(
            x.amount for x in self.order.refunds.all() if x.status == Refund.STATUS_SUCCESS
        )
        self.assertEqual(succeeded, self.order.paid_total - 2900)

    def test_idempotent_create(self):
        r1, reused1 = create_refund(self.order.pk, [self.lid["A"]], "", "same-key", simulate="success")
        r2, reused2 = create_refund(self.order.pk, [self.lid["A"]], "", "same-key", simulate="success")
        self.assertFalse(reused1)
        self.assertTrue(reused2)
        self.assertEqual(r1.pk, r2.pk)
        self.assertEqual(self.order.refunds.count(), 1)

    def test_unknown_keeps_hold_and_blocks_refund(self):
        r, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="unknown")
        self.assertEqual(r.status, Refund.STATUS_PENDING_VERIFICATION)
        line = OrderLine.objects.get(pk=self.lid["A"])
        self.assertEqual(line.status, OrderLine.STATUS_LOCKED)  # 额度仍占用
        with self.assertRaises(RefundError) as ctx:
            create_refund(self.order.pk, [self.lid["A"]], "", "k2", simulate="success")
        self.assertIn("占用", str(ctx.exception))

    def test_failure_releases_hold(self):
        r, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="failure")
        self.assertEqual(r.status, Refund.STATUS_FAILED)
        line = OrderLine.objects.get(pk=self.lid["A"])
        self.assertEqual(line.status, OrderLine.STATUS_NORMAL)
        r2, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k2", simulate="success")
        self.assertEqual(r2.status, Refund.STATUS_SUCCESS)

    def test_duplicate_callback_idempotent(self):
        r, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="unknown")
        _, note1 = handle_gateway_callback(r.pk, "CB-1", "SUCCESS")
        _, note2 = handle_gateway_callback(r.pk, "CB-1", "SUCCESS")
        self.assertIn("受理", note1)
        self.assertIn("重复回调", note2)
        r.refresh_from_db()
        self.assertEqual(r.status, Refund.STATUS_SUCCESS)
        self.assertEqual(r.events.filter(callback_id="CB-1").count(), 1)

    def test_recheck_resolves_pending(self):
        r, _ = create_refund(self.order.pk, [self.lid["A"]], "", "k1", simulate="unknown")
        r, _ = recheck_refund(r.pk, force="success")
        self.assertEqual(r.status, Refund.STATUS_SUCCESS)
        line = OrderLine.objects.get(pk=self.lid["A"])
        self.assertEqual(line.status, OrderLine.STATUS_RETURNED)

    def test_gift_cannot_be_refunded_alone(self):
        with self.assertRaises(RefundError):
            create_refund(self.order.pk, [self.lid["G"]], "", "kg", simulate="success")
