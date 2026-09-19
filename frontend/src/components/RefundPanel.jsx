import React from "react";
import { Money } from "../money";

/** 退款试算预览 + 提交 */
export default function RefundPanel({
  hasSelection,
  quote,
  quoteError,
  submitting,
  onSubmit,
}) {
  if (!hasSelection && !quoteError) return null;

  return (
    <section className="panel quote-panel">
      <h3>退款试算</h3>
      {quoteError && <div className="alert alert-error">{quoteError}</div>}
      {quote && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>商品</th>
                <th className="num">退件数</th>
                <th className="num">退款金额</th>
                <th>计算依据</th>
              </tr>
            </thead>
            <tbody>
              {quote.trace.lines.map((l) => (
                <tr key={l.line_no}>
                  <td>
                    {l.title}
                    {l.is_gift && <span className="badge badge-gift">赠品</span>}
                  </td>
                  <td className="num">{l.qty_requested}</td>
                  <td className="num"><Money cents={l.amount_cents} strong /></td>
                  <td className="explain">{l.explain}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="quote-total">
            <div className="formula">{quote.trace.order_level.formula}</div>
            <div>
              本次应退：<Money cents={quote.total_cents} strong className="big" />
              {quote.trace.order_level.settles_order && (
                <span className="badge badge-primary">整单结清</span>
              )}
            </div>
          </div>
          <button className="btn btn-primary" disabled={submitting} onClick={onSubmit}>
            {submitting ? "提交中…" : "提交退款（创建后占用额度，等待支付结果）"}
          </button>
        </>
      )}
    </section>
  );
}
