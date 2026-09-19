import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Money } from "./money";
import OrderDetail from "./components/OrderDetail";

export default function App() {
  const [orders, setOrders] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [toast, setToast] = useState(null);

  const notify = useCallback((text, kind = "info") => {
    setToast({ text, kind, ts: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const reloadOrders = useCallback(async () => {
    const { data } = await api.listOrders();
    setOrders(data);
    return data;
  }, []);

  useEffect(() => {
    reloadOrders().then((list) => {
      if (list.length && !selectedId) setSelectedId(list[list.length - 1].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>套装拆退核算工作台</h1>
        <span className="subtitle">
          按下单权重分摊优惠/运费 · 分币尾差稳定分配 · 退款占用 · 支付回调幂等
        </span>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <div className="sidebar-title">订单列表</div>
          {orders.map((o) => (
            <button
              key={o.id}
              className={`order-card ${o.id === selectedId ? "active" : ""}`}
              onClick={() => setSelectedId(o.id)}
            >
              <div className="order-card-head">
                <span className="mono">{o.order_no}</span>
                <Money cents={o.paid_cents} strong />
              </div>
              <div className="order-card-title">
                {o.bundle_name} · {o.customer_name}
              </div>
              <div className="order-card-stats">
                <span className="stat-ok">已退 <Money cents={o.refunded_cents} /></span>
                <span className="stat-warn">待核实 <Money cents={o.pending_cents} /></span>
                <span>可退 <Money cents={o.avail_cents} /></span>
              </div>
            </button>
          ))}
        </aside>
        <main className="main">
          {selectedId ? (
            <OrderDetail
              key={selectedId}
              orderId={selectedId}
              notify={notify}
              onChanged={reloadOrders}
            />
          ) : (
            <div className="empty">请选择左侧订单</div>
          )}
        </main>
      </div>
      {toast && <div className={`toast toast-${toast.kind}`}>{toast.text}</div>}
    </div>
  );
}
