// Fold an AG-UI event stream into render state. Kept a pure function so the
// same reducer serves the initial run and the resume stream (the caller resets
// state only before a FRESH run, never on resume — so resume continues the
// same timeline).

export const initialRun = {
  status: "idle",        // idle | running | interrupted | done | error
  runId: null,
  threadId: null,
  timeline: [],          // ordered render items (see kinds below)
  state: {},             // AG-UI shared state (STATE_SNAPSHOT + STATE_DELTA)
  interrupt: null,       // { toolCallId, toolName, command, options, reason }
  result: null,
  error: null,
};

// Apply a subset of RFC-6902 JSON Patch (the ops our adapter emits): replace,
// add — with "/-" meaning append to an array. Shallow paths only.
function applyPatch(obj, ops) {
  const next = structuredClone(obj);
  for (const op of ops || []) {
    const parts = op.path.split("/").slice(1); // drop leading ""
    if (parts.length === 1) {
      next[parts[0]] = op.value;
    } else if (parts.length === 2) {
      const [key, idx] = parts;
      if (!Array.isArray(next[key])) next[key] = [];
      if (idx === "-") next[key].push(op.value);
      else next[key][Number(idx)] = op.value;
    }
  }
  return next;
}

// Find the last timeline item matching a predicate (events mutate the most
// recent message/tool with a given id).
function patchItem(timeline, id, fn) {
  const next = timeline.slice();
  for (let i = next.length - 1; i >= 0; i--) {
    if (next[i].id === id) {
      next[i] = fn(next[i]);
      return next;
    }
  }
  return next;
}

export function applyEvent(run, e) {
  switch (e.type) {
    case "RUN_STARTED":
      return { ...run, status: "running", runId: e.runId, threadId: e.threadId };

    case "RUN_FINISHED": {
      const interrupted = e.result && e.result.reason === "interrupted";
      return {
        ...run,
        status: interrupted ? "interrupted" : "done",
        result: e.result || null,
        interrupt: interrupted ? run.interrupt : null,
      };
    }

    case "RUN_ERROR":
      return { ...run, status: "error", error: e.message || "run error" };

    case "STEP_STARTED":
      return { ...run, timeline: [...run.timeline, { kind: "step", id: `step-${run.timeline.length}`, name: e.stepName }] };

    case "STATE_SNAPSHOT":
      return { ...run, state: e.snapshot || {} };

    case "STATE_DELTA":
      return { ...run, state: applyPatch(run.state, e.delta) };

    case "TEXT_MESSAGE_START":
      return { ...run, timeline: [...run.timeline, { kind: "message", id: e.messageId, role: e.role || "assistant", text: "", done: false }] };

    case "TEXT_MESSAGE_CONTENT":
      return { ...run, timeline: patchItem(run.timeline, e.messageId, (it) => ({ ...it, text: it.text + (e.delta || "") })) };

    case "TEXT_MESSAGE_END":
      return { ...run, timeline: patchItem(run.timeline, e.messageId, (it) => ({ ...it, done: true })) };

    case "TOOL_CALL_START":
      return { ...run, timeline: [...run.timeline, { kind: "tool", id: e.toolCallId, name: e.toolCallName, args: "", result: null, isError: false, status: "running" }] };

    case "TOOL_CALL_ARGS":
      return { ...run, timeline: patchItem(run.timeline, e.toolCallId, (it) => ({ ...it, args: it.args + (e.delta || "") })) };

    case "TOOL_CALL_END":
      return run; // result may still be pending; keep status "running"

    case "TOOL_CALL_RESULT":
      return { ...run, timeline: patchItem(run.timeline, e.toolCallId, (it) => ({ ...it, result: e.content, isError: !!e.isError, status: "done" })) };

    case "CUSTOM":
      if (e.name === "interrupt") {
        return { ...run, interrupt: e.value };
      }
      if (e.name === "args_replace" && e.value) {
        return { ...run, timeline: patchItem(run.timeline, e.value.toolCallId, (it) => ({ ...it, args: e.value.args })) };
      }
      if (e.name === "activity" || e.name === "reasoning") {
        return { ...run, timeline: [...run.timeline, { kind: e.name, id: `${e.name}-${run.timeline.length}`, text: (e.value && e.value.text) || "" }] };
      }
      return run;

    default:
      return run;
  }
}
