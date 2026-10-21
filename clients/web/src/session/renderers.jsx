/**
 * Body renderers for a tool call.
 *
 * One rule: a renderer draws the result in the shape the result already has.
 * A terminal run is lines with colour, an edit is a diff, a search is a tree,
 * a screenshot is a picture. `<pre>` is the fallback, not the default.
 */
import React, { useMemo, useState } from "react";
import { parseAnsi } from "./ansi.js";
import { unifiedDiff } from "./diff.js";
import { buildTree } from "./paths.js";
import { highlight } from "./syntax.js";

/* How many lines of machine output are shown before the fold. Roughly one
   screen: past that the reader is scanning, and scanning wants the header. */
const FOLD_AT = 18;

function Fold({ hidden, onShow, label }) {
  if (!hidden) return null;
  return (
    <button type="button" className="fds-fold" onClick={onShow}>
      show the remaining {hidden.toLocaleString()} {label || "lines"}
    </button>
  );
}

/* ---- terminal -------------------------------------------------------- */

const seg = (s, i) => (
  <span
    key={i}
    style={{
      color: s.hex || (s.key ? `var(--fds-${s.key})` : undefined),
      fontWeight: s.bold ? 600 : undefined,
      opacity: s.dim ? 0.7 : undefined,
      textDecoration: s.underline ? "underline" : undefined,
    }}
  >
    {s.text}
  </span>
);

/* `path:line:` at the head of a line is grep, ripgrep, a linter and half the
   compilers. Colouring those two fields turns a wall of output into a list of
   places, which is what the reader is scanning for. */
const GREP_HEAD = /^([~.]?[\w./@-]*\/[\w.@-]+):(\d+):(?:(\d+):)?/;

function grepSegments(line) {
  const m = GREP_HEAD.exec(line);
  if (!m) return null;
  const out = [{ text: m[1], cls: "tk-path" },
               { text: ":", cls: "tk-punc" },
               { text: m[2], cls: "tk-lineno" }];
  if (m[3]) out.push({ text: ":", cls: "tk-punc" }, { text: m[3], cls: "tk-lineno" });
  out.push({ text: line.slice(m[0].length), cls: null });
  return out;
}

export function Terminal({ text, wrap = false, tone, lang }) {
  const [open, setOpen] = useState(false);
  // Three ways to colour one blob, in order of how much the producer told us:
  // its own ANSI first, then the language the output turns out to be written
  // in, then the path:line shape that every grep-like tool shares.
  const lines = useMemo(() => {
    if (lang && lang !== "plain") {
      return highlight(text, lang).map((toks) =>
        toks.map((t) => ({ text: t.t, cls: t.c ? `tk-${t.c}` : null })));
    }
    return parseAnsi(text).map((segs) => {
      if (segs.length === 1 && !segs[0].key && !segs[0].hex) {
        const g = grepSegments(segs[0].text);
        if (g) return g;
      }
      return segs;
    });
  }, [text, lang]);
  const shown = open ? lines : lines.slice(0, FOLD_AT);
  const hidden = lines.length - shown.length;
  return (
    <>
      <div className={`fds-well fds-mono ${wrap ? "fds-wrap" : ""}`}
           style={tone === "err" ? { color: "var(--fds-del-fg)" } : undefined}>
        {shown.map((segs, i) => (
          <div className="fds-line" key={i}>
            {segs.length
              ? segs.map((sg, j) => (sg.cls
                  ? <span className={sg.cls} key={j}>{sg.text}</span>
                  : seg(sg, j)))
              : " "}
          </div>
        ))}
      </div>
      <Fold hidden={hidden > 0 ? hidden : 0} onShow={() => setOpen(true)} />
    </>
  );
}

/* ---- diff ------------------------------------------------------------ */

