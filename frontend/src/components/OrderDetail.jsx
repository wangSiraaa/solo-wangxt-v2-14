import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Money } from "../money";
import AllocationTable from "./AllocationTable";
import RefundPanel from "./RefundPanel";
import RefundHistory from "./RefundHistory";

export default function OrderDetail({ orderId, notify, onChanged }) {
  const [order, setOrder] = useState(null);
  const [refunds, setRefunds] = useState([]);
  const [selection, setSelection] = useState({}); // {orderItemId: qty}
  const [quote, setQuote] = useState(null);
  const [quoteError, setQuoteError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // 幂等键：一次退货操作期间保持不变，网络重试/重复点击不会产生重复退款单
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());

  const reload = useCallback(async () => {
    const [{ data: o }, { data: l }] = await Promise.all([
      api.getOrder(orderId),
      api.getLedger(orderId),
    ]);
    setOrder(o);
    setRefunds(l.refunds);
  }, [orderId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const hasSelection = useMemo(
    () => Object.values(selection).some((q) => q > 0),
    [selection]
  );

  // 选择变化 → 实时试算
  useEffect(() => {
    if (!hasSelection) {
      setQuote(null);
      setQuoteError(null);
      return;
    }
    let cancelled = false;
    api
      .quoteRefund(orderId, selection)
      .then(({ data }) => {
        if (!cancelled) {
          setQuote(data);
          setQuoteError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setQuote(null);
          setQuoteError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selection, hasSelection, orderId]);

  const changeQty = (itemId, qty) => {
    setSelection((s) => ({ ...s, [itemId]: qty }));
  };

  const submit = async () => {
    setSubmitting(true);
    try {
      const { data, status } = await api.createRefund(orderId, selection, idemKey);
      notify(
        status === 201
          ? `退款单 ${data.refund_no} 已创建（待核实），额度已占用`
          : `幂等命中：${data.refund_no} 已存在，未重复创建`,
        "success"
      );
      setSelection({});
      setIdemKey(crypto.randomUUID());
      await reload();
      onChanged();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setSubmitting(false);
    }
  };

  const afterRefundChanged = async (msg, kind = "success") => {
    if (msg) notify(msg, kind);
    await reload();
    onChanged();
  };

  if (!order) return <div className="empty">加载中…</div>;

  return (
    <div className="order-detail">
      <section className="panel">
        <div className="panel-head">
          <div>
            <span className="mono order-no">{order.order_no}</span>
            <h2>
              {order.bundle_name}
              <span className="customer">顾客：{order.customer_name}</span>
            </h2>
          </div>
          <div className="order-time">下单时间 {order.created_at}</div>
        </div>
        <div className="summary-cards">
          <Card label="原价合计" cents={order.total_list_cents} />
          <Card label="套装优惠" cents={-order.discount_cents} />
          <Card label="运费" cents={order.shipping_cents} />
          <Card label="实付金额" cents={order.paid_cents} strong />
          <Card label="已退（成功）" cents={order.refunded_cents} cls="ok" />
          <Card label="待核实占用" cents={order.pending_cents} cls="warn" />
          <Card label="可退余额" cents={order.avail_cents} cls="primary" strong />
        </div>
        <div className="rule-note">
          分摊规则：{order.snapshot?.allocation_rule}
        </div>
      </section>

      <section className="panel">
        <h3>商品分摊明细（财务视角）</h3>
        <AllocationTable
          items={order.items}
          selection={selection}
          onChangeQty={changeQty}
        />
      </section>

      <RefundPanel
        hasSelection={hasSelection}
        quote={quote}
        quoteError={quoteError}
        submitting={submitting}
        onSubmit={submit}
      />

      <section className="panel">
        <h3>退款记录（逐笔可追溯）</h3>
        <RefundHistory refunds={refunds} onChanged={afterRefundChanged} notify={notify} />
      </section>
    </div>
  );
}

function Card({ label, cents, strong, cls = "" }) {
  return (
    <div className={`card ${cls}`}>
      <div className="card-label">{label}</div>
      <Money cents={cents} strong={strong} />
    </div>
  );
}
