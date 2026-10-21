// AG-UI client: POST a run/resume request and parse the Server-Sent-Events
// response by hand. We read response.body as a stream (EventSource can't POST),
// split on the SSE frame boundary (\n\n), and hand each parsed event to onEvent.

async function pump(res, onEvent) {
  if (!res.ok || !res.body) {
    throw new Error(`agui stream: ${res.status}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let sep;
    // Frames are separated by a blank line ("\n\n").
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        onEvent(JSON.parse(json));
      } catch {
        /* ignore a partial/garbled frame */
      }
    }
  }
}

export async function streamRun(body, { onEvent, signal } = {}) {
  const res = await fetch("/api/agui/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await pump(res, onEvent);
}

export async function resumeRun(body, { onEvent, signal } = {}) {
  const res = await fetch("/api/agui/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await pump(res, onEvent);
}