export function DiffView({ oldStr, newStr, filePath }) {
  const [open, setOpen] = useState(false);
  const { rows } = useMemo(() => unifiedDiff(oldStr, newStr), [oldStr, newStr]);
  const shown = open ? rows : rows.slice(0, FOLD_AT * 2);
  const hidden = rows.length - shown.length;
  return (
    <>
      {filePath && (
        <div className="fds-mono fds-faint" style={{ padding: "8px 12px 0" }}>{filePath}</div>
      )}
      <div className="fds-well fds-mono" style={{ paddingLeft: 0, paddingRight: 0 }}>
        {shown.map((r, i) => {
          if (r.type === "fold") {
            return (
              <div className="fds-diff-row fds-diff-fold" key={i}>
                <span className="fds-diff-num">{"⋯"}</span>
                <span className="fds-line">  {r.count} unchanged {r.count === 1 ? "line" : "lines"}</span>
              </div>
            );
          }
          const cls = r.type === "add" ? "fds-diff-add" : r.type === "del" ? "fds-diff-del" : "fds-diff-ctx";
          const mark = r.type === "add" ? "+" : r.type === "del" ? "-" : " ";
          return (
            <div className={`fds-diff-row ${cls}`} key={i}>
              <span className="fds-diff-num">{r.a ?? ""}</span>
              <span className="fds-diff-num">{r.b ?? ""}</span>
              <span className="fds-line">{mark} {r.text}</span>
            </div>
          );
        })}
      </div>
      <Fold hidden={hidden > 0 ? hidden : 0} onShow={() => setOpen(true)} label="diff rows" />
    </>
  );
}

/* ---- file tree ------------------------------------------------------- */

