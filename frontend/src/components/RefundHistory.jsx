import React, { useState } from "react";
import { api } from "../api";
import { EventBadge, Money, StatusBadge } from "../money";

/** 退款记录列表：状态、计算依据、支付模拟、回调重放 */
export default function RefundHistory({ refunds, onChanged, notify }) {
  if (!refunds.length) {
    return <div className="empty">暂无退款记录</div>;
  }
  return (
    <div className="refund-list">
      {refunds.map((r) => (
        <RefundRow key={r.id} refund={r} onChanged={onChanged} notify={notify} />
      ))}
    </div>
  );
}

function RefundRow({ refund, onChanged, notify }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const pay = async (outcome) => {
    setBusy(true);
    try {
      const { data } = await api.simulatePay(refund.id, outcome);
      const label = { success: "支付成功", failed: "支付失败", unknown: "结果未知" }[outcome];
      await onChanged(`模拟器：${label} — ${data.note || data.refund.status_display}`);
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const replay = async (replayLast) => {
    setBusy(true);
    try {
      const { data } = await api.callback({
        transaction_id: refund.transaction_id,
        status: refund.status === "PENDING" ? "SUCCESS" : refund.status,
        replay_last: replayLast,
      });
      const map = {
        duplicate: "重复回调：幂等忽略，状态不变",
        conflict: "状态冲突：已拒绝，保持原状态",
        accepted: "回调已受理",
        held: "结果未知：保持待核实",
      };
      await onChanged(`回调处理结果：${map[data.result] || data.result}`);
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`refund-card status-${refund.status.toLowerCase()}`}>
      <div className="refund-head" onClick={() => setOpen(!open)}>
        <span className="mono">{refund.refund_no}</span>
        <Money cents={refund.amount_cents} strong />
        <StatusBadge status={refund.status} />
        <span className="muted">{refund.created_at}</span>
        <span className="expand">{open ? "收起 ▲" : "计算依据 ▼"}</span>
      </div>

      <div className="refund-actions">
        {refund.status === "PENDING" && (
          <>
            <span className="action-label">支付模拟器：</span>
            <button className="btn btn-sm btn-ok" disabled={busy} onClick={() => pay("success")}>
              模拟成功
            </button>
            <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => pay("failed")}>
              模拟失败
            </button>
            <button className="btn btn-sm btn-warn" disabled={busy} onClick={() => pay("unknown")}>
              模拟未知（保持待核实）
            </button>
          </>
        )}
        {refund.transaction_id && refund.events.length > 0 && (
          <>
            <span className="action-label">回调演练：</span>
            <button className="btn btn-sm" disabled={busy} onClick={() => replay(true)}>
              重放同一回调
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => replay(false)}>
              重复通知（新事件号）
            </button>
          </>
        )}
      </div>

      {open && (
        <div className="refund-detail">
          <h4>逐行计算依据</h4>
          <table className="table">
            <thead>
              <tr>
                <th>商品</th>
                <th className="num">退件数</th>
                <th className="num">金额</th>
                <th>计算依据</th>
              </tr>
            </thead>
            <tbody>
              {refund.items.map((it) => (
                <tr key={it.id}>
                  <td>
                    {it.title}
                    {it.is_gift && <span className="badge badge-gift">赠品</span>}
                  </td>
                  <td className="num">{it.qty}</td>
                  <td className="num"><Money cents={it.amount_cents} strong /></td>
                  <td className="explain">{it.calc_detail.explain}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="formula">
            {refund.trace?.order_level?.formula}
            {refund.trace?.order && (
              <span className="muted">
                ｜ 本单创建前已占用 <Money cents={refund.trace.order.occupied_before_cents} />
                ，本单后剩余可退 <Money cents={refund.trace.order.remaining_after_cents} />
              </span>
            )}
          </div>

          <h4>支付事件流水{refund.transaction_id && <span className="mono muted">（流水号 {refund.transaction_id}）</span>}</h4>
          {refund.events.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件号</th>
                  <th>网关状态</th>
                  <th>处理结果</th>
                </tr>
              </thead>
              <tbody>
                {refund.events.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.created_at}</td>
                    <td className="mono">{e.event_id}</td>
                    <td>{e.gateway_status}</td>
                    <td><EventBadge result={e.result} text={e.result_display} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="muted">尚无支付事件（可点击上方「支付模拟器」触发）</div>
          )}
          {refund.failure_reason && (
            <div className="alert alert-error">失败原因：{refund.failure_reason}</div>
          )}
        </div>
      )}
    </div>
  );
}
