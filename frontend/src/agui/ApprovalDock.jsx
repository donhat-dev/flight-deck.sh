import React, { useEffect, useState } from "react";
import { get, post, subscribeEvents } from "../api.js";
import {
  Button, EmptyState, StatusBadge, SurfaceCard, TextAreaField,
} from "../ui/FlightComponents.jsx";

/* ---- Approval dock ------------------------------------------------------
 * Renders the synchronous approval channel's pending decisions: tool calls a
 * `bin/fd-approve` PreToolUse hook escalated (per `~/.flightdeck/approve.conf`)
 * and is blocking on, polling `GET /api/decisions/{id}` for up to 90s before
 * failing open. This view is the human side of that wait.
 *
 * Built from `../ui/FlightComponents.jsx` primitives rather than hand-rolled
 * markup, per DESIGN.md §12 (extend an existing component before forking a
 * local style) — SurfaceCard already carries the three-layer control depth
 * and the Ready/Loading/Needs-attention state map C3 expects, and using it
 * through its component API (not by copying its `fdx-*` class names into this
 * file) is also what keeps a list of decisions clear of the composition
 * lint's C3c check: that rule flags a depth-bearing class name written as a
 * literal string inside a `.map()`, and no such literal exists here — the
 * class lives once, inside FlightComponents.jsx's own definition.
 */

// Mirrors flightdeck.decisions.TIMEOUT_SECONDS. Duplicated rather than fetched
// because the countdown is cosmetic (a rough "how much longer"); the row the
// dock removes on `decision.resolved` is the actual source of truth for
// whether the call already auto-released server-side.
const TIMEOUT_SECONDS = 90;

function ageSeconds(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : (Date.now() - t) / 1000;
}

function Countdown({ createdAt }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const age = ageSeconds(createdAt);
  const remaining = age == null ? TIMEOUT_SECONDS : Math.max(0, Math.round(TIMEOUT_SECONDS - age));
  return (
    <span className="fdx-eyebrow" title="Seconds left before this call auto-releases (fail-open)">
      {remaining}s to auto-release
    </span>
  );
}

function DecisionCard({ decision, busy, onDecide }) {
  const [editing, setEditing] = useState(false);
  const [cmd, setCmd] = useState(decision.command || "");

  return (
    <SurfaceCard
      eyebrow={decision.session_id ? `session ${decision.session_id}` : "approval needed"}
      title={decision.tool_name}
      loading={busy}
      footer={
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--fdx-space-3)" }}>
          <Button
            variant="primary"
            loading={busy}
            onClick={() => onDecide(decision.id, editing ? "edit" : "approve", editing ? cmd : undefined)}
          >
            {editing ? "Approve edited" : "Approve"}
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => onDecide(decision.id, "reject")}>
            Reject
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel edit" : "Edit"}
          </Button>
          <Countdown createdAt={decision.created_at} />
        </div>
      }
    >
      {decision.pattern && (
        <div className="fdx-eyebrow" style={{ marginBottom: "var(--fdx-space-2)" }}>
          matched pattern: {decision.pattern}
        </div>
      )}
      {editing ? (
        <TextAreaField
          label="Run this instead"
          hint="Sent back as the approved command when you Approve edited."
          rows={2}
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
        />
      ) : (
        <pre style={{
          margin: 0, whiteSpace: "pre-wrap", overflowX: "auto",
          fontFamily: "var(--fdx-font-mono)", fontSize: "0.8rem", color: "var(--fdx-text)",
        }}
        >
          {decision.command}
        </pre>
      )}
      {decision.cwd && (
        <div className="fdx-eyebrow" style={{ marginTop: "var(--fdx-space-2)" }}>{decision.cwd}</div>
      )}
    </SurfaceCard>
  );
}

export default function ApprovalDock() {
  const [decisions, setDecisions] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [live, setLive] = useState(false);

  // Initial state from a plain fetch; the bus is what keeps it current after
  // that (a hook can create a decision, and a second dashboard tab can
  // resolve it, at any moment this view is open).
  useEffect(() => {
    get("/api/decisions").then((d) => setDecisions(d.decisions || [])).catch(() => {});
  }, []);

  useEffect(() => subscribeEvents("decision", (ev) => {
    if (ev.kind === "decision.pending") {
      setDecisions((cur) => (cur.some((d) => d.id === ev.payload.id) ? cur : [...cur, ev.payload]));
    } else if (ev.kind === "decision.resolved") {
      setDecisions((cur) => cur.filter((d) => d.id !== ev.payload.id));
    }
  }, setLive), []);

  const decide = async (id, action, editedCommand) => {
    setBusyId(id);
    try {
      await post(`/api/decisions/${id}/resolve`, { action, edited_command: editedCommand });
      // The `decision.resolved` event will also arrive over SSE and no-op
      // against an id already removed; this just avoids waiting on it for
      // the row to disappear from under the button that was just clicked.
      setDecisions((cur) => cur.filter((d) => d.id !== id));
    } catch {
      // Leave the row pending — its own controls stay usable to retry, and
      // the hook keeps polling regardless of whether this request landed.
    } finally {
      setBusyId(null);
    }
  };

  if (decisions.length === 0) {
    return (
      <EmptyState title="No approvals waiting">
        A tool call matching a pattern in <code>~/.flightdeck/approve.conf</code> will appear here,
        blocking the session that made it until answered or auto-released.
      </EmptyState>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--fdx-space-4)" }}>
      <StatusBadge tone={live ? "live" : "neutral"} pulse={live}>
        {decisions.length} waiting
      </StatusBadge>
      {decisions.map((d) => (
        <DecisionCard key={d.id} decision={d} busy={busyId === d.id} onDecide={decide} />
      ))}
    </div>
  );
}
