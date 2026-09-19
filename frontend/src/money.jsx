export function fmt(cents) {
  return `¥${(cents / 100).toFixed(2)}`;
}

export function Money({ cents, strong, className = "" }) {
  return (
    <span className={`money ${strong ? "strong" : ""} ${className}`}>
      {fmt(cents)}
    </span>
  );
}

const STATUS_MAP = {
  PENDING: { text: "待核实", cls: "badge-pending" },
  SUCCESS: { text: "退款成功", cls: "badge-success" },
  FAILED: { text: "支付失败", cls: "badge-failed" },
};

export function StatusBadge({ status }) {
  const s = STATUS_MAP[status] || { text: status, cls: "" };
  return <span className={`badge ${s.cls}`}>{s.text}</span>;
}

const EVENT_CLS = {
  accepted: "badge-success",
  held: "badge-pending",
  duplicate: "badge-muted",
  conflict: "badge-failed",
};

export function EventBadge({ result, text }) {
  return <span className={`badge ${EVENT_CLS[result] || ""}`}>{text}</span>;
}
