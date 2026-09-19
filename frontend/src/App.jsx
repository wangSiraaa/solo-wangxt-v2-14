import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";

const fmt = (c) => `¥${(c / 100).toFixed(2)}`;
const fmtTime = (s) => new Date(s).toLocaleString("zh-CN", { hour12: false });

const STATUS_META = {
  NORMAL: { text: "正常", cls: "tag-gray" },
  LOCKED: { text: "退款中", cls: "tag-orange" },
  RETURNED: { text: "已退", cls: "tag-blue" },
  PENDING: { text: "待提交", cls: "tag-gray" },
  SUCCESS: { text: "退款成功", cls: "tag-green" },
  FAILED: { text: "退款失败", cls: "tag-red" },
  PENDING_VERIFICATION: { text: "待核实", cls: "tag-orange" },
};

function Tag({ status }) {
  const m = STATUS_META[status] || { text: status, cls: "tag-gray" };
  return <span className={`tag ${m.cls}`}>{m.text}</span>;
}

export default function App() {
  const [orders, setOrders] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [order, setOrder] = useState(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async (id) => {
    const list = await api.listOrders();
    setOrders(list);
    const target = id ?? currentId ?? list[0]?.id;
    if (target) {
      setCurrentId(target);
      setOrder(await api.getOrder(target));
    }
  }, [currentId]);

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = async (id) => {
    setError("");
    setCurrentId(id);
    setOrder(await api.getOrder(id));
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>拆退核算工作台</h1>
        <p className="muted">客服选择退件 · 财务核对分摊</p>
        {orders.map((o) => (
          <button
            key={o.id}
            className={`order-item ${o.id === currentId ? "active" : ""}`}
            onClick={() => select(o.id)}
          >
            <span className="order-no">{o.order_no}</span>
            <span className="order-title">{o.title}</span>
            <span className="muted">
              实付 {fmt(o.paid_total)} · 已退 {fmt(o.succeeded_total)}
              {o.held_total > 0 && ` · 占用 ${fmt(o.held_total)}`}
            </span>
          </button>
        ))}
      </aside>
      <main>
        {error && <div className="banner error">{error}</div>}
        {order ? (
          <OrderDetail
            order={order}
            onChanged={() => refresh(order.id).catch((e) => setError(e.message))}
          />
        ) : (
          <p className="muted">加载中…</p>
        )}
      </main>
    </div>
  );
}

