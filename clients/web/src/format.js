const compactFormatter = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

const moneyFormatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function usd(value) {
  const number = Number(value);
  return Number.isFinite(number) ? moneyFormatter.format(number) : "—";
}

export function compact(value) {
  const number = Number(value);
  return Number.isFinite(number) ? compactFormatter.format(number) : "—";
}

export function pct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const percent = number * 100;
  return `${Math.round(percent * 10) / 10}%`;
}

export function day(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function hm(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function shortModel(value) {
  if (!value) return "Unknown";
  const cleaned = String(value)
    .replace(/^claude-/, "")
    .replace(/-\d{8}$/, "")
    .replace(/[-_]+/g, " ");
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}
