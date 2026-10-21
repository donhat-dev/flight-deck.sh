/**
 * One tool call.
 *
 * The header is six fixed slots, in the same order every time, because the
 * collapsed state IS the header: if it does not say enough, the reader has to
 * open every call to find the one they want.
 *
 *   [status dot] [tool chip] [target ..................] [duration] [badge] [v]
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { pickRenderer, resultBadge, toolTarget, toolLabel } from "./registry.js";
import { Terminal, DiffView, FileTree, ImageBox, DataTable, JsonTree, WebCard, Widget,
         Snapshot, TodoList, CodeBlock } from "./renderers.jsx";
import { highlightCommand } from "./syntax.js";
import { toolColor } from "./toolcolor.js";

const TONE_VAR = { ok: "var(--fds-ok)", err: "var(--fds-err)", warn: "var(--fds-warn)", info: "var(--fds-info)" };

function Body({ kind, props }) {
  switch (kind) {
    case "image": return <ImageBox {...props} />;
    case "diff": return <DiffView {...props} />;
    case "tree": return <FileTree {...props} />;
    case "table": return <DataTable {...props} />;
    case "json": return <JsonTree {...props} />;
    case "web": return <WebCard {...props} />;
    case "widget": return <Widget {...props} />;
    case "snapshot": return <Snapshot {...props} />;
    case "todo": return <TodoList {...props} />;
    case "code": return <CodeBlock {...props} />;
    case "inline": return null;
    default: return <Terminal {...props} />;
  }
}

const fmtDur = (ms) => {
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}MS`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}S`;
  return `${Math.floor(ms / 60000)}M ${Math.round((ms % 60000) / 1000)}S`;
};

/* A command longer than this is a script that happens to have been pasted into
   a shell, and it pushes the result it produced off the screen. */
const CMD_FOLD = 24;

export default function ToolCard({ block, startTs, defaultOpen = false, cursor = false, agent = null }) {
  const [cmdOpen, setCmdOpen] = useState(false);
  const renderer = useMemo(
    () => pickRenderer(block.name, block.input, block.result),
    [block.name, block.input, block.result]);
  const badge = useMemo(
    () => resultBadge(block.name, block.input, block.result, renderer),
    [block, renderer]);

  const failed = Boolean(block.result?.is_error);
  // A failure and a picture are worth opening unasked; everything else earns
  // its space only when the reader asks for it.
  // A one-line answer is already in the header, so there is nothing to open.
  const inline = renderer.kind === "inline";
  const auto = !inline && (defaultOpen || failed || renderer.kind === "image" || renderer.kind === "todo");
  const [open, setOpen] = useState(auto);
  // Focus mode flips `defaultOpen` on cards that are already mounted, so the
  // card follows it - until the reader overrides this one by hand, after which
  // their choice wins.
  const touched = useRef(false);
  useEffect(() => { if (!touched.current) setOpen(auto); }, [auto]);
  const toggle = () => { touched.current = true; setOpen((o) => !o); };

  const { server, tool } = toolLabel(block.name);
  const target = toolTarget(block.name, block.input);
  const dur = fmtDur(startTs && block.result?.ts ? Date.parse(block.result.ts) - Date.parse(startTs) : NaN);
  const tone = failed ? "err" : "ok";

  return (
    <div className={`fds-card ${cursor ? "fds-cursor" : ""}`} data-tone={tone} data-tool={tool}
         style={{ "--tool-c": toolColor(block.name) }}>
      <button type="button" className="fds-head" onClick={inline ? undefined : toggle}
              aria-expanded={inline ? undefined : open} data-inline={inline ? "true" : undefined}>
        <span className="fds-dot" style={{ background: TONE_VAR[tone] }} />
        {server && <span className="fds-label fds-tool" style={{ flex: "none", opacity: 0.75 }}>{server}</span>}
        <span className="fds-label fds-tool" style={{ flex: "none" }}>{tool}</span>
        <span className="fds-target">{target}</span>
        {inline && <span className="fds-inline-result">{renderer.props.text}</span>}
        <span style={{ flex: 1 }} />
        {dur && <span className="fds-label" style={{ flex: "none" }}>{dur}</span>}
        {badge && (
          <span className="fds-badge"
                style={{ color: TONE_VAR[badge.tone],
                         background: `color-mix(in srgb, ${TONE_VAR[badge.tone]} 14%, transparent)` }}>
            {badge.text}
          </span>
        )}
        {!inline && <span className="fds-label" style={{ flex: "none", letterSpacing: 0 }}>{open ? "▾" : "▸"}</span>}
      </button>
      {open && (
        <>
          {/* What went in. A heredoc is many lines and used to be flattened into
              one wrapped paragraph, which made the command unreadable exactly
              when it was long enough to matter. */}
          {renderer.kind !== "diff" && renderer.kind !== "image" && block.input?.command && (
            <div className="fds-io-in fds-mono">
              {(() => {
                const all = highlightCommand(block.input.command);
                const shown = cmdOpen ? all : all.slice(0, CMD_FOLD);
                const hidden = all.length - shown.length;
                return (<>
              {shown.map((toks, i) => (
                <div className="fds-line" key={i}>
                  {i === 0 && <span style={{ color: "var(--fds-ok)", fontWeight: 600 }}>$ </span>}
                  {i > 0 && <span>{"  "}</span>}
                  {toks.length
                    ? toks.map((tk, j) => (tk.c
                        ? <span className={`tk-${tk.c}`} key={j}>{tk.t}</span>
                        : <span key={j}>{tk.t}</span>))
                    : " "}
                </div>
              ))}
              {hidden > 0 && (
                <button type="button" className="fds-fold" style={{ border: "none", padding: "6px 0 0" }}
                        onClick={(e) => { e.stopPropagation(); setCmdOpen(true); }}>
                  show the remaining {hidden} lines of the command
                </button>
              )}
                </>);
              })()}
            </div>
          )}
          <div className={block.input?.command ? "fds-io-out" : undefined}>
            <Body kind={renderer.kind} props={renderer.props} />
          </div>
          {agent}
        </>
      )}
    </div>
  );
}