function OrderDetail({ order, onChanged }) {
  const [checked, setChecked] = useState([]);
  const [reason, setReason] = useState("");
  const [simulate, setSimulate] = useState("random");
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const selectable = order.lines.filter((l) => !l.is_gift && l.status === "NORMAL");
  const remainingGoods = order.lines.filter((l) => !l.is_gift && l.status !== "RETURNED");
  const isFinal =
    checked.length > 0 &&
    remainingGoods.every((l) => checked.includes(l.id) || l.status === "LOCKED") &&
    remainingGoods.filter((l) => l.status === "NORMAL").every((l) => checked.includes(l.id));
  const unreturnedGifts = order.lines.filter((l) => l.is_gift && l.status !== "RETURNED");

  // 前端预估（最终金额以后端核算为准）
  const preview = useMemo(() => {
    if (checked.length === 0) return null;
    const selected = order.lines.filter((l) => checked.includes(l.id));
    if (isFinal) {
      const giftDed = unreturnedGifts.reduce((s, g) => s + g.gift_value, 0);
      const goods = Math.max(order.refundable_remaining - giftDed, 0);
      return {
        final: true,
        goods,
        shipping: order.shipping_fee,
        giftDed,
        total: goods + order.shipping_fee,
      };
    }
    const goods = selected.reduce((s, l) => s + l.allocated_paid, 0);
    return { final: false, goods, shipping: 0, giftDed: 0, total: goods };
  }, [checked, isFinal, order, unreturnedGifts]);

  const toggle = (id) =>
    setChecked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  const submit = async () => {
    setErr("");
    setMsg("");
    try {
      const res = await api.createRefund(order.id, {
        line_ids: checked,
        reason,
        idempotency_key: idemKey,
        simulate,
      });
      setMsg(
        res.idempotent_replay
          ? `幂等重放：返回原退款单 ${res.refund.refund_no}`
          : `退款单 ${res.refund.refund_no} 已创建，状态：${STATUS_META[res.refund.status].text}`
      );
      setChecked([]);
      setReason("");
      setIdemKey(crypto.randomUUID());
      onChanged();
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div>
      <header className="order-header">
        <div>
          <h2>{order.title}</h2>
          <p className="muted">
            {order.order_no} · 下单于 {fmtTime(order.created_at)}
          </p>
        </div>
      </header>

      <section className="cards">
        <Card label="商品实付" value={fmt(order.paid_total)} />
        <Card label="订单优惠" value={fmt(order.discount_total)} />
        <Card label="运费" value={fmt(order.shipping_fee)} hint="末次退货退还" />
        <Card label="累计已退" value={fmt(order.succeeded_total)} cls="ok" />
        <Card label="退款占用中" value={fmt(order.held_total)} cls="warn" />
        <Card label="可退余额" value={fmt(order.refundable_remaining)} cls="primary" />
      </section>

      <section className="panel">
        <h3>下单快照 · 优惠按权重分摊（尾差稳定排序分配）</h3>
        <table>
          <thead>
            <tr>
              <th></th><th>#</th><th>商品</th><th>标价</th>
              <th>分摊优惠</th><th>实付分摊</th><th>累计已退</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            {order.lines.map((l) => (
              <tr key={l.id} className={l.is_gift ? "gift-row" : ""}>
                <td>
                  {!l.is_gift && l.status === "NORMAL" && (
                    <input
                      type="checkbox"
                      checked={checked.includes(l.id)}
                      onChange={() => toggle(l.id)}
                    />
                  )}
                </td>
                <td>{l.line_no}</td>
                <td>
                  {l.title}
                  {l.is_gift && (
                    <span className="gift-note">（未随末次退货退回将扣 {fmt(l.gift_value)}）</span>
                  )}
                </td>
                <td>{fmt(l.list_price)}</td>
                <td>-{fmt(l.allocated_discount)}</td>
                <td>{fmt(l.allocated_paid)}</td>
                <td>{fmt(l.refunded_amount)}</td>
                <td><Tag status={l.status} /></td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td></td><td></td><td>合计</td>
              <td>{fmt(order.lines.reduce((s, l) => s + l.list_price, 0))}</td>
              <td>-{fmt(order.lines.reduce((s, l) => s + l.allocated_discount, 0))}</td>
              <td>{fmt(order.lines.reduce((s, l) => s + l.allocated_paid, 0))}</td>
              <td colSpan="2"></td>
            </tr>
          </tfoot>
        </table>
      </section>

      <section className="panel">
        <h3>提交退款</h3>
        {selectable.length === 0 ? (
          <p className="muted">该订单没有可退的商品行。</p>
        ) : (
          <>
            <div className="form-row">
              <input
                placeholder="退款原因（选填）"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
                <option value="random">支付模拟器：随机结果</option>
                <option value="success">支付模拟器：成功</option>
                <option value="failure">支付模拟器：失败</option>
                <option value="unknown">支付模拟器：未知（进入待核实）</option>
              </select>
              <button
                className="primary"
                disabled={checked.length === 0}
                onClick={submit}
              >
                提交退款
              </button>
            </div>
            {preview && (
              <p className="preview">
                预计退款 <b>{fmt(preview.total)}</b>（商品 {fmt(preview.goods)}
                {preview.final && (
                  <>
                    {" "}+ 运费 {fmt(preview.shipping)}
                    {preview.giftDed > 0 && <> − 赠品扣款 {fmt(preview.giftDed)}</>}
                    ，<b>末次结清可退尾差</b>
                  </>
                )}
                ）
              </p>
            )}
            {err && <div className="banner error">{err}</div>}
            {msg && <div className="banner ok">{msg}</div>}
          </>
        )}
      </section>

      <section className="panel">
        <h3>退款记录（{order.refunds.length}）</h3>
        {order.refunds.length === 0 && <p className="muted">暂无退款。</p>}
        {order.refunds.map((r) => (
          <RefundCard key={r.id} refund={r} onChanged={onChanged} />
        ))}
      </section>
    </div>
  );
}

function Card({ label, value, hint, cls }) {
  return (
    <div className={`card ${cls || ""}`}>
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      {hint && <div className="card-hint">{hint}</div>}
    </div>
  );
}

function RefundCard({ refund: r, onChanged }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const t = r.calc_trace || {};

  const act = async (fn) => {
    const res = await fn();
    setNote(res.note || "");
    onChanged();
  };

  const firstCallbackId = r.events.find((e) => e.kind === "CALLBACK")?.callback_id;

  return (
    <div className="refund">
      <div className="refund-head" onClick={() => setOpen(!open)}>
        <span className="mono">{r.refund_no}</span>
        <Tag status={r.status} />
        {r.is_final && <span className="tag tag-purple">末次结清</span>}
        <span>
          退 <b>{fmt(r.amount + r.shipping_refund)}</b>
          <span className="muted">
            （商品 {fmt(r.amount)}
            {r.shipping_refund > 0 && ` + 运费 ${fmt(r.shipping_refund)}`}
            {r.gift_deduction > 0 && `，已扣赠品 ${fmt(r.gift_deduction)}`}）
          </span>
        </span>
        <span className="muted">{fmtTime(r.created_at)}</span>
        <span className="muted">{open ? "▲ 收起" : "▼ 计算依据"}</span>
      </div>

      {open && (
        <div className="refund-body">
          {r.reason && <p>原因：{r.reason}</p>}
          <p className="muted">网关流水：{r.gateway_txn || "—"} · 计算口径：{t.formula}</p>

          <table className="trace">
            <thead>
              <tr><th>#</th><th>商品</th><th>标价</th><th>分摊优惠</th><th>实付分摊(=本次退款基数)</th></tr>
            </thead>
            <tbody>
              {(t.selected_lines || []).map((l) => (
                <tr key={l.line_no}>
                  <td>{l.line_no}</td><td>{l.title}</td>
                  <td>{fmt(l.list_price)}</td>
                  <td>-{fmt(l.allocated_discount)}</td>
                  <td>{fmt(l.allocated_paid)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <ul className="trace-math">
            <li>订单实付 {fmt(t.paid_total)} − 此前已退 {fmt(t.succeeded_before)} − 占用 {fmt(t.held_before)}</li>
            <li>本次行分摊合计 {fmt(t.lines_base)}
              {t.is_final ? `，末次结清改按可退余额 ${fmt(t.goods_amount + (t.gift_deduction || 0))}` : ""}
            </li>
            {t.gift_deduction > 0 && (
              <li>赠品未退回扣款 −{fmt(t.gift_deduction)}（
                {t.unreturned_gifts.map((g) => `${g.title} ${fmt(g.gift_value)}`).join("、")}）
              </li>
            )}
            {t.shipping_refund > 0 && <li>末次退运费 +{fmt(t.shipping_refund)}</li>}
            <li><b>本次应退 = {fmt(r.amount + r.shipping_refund)}</b></li>
          </ul>

          {r.status === "PENDING_VERIFICATION" && (
            <div className="actions">
              <span className="muted">网关结果未知，额度占用中，不可重复退款：</span>
              <button onClick={() => act(() => api.recheck(r.id, { force: "random" }))}>主动核实</button>
              <button onClick={() => act(() => api.recheck(r.id, { force: "success" }))}>核实=成功</button>
              <button onClick={() => act(() => api.recheck(r.id, { force: "failure" }))}>核实=失败</button>
              <button
                onClick={() =>
                  act(() =>
                    api.callback(r.id, {
                      callback_id: `CB-${Date.now()}`,
                      result: "SUCCESS",
                    })
                  )
                }
              >
                模拟网关回调(成功)
              </button>
              {firstCallbackId && (
                <button
                  onClick={() =>
                    act(() =>
                      api.callback(r.id, { callback_id: firstCallbackId, result: "SUCCESS" })
                    )
                  }
                >
                  重发回调 {firstCallbackId}（验证幂等）
                </button>
              )}
            </div>
          )}
          {note && <div className="banner ok">{note}</div>}

          <h4>事件流</h4>
          <ul className="events">
            {r.events.map((e) => (
              <li key={e.id}>
                <span className="muted">{fmtTime(e.created_at)}</span>{" "}
                <b>{{ SUBMIT: "提交网关", CALLBACK: "网关回调", RECHECK: "主动核实" }[e.kind]}</b>
                {e.callback_id && <span className="mono"> [{e.callback_id}]</span>} — {e.note}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
