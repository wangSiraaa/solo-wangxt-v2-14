const BASE = "/api";

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.detail || `请求失败(${resp.status})`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return { data, status: resp.status };
}

export const api = {
  listOrders: () => request("/orders/"),
  getOrder: (id) => request(`/orders/${id}/`),
  getLedger: (id) => request(`/orders/${id}/ledger/`),
  quoteRefund: (orderId, lines) =>
    request(`/orders/${orderId}/refunds/quote/`, {
      method: "POST",
      body: JSON.stringify({ lines }),
    }),
  createRefund: (orderId, lines, idempotencyKey) =>
    request(`/orders/${orderId}/refunds/`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ lines }),
    }),
  simulatePay: (refundId, outcome) =>
    request(`/refunds/${refundId}/pay/`, {
      method: "POST",
      body: JSON.stringify({ outcome }),
    }),
  callback: (payload) =>
    request(`/payments/callback/`, { method: "POST", body: JSON.stringify(payload) }),
};
