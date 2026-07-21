export async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function del(path) {
  const r = await fetch(path, { method: "DELETE" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
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
