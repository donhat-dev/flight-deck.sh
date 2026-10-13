/**
 * Formatters shared by the library and the detail page.
 *
 * They lived twice — once in each — and the copies had drifted: one returned the
 * raw timestamp string for an unparseable date, the other an em dash, so the same
 * bad value rendered differently depending on which screen you were on. Detail's
 * copy also knew about months and years while the row's stopped at weeks.
 *
 * This is the merge, and it keeps the row's contract for the range the row's tests
 * pin (minutes → weeks, "—" for anything unparseable) while keeping the longer
 * units the detail page needs for old artifacts.
 */

export function relTime(ts) {
  if (!ts) return "—";
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.round(days / 7)}w ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

// Zero is not a size — an artifact of zero bytes means "not rendered", and
// printing "0.0 KB" would present a missing render as a real measurement.
export function kb(bytes) {
  if (!bytes) return "—";
  return `${(bytes / 1024).toFixed(1)} KB`;
}
