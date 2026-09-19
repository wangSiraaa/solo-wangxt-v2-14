const BASE = "/api";

async function req(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
  return data;
}

export const api = {
  listOrders: () => req("/orders/"),
  getOrder: (id) => req(`/orders/${id}/`),
  createRefund: (orderId, body) =>
    req(`/orders/${orderId}/refunds/`, { method: "POST", body: JSON.stringify(body) }),
  callback: (refundId, body) =>
    req(`/refunds/${refundId}/callback/`, { method: "POST", body: JSON.stringify(body) }),
  recheck: (refundId, body) =>
    req(`/refunds/${refundId}/recheck/`, { method: "POST", body: JSON.stringify(body) }),
};
