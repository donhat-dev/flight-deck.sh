/**
 * Turn a failed response into an error that says WHY.
 *
 * These used to throw `"/api/x: 400"` and drop the body, which is where the
 * reason lives: FastAPI puts it in `detail`, and for the treasures write paths
 * that detail is the service's own validation text ("title is required when
 * passing content="). Without it a form can only report a number.
 */
async function fail(path, r) {
  let detail = "";
  try {
    const body = await r.json();
    detail = typeof body?.detail === "string" ? body.detail : "";
  } catch {
    /* not JSON, or empty — the status is all we have */
  }
  const err = new Error(detail || `${path}: ${r.status}`);
  err.status = r.status;
  err.detail = detail;
  throw err;
}

export async function get(path) {
  const r = await fetch(path);
  if (!r.ok) await fail(path, r);
  return r.json();
}

export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) await fail(path, r);
  return r.json();
}

export async function del(path) {
  const r = await fetch(path, { method: "DELETE" });
  if (!r.ok) await fail(path, r);
  return r.json();
}

// Subscribe to the server's SSE stream. `onUpdate` fires on each
// `summary-updated` event; `onStatus(true|false)` reflects the real
// connection state so the UI can show a truthful live/offline indicator.
export function subscribe(onUpdate, onStatus) {
  const es = new EventSource("/api/stream");
  es.addEventListener("summary-updated", onUpdate);
  es.onopen = () => onStatus?.(true);
  es.onerror = () => onStatus?.(false);
  return () => es.close();
}
