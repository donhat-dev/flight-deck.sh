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

export async function patch(path, body) {
  const r = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
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

/**
 * Subscribe to typed backend events (`events.BUS` on the server).
 *
 * Every kind rides one SSE event name, `fd-event`, so a new server-side kind
 * needs no change here — the filter is on `kind` in the payload. `kindPrefix`
 * is a plain string prefix matching the server's `Bus.on` semantics: `""` takes
 * everything, `"decision"` takes `decision.pending` and `decision.resolved`.
 *
 * Separate EventSource from `subscribe()` on purpose. A caller that only wants
 * decisions should not have to own the dashboard's refetch callback, and the
 * server gives each connection its own queue, so two connections cost two
 * queues rather than one raced flag.
 */
export function subscribeEvents(kindPrefix, onEvent, onStatus) {
  const es = new EventSource("/api/stream");
  es.addEventListener("fd-event", (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return; // malformed frame: drop it rather than break the stream
    }
    if (typeof ev?.kind === "string" && ev.kind.startsWith(kindPrefix)) onEvent(ev);
  });
  es.onopen = () => onStatus?.(true);
  es.onerror = () => onStatus?.(false);
  return () => es.close();
}