function TreeNode({ name, node, depth }) {
  const dirs = [...node.children].filter(([, c]) => !c.isFile || c.children.size);
  const files = [...node.children].filter(([, c]) => c.isFile && !c.children.size);
  return (
    <div style={{ paddingLeft: depth ? 14 : 0 }}>
      {name && !(node.isFile && !node.children.size) && (
        <div className="fds-mono" style={{ color: "var(--fdx-text)", fontWeight: 600 }}>{name}/</div>
      )}
      {dirs.map(([k, c]) => <TreeNode key={k} name={k} node={c} depth={depth + 1} />)}
      {files.map(([k, c]) => (
        <div key={k} className="fds-mono fds-dim"
             style={{ paddingLeft: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <span>{k}</span>
          {c.count > 1 && (
            <span className="fds-badge" style={{ background: "var(--fdx-surface-raised)", color: "var(--fds-info)" }}>
              {c.count}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function FileTree({ counts }) {
  const tree = useMemo(() => buildTree(counts), [counts]);
  return (
    <div className="fds-well" style={{ maxHeight: "26rem", overflow: "auto" }}>
      <TreeNode name="" node={tree} depth={0} />
    </div>
  );
}

/* ---- image ----------------------------------------------------------- */

export function ImageBox({ path }) {
  const [broken, setBroken] = useState(false);
  if (!path) return null;
  const src = `/api/screenshot?path=${encodeURIComponent(path)}`;
  if (broken) {
    return <div className="fds-well fds-mono fds-faint">screenshot file not found - {path}</div>;
  }
  // Fixed box: an image that grows on decode shoves every row below it, and the
  // virtualizer cannot compensate for that.
  return (
    <div className="fds-well">
      <div className="fds-mono fds-faint" style={{ marginBottom: 8 }}>{path}</div>
      <a href={src} target="_blank" rel="noreferrer" title="Open full size"
         style={{ display: "block", height: 320 }}>
        <img src={src} alt="screenshot" onError={() => setBroken(true)}
             style={{ height: "100%", width: "auto", maxWidth: "100%", objectFit: "contain",
                      objectPosition: "left", borderRadius: 8, border: "1px solid var(--fdx-rule)" }} />
      </a>
    </div>
  );
}

/* ---- table ----------------------------------------------------------- */

export function DataTable({ rows, columns }) {
  const cols = useMemo(
    () => columns || [...new Set(rows.flatMap((r) => Object.keys(r || {})))].slice(0, 8),
    [rows, columns]);
  const [open, setOpen] = useState(false);
  const shown = open ? rows : rows.slice(0, 12);
  const cell = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
  return (
    <>
      <div className="fds-well" style={{ padding: "4px 12px 10px" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c} className="fds-label" style={{ textAlign: "left", padding: "8px 12px 6px 0",
                    borderBottom: "1px solid var(--fdx-rule)" }}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                {cols.map((c) => (
                  <td key={c} className="fds-mono"
                      style={{ padding: "6px 12px 6px 0", borderBottom: "1px solid var(--fdx-rule)",
                               color: "var(--fdx-text)", fontVariantNumeric: "tabular-nums",
                               verticalAlign: "top" }}>
                    {cell(r?.[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Fold hidden={open ? 0 : Math.max(0, rows.length - shown.length)} onShow={() => setOpen(true)} label="rows" />
    </>
  );
}

/* ---- json ------------------------------------------------------------ */

function JsonNode({ k, v, depth }) {
  const [open, setOpen] = useState(depth < 1);
  const isObj = v && typeof v === "object";
  if (!isObj) {
    const color = typeof v === "number" ? "var(--fds-a5)"
      : typeof v === "boolean" ? "var(--fds-a3)"
      : v === null ? "var(--fdx-text-muted)" : "var(--fds-a2)";
    return (
      <div className="fds-mono" style={{ paddingLeft: depth * 14 }}>
        {k !== undefined && <span style={{ color: "var(--fds-a6)" }}>{k}: </span>}
        <span style={{ color }}>{v === null ? "null" : typeof v === "string" ? `"${v}"` : String(v)}</span>
      </div>
    );
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
  return (
    <div style={{ paddingLeft: depth * 14 }}>
      <button type="button" className="fds-mono" onClick={() => setOpen((o) => !o)}
              style={{ color: "var(--fdx-text-muted)" }}>
        {open ? "▾" : "▸"} {k !== undefined ? `${k} ` : ""}
        {Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`}
      </button>
      {open && entries.map(([ck, cv]) => <JsonNode key={ck} k={ck} v={cv} depth={depth + 1} />)}
    </div>
  );
}

export function JsonTree({ value }) {
  return (
    <div className="fds-well" style={{ maxHeight: "26rem", overflow: "auto" }}>
      <JsonNode v={value} depth={0} />
    </div>
  );
}

/* ---- web ------------------------------------------------------------- */

export function WebCard({ url, text }) {
  let host = "";
  try { host = new URL(url).host; } catch { host = ""; }
  return (
    <div className="fds-well">
      <div className="fds-label" style={{ marginBottom: 6 }}>{host || "fetched"}</div>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="fds-mono"
           style={{ color: "var(--fds-info)", wordBreak: "break-all" }}>{url}</a>
      )}
      {text && <div className="fds-think fds-wrap" style={{ marginTop: 8 }}>{String(text).slice(0, 1200)}</div>}
    </div>
  );
}

/* ---- widget ---------------------------------------------------------- */

/** An MCP that knows about the deck can return a drawing instead of 4 KB of
 *  JSON. Unknown types fall back to the json tree, so an older deck never
 *  breaks on a newer server. */
export function Widget({ spec, raw }) {
  const [showRaw, setShowRaw] = useState(false);
  const { title, metrics, columns, rows, type } = spec || {};
  const known = type === "table" || type === "metrics";
  // Drawing on the left, source on the right. Stacked, the reader had to scroll
  // away from the picture to check the number under it, which is the one moment
  // they wanted both at once.
  return (
    <>
      <div className={showRaw && raw ? "fds-split" : undefined}>
        <div className="fds-well" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {title && <div className="fds-label">{title}</div>}
          {Array.isArray(metrics) && metrics.length > 0 && (
            <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
              {metrics.map((m, i) => (
                <div key={i}>
                  <div className="fds-label">{m.label}</div>
                  <div className="fds-mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--fdx-text)" }}>
                    {String(m.value)}
                  </div>
                </div>
              ))}
            </div>
          )}
          {known && Array.isArray(rows) && rows.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {(columns || []).map((c) => (
                    <th key={c} className="fds-label" style={{ textAlign: "left", padding: "6px 12px 6px 0",
                        borderBottom: "1px solid var(--fdx-rule)" }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {(Array.isArray(r) ? r : (columns || []).map((c) => r[c])).map((v, j) => (
                      <td key={j} className="fds-mono" style={{ padding: "6px 12px 6px 0",
                          borderBottom: "1px solid var(--fdx-rule)", color: "var(--fdx-text)",
                          fontVariantNumeric: "tabular-nums" }}>{String(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!known && <JsonNode v={spec} depth={0} />}
        </div>
        {showRaw && raw && (
          <div className="fds-well fds-mono fds-wrap fds-split-right">
            <div className="fds-label" style={{ marginBottom: 6 }}>raw</div>
            {String(raw).split("\n").map((l, i) => <div className="fds-line" key={i}>{l || " "}</div>)}
          </div>
        )}
      </div>
      {raw && (
        <button type="button" className="fds-fold" onClick={() => setShowRaw((v) => !v)}>
          {showRaw ? "hide the raw result" : "show the raw result beside it"}
        </button>
      )}
    </>
  );
}

/* ---- accessibility snapshot (chrome-devtools take_snapshot) ------------- */

/** 259 calls in one measured session, all of them landing in a grey <pre>.
 *  The snapshot is a tree with a uid per node, and the uid is the only part the
 *  next tool call actually uses, so it gets its own colour and the depth gets
 *  a rule to follow. */
export function Snapshot({ text }) {
  const [open, setOpen] = useState(false);
  const lines = useMemo(() => String(text || "").split("\n"), [text]);
  const shown = open ? lines : lines.slice(0, FOLD_AT * 2);
  const hidden = lines.length - shown.length;
  return (
    <>
      <div className="fds-well fds-mono">
        {shown.map((raw, i) => {
          const indent = raw.length - raw.trimStart().length;
          const line = raw.trim();
          const m = /^(\S+)\s*(.*?)\s*(uid=[\w.-]+)?$/.exec(line) || [];
          return (
            <div className="fds-line" key={i} style={{ paddingLeft: Math.min(indent, 40) * 6 }}>
              <span style={{ color: "var(--fds-a4)" }}>{m[1] || line}</span>
              {m[2] ? <span style={{ color: "var(--fdx-text)" }}> {m[2]}</span> : null}
              {m[3] ? <span style={{ color: "var(--fds-a5)" }}> {m[3]}</span> : null}
            </div>
          );
        })}
      </div>
      <Fold hidden={hidden > 0 ? hidden : 0} onShow={() => setOpen(true)} label="nodes" />
    </>
  );
}

/* ---- todo list ---------------------------------------------------------- */

const TODO_MARK = { completed: ["done", "var(--fds-ok)"], in_progress: ["now", "var(--fds-warn)"], pending: ["next", "var(--fdx-text-muted)"] };

/** A checklist is a checklist. Rendering it as JSON made the one thing the
 *  reader wants, what is left, the hardest thing to see. */
export function TodoList({ todos }) {
  return (
    <div className="fds-well" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {todos.map((t, i) => {
        const [mark, color] = TODO_MARK[t.status] || TODO_MARK.pending;
        const done = t.status === "completed";
        return (
          <div key={i} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
            <span className="fds-label" style={{ color, flex: "none", width: 34 }}>{mark}</span>
            <span className="fds-mono" style={{ color: done ? "var(--fdx-text-muted)" : "var(--fdx-text)",
                                                textDecoration: done ? "line-through" : undefined }}>
              {t.content || t.activeForm || ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ---- code, coloured by what the file is -------------------------------- */

/** Read/Write/Edit of a real file. 1099 .py, 806 .md and 206 .xml in one
 *  measured session were all being drawn as undifferentiated grey. */
export function CodeBlock({ text, lang, startLine = 1, showLines = true }) {
  const [open, setOpen] = useState(false);
  const lines = useMemo(() => highlight(String(text || ""), lang), [text, lang]);
  const shown = open ? lines : lines.slice(0, FOLD_AT * 2);
  const hidden = lines.length - shown.length;
  return (
    <>
      <div className="fds-well fds-mono">
        {shown.map((toks, i) => (
          <div className="fds-line" key={i}>
            {showLines && <span className="fds-code-num">{startLine + i}</span>}
            {toks.length
              ? toks.map((tk, j) => (tk.c ? <span className={`tk-${tk.c}`} key={j}>{tk.t}</span> : <span key={j}>{tk.t}</span>))
              : " "}
          </div>
        ))}
      </div>
      <Fold hidden={hidden > 0 ? hidden : 0} onShow={() => setOpen(true)} />
    </>
  );
}
