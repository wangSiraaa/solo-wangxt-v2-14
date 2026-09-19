import React from "react";
import { Money } from "../money";

/** 商品分摊明细表：财务视角的优惠/运费/实付分摊 + 客服的退件选择 */
export default function AllocationTable({ items, selection, onChangeQty }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>#</th>
          <th>商品</th>
          <th className="num">单价</th>
          <th className="num">数量</th>
          <th className="num">行原价</th>
          <th className="num">优惠分摊</th>
          <th className="num">运费分摊</th>
          <th className="num">实付分摊</th>
          <th className="num">单件可退</th>
          <th className="num">已退/占用</th>
          <th className="num">可退</th>
          <th className="num">本次退件</th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const q = selection[it.id] || 0;
          return (
            <tr key={it.id} className={it.is_gift ? "gift-row" : ""}>
              <td className="mono">{it.line_no}</td>
              <td>
                {it.title}
                {it.is_gift && <span className="badge badge-gift">赠品</span>}
                <div className="sku mono">{it.sku}</div>
              </td>
              <td className="num"><Money cents={it.unit_price_cents} /></td>
              <td className="num">{it.qty}</td>
              <td className="num"><Money cents={it.list_total_cents} /></td>
              <td className="num neg">-<Money cents={it.discount_alloc_cents} /></td>
              <td className="num"><Money cents={it.shipping_alloc_cents} /></td>
              <td className="num"><Money cents={it.paid_alloc_cents} strong /></td>
              <td className="num"><Money cents={it.unit_cents} /></td>
              <td className="num">
                {it.occupied_qty} 件 / <Money cents={it.occupied_amount_cents} />
              </td>
              <td className="num">
                {it.avail_qty} 件 / <Money cents={it.avail_amount_cents} />
              </td>
              <td className="num">
                {it.avail_qty > 0 ? (
                  <select
                    value={q}
                    onChange={(e) => onChangeQty(it.id, Number(e.target.value))}
                  >
                    {Array.from({ length: it.avail_qty + 1 }, (_, n) => (
                      <option key={n} value={n}>
                        {n === 0 ? "不退" : `退 ${n} 件`}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="muted">已退完</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
