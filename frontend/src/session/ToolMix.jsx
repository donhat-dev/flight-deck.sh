/**
 * The shape of the work in one session.
 *
 * Counted over the WHOLE session from the ledger's `tool_calls` table, not over
 * the window the reader happens to have loaded, because "this session is 63%
 * Bash" is only true if you counted all of it.
 *
 * Bars are linear against the top tool, not against the total. One tool
 * usually dominates by an order of magnitude, so a share-of-total bar would
 * render everything below first place as an invisible sliver; the percentage
 * next to each bar carries the true share.
 */
import React from "react";

const SERVER_COLOR = [
  "var(--fds-info)", "var(--fds-a5)", "var(--fds-a2)",
  "var(--fds-a3)", "var(--fds-a6)", "var(--fds-b1)",
];

/** `mcp__chrome-devtools__take_snapshot` reads as `take_snapshot`. */
const shortName = (t) => {
  const m = /^mcp__[^_]+(?:_[^_]+)*?__(.+)$/.exec(t || "");
  return m ? m[1] : t;
};

export default function ToolMix({ mix }) {
  if (!mix) return <div className="fds-label">no tool ledger for this session</div>;
  const { total, distinct, tools = [], other = 0, by_server: servers = [] } = mix;
  if (!total) return <div className="fds-label">no tool calls recorded</div>;
  const top = tools[0]?.count || 1;

  return (
    <div className="fds-mix">
      <div className="fds-reading">
        <span className="fds-label">tool calls</span>
        <span className="fds-reading-value">{total.toLocaleString()}</span>
      </div>
      <div className="fds-reading">
        <span className="fds-label">distinct tools</span>
        <span className="fds-reading-value">{distinct}</span>
      </div>

      <div className="fds-label" style={{ marginTop: 10 }}>where they came from</div>
      <div className="fds-mix-stack">
        {servers.map((s, i) => (
          <span key={s.server} className="fds-mix-seg" title={`${s.server}: ${s.count}`}
                style={{ width: `${(s.count / total) * 100}%`, background: SERVER_COLOR[i % SERVER_COLOR.length] }} />
        ))}
      </div>
      <div className="fds-mix-legend">
        {servers.map((s, i) => (
          <span className="fds-mix-key" key={s.server}>
            <span className="fds-mix-dot" style={{ background: SERVER_COLOR[i % SERVER_COLOR.length] }} />
            <span className="fds-label">{s.server}</span>
            <span className="fds-label" style={{ color: "var(--fdx-text-muted)" }}>
              {Math.round((s.count / total) * 100)}%
            </span>
          </span>
        ))}
      </div>

      <div className="fds-label" style={{ marginTop: 12 }}>what ran</div>
      <div className="fds-mix-bars">
        {tools.map((t) => (
          <div className="fds-mix-row" key={t.tool} title={`${t.tool}: ${t.count}`}>
            <span className="fds-mix-name">{shortName(t.tool)}</span>
            <span className="fds-mix-track">
              <span className="fds-mix-fill"
                    style={{ width: `${Math.max(2, (t.count / top) * 100)}%`,
                             background: t.server ? "var(--fds-info)" : "var(--fdx-signal-hover)" }} />
            </span>
            <span className="fds-mix-count">{t.count.toLocaleString()}</span>
            <span className="fds-mix-pct">{((t.count / total) * 100).toFixed(1)}%</span>
          </div>
        ))}
        {other > 0 && (
          <div className="fds-mix-row">
            <span className="fds-mix-name fds-faint">everything else</span>
            <span className="fds-mix-track" />
            <span className="fds-mix-count fds-faint">{other.toLocaleString()}</span>
            <span className="fds-mix-pct fds-faint">{((other / total) * 100).toFixed(1)}%</span>
          </div>
        )}
      </div>
    </div>
  );
}
