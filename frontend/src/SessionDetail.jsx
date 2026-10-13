import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Virtuoso } from "react-virtuoso";
import { get, subscribe } from "./api.js";
import ToolCard from "./session/ToolCard.jsx";
import ToolMix from "./session/ToolMix.jsx";
import "./session/session.css";

/* ---- transcript shaping ------------------------------------------------ */
// Claude Code emits each tool result as its own *user*-role turn. Rendering it
// as a "You" bubble is misleading — the result belongs to the assistant's tool
// call. So we attach each tool_result to its originating tool_use block (by id)
// and drop turns that end up with nothing but tool results.
function mergeToolResults(rawTurns) {
  const results = {};
  for (const t of rawTurns || [])
    for (const b of t.blocks)
      if (b.type === "tool_result" && b.tool_use_id)
        // `ts` is the turn the result arrived in: paired with the tool_use's
        // own turn it is the only duration the transcript can give us.
        results[b.tool_use_id] = { content: b.content, is_error: b.is_error, ts: t.ts };

  const out = [];
  for (const t of rawTurns || []) {
    const blocks = [];
    for (const b of t.blocks) {
      if (b.type === "tool_result") continue; // folded into its tool_use below
      if (b.type === "tool_use") blocks.push({ ...b, result: results[b.id] || null });
      else blocks.push(b);
    }
    if (blocks.length) out.push({ ...t, blocks });
  }
  return out;
}

// Collapse consecutive turns from the same speaker (e.g. a run of tool calls)
// into one group, so the "You"/"Claude" header appears once per speaker turn
// rather than once per JSONL line.
function groupTurns(turns) {
  const groups = [];
  for (const t of turns || []) {
    const last = groups[groups.length - 1];
    const stamped = t.blocks.map((b) => (b._ts ? b : { ...b, _ts: t.ts }));
    if (last && last.role === t.role && last.is_meta === t.is_meta &&
        last.is_sidechain === t.is_sidechain) {
      last.blocks.push(...stamped);
      last.last_ts = t.ts || last.last_ts;
    } else {
      groups.push({
        // Identity of the group, stable across prepends — an array index is
        // not, and using one makes every loaded page remount the list.
        key: t.uuid || `${t.ts || ""}#${groups.length}`,
        role: t.role, is_meta: t.is_meta, is_sidechain: t.is_sidechain,
        ts: t.ts, last_ts: t.ts, blocks: stamped,
      });
    }
  }
  return groups;
}

/* ---- formatters -------------------------------------------------------- */
const fmtTs = (iso) =>
  iso ? new Date(iso).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }) : "";
const fmtClock = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
const fmtCompact = (n) => {
  const v = Number(n) || 0;
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
};
const fmtRange = (a, b) => {
  if (!a) return "";
  const da = new Date(a), db = b ? new Date(b) : null;
  const sameDay = db && da.toDateString() === db.toDateString();
  return sameDay
    ? `${fmtTs(a)} - ${new Date(b).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : `${fmtTs(a)}${b ? ` - ${fmtTs(b)}` : ""}`;
};

const isScreenshotTool = (name) => typeof name === "string" && name.includes("take_screenshot");

// A screenshot's saved path lives in the tool input; fall back to parsing the
// "Saved screenshot to <path>." result text.
function screenshotPath(b) {
  const fromInput = b.input?.filePath || b.input?.path;
  if (fromInput) return fromInput;
  const m = /(?:Saved screenshot to|screenshot to)\s+(\S+?\.(?:png|jpe?g|webp|gif))/i.exec(b.result?.content || "");
  return m ? m[1] : null;
}

// Best-effort one-line summary for a tool call header.
function toolSummary(name, input) {
  if (!input || typeof input !== "object") return "";
  const first = (v) => String(v ?? "").split("\n")[0];
  if (input.command) return first(input.command);
  if (input.file_path) return input.file_path;
  if (input.path) return input.path;
  if (input.filePath) return input.filePath;
  if (input.pattern) return `/${input.pattern}/`;
  if (input.url) return input.url;
  if (input.query) return first(input.query);
  if (input.description) return first(input.description);
  const k = Object.keys(input)[0];
  return k ? first(JSON.stringify(input[k])).slice(0, 90) : "";
}

/* ---- path-list parsing (Grep/Glob/Bash-grep → file tree) ---------------- */
// Extract file paths (+ per-file hit counts) from a tool result. Handles Grep
// files_with_matches (bare paths), content mode (path:line:…), Glob listings,
// and Bash `grep -rl` output.
function extractPaths(text) {
  const counts = new Map();
  if (!text) return counts;
  let lines = 0;
  for (let line of text.split("\n")) {
    line = line.trim();
    if (!line) continue;
    lines++;
    const m = /^((?:~?\/)?[\w.@\-/]+\/[\w.@\-]+?):\d+[:-]/.exec(line);
    let p = m ? m[1] : null;
    if (!p && /^(?:~?\/)?[\w.@\-/]+\/[\w.@\-]+$/.test(line)) p = line;
    if (p) counts.set(p, (counts.get(p) || 0) + 1);
  }
  // Only treat as a path listing when it dominates the output.
  if (counts.size < 2 || counts.size / Math.max(lines, 1) < 0.5) return new Map();
  return counts;
}

// counts(Map path→hits) → nested tree, single-child dir chains collapsed.
function buildTree(counts) {
  const root = { children: new Map(), count: 0 };
  for (const [p, n] of counts) {
    let node = root;
    for (const part of p.replace(/^\/+/, "").split("/")) {
      if (!node.children.has(part)) node.children.set(part, { children: new Map(), count: 0 });
      node = node.children.get(part);
    }
    node.count += n;
    node.isFile = true;
  }
  const collapse = (node) => {
    for (const [key, child] of [...node.children]) {
      collapse(child);
      if (child.children.size === 1 && !child.isFile) {
        const [[ck, cv]] = [...child.children];
        node.children.delete(key);
        node.children.set(`${key}/${ck}`, cv);
      }
    }
  };
  collapse(root);
  return root;
}

function TreeNode({ name, node, depth }) {
  const dirs = [...node.children].filter(([, c]) => !c.isFile || c.children.size);
  const files = [...node.children].filter(([, c]) => c.isFile && !c.children.size);
  return (
    <div style={{ paddingLeft: depth ? 14 : 0 }}>
      {name && (
        <div className="font-mono text-[12px] leading-relaxed text-zinc-500">
          {node.isFile && !node.children.size ? null : `${name}/`}
        </div>
      )}
      {dirs.map(([k, c]) => <TreeNode key={k} name={k} node={c} depth={depth + 1} />)}
      {files.map(([k, c]) => (
        <div key={k} style={{ paddingLeft: 14 }}
             className="font-mono text-[12px] leading-relaxed text-zinc-300">
          {k}
          {c.count > 1 && <span className="ml-2 rounded bg-emerald-500/10 px-1 text-[10px] text-emerald-400">{c.count}</span>}
        </div>
      ))}
    </div>
  );
}

function FileTree({ counts }) {
  const tree = useMemo(() => buildTree(counts), [counts]);
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  return (
    <div className="max-h-[24rem] overflow-auto rounded-lg bg-zinc-950/70 p-3">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {counts.size} file{counts.size > 1 ? "s" : ""}{total > counts.size ? ` · ${total} matches` : ""}
      </div>
      <TreeNode name="" node={tree} depth={0} />
    </div>
  );
}

/* ---- subagent threads ---------------------------------------------------- */
// Map of Agent tool_use id → subagent transcript, provided by SessionDetail.
// focusToolUseId / focusAgentId are set when the page was opened on a specific
// subagent (via a `<parent>~<agentId>` route) so it auto-expands + scrolls.
// Reading mode, shared with every row: focus folds the prose and opens every
// call, and `cursorKey` marks the call the keyboard is standing on.
const ReadCtx = React.createContext({ focusTools: false, cursorKey: null });

const SubagentsCtx = React.createContext({
  byToolUse: new Map(), orphans: [], focusToolUseId: null, focusAgentId: null,
  sessionId: null });

// Turns per page. The session payload carries this many turns anchored to the
// END of the transcript; earlier pages arrive as the reader scrolls into them.
const PAGE = 150;


const normText = (s) => String(s || "").replace(/\s+/g, " ").trim();

// Assign each subagent transcript to the Agent tool_use whose dispatch prompt
// matches its first user message. Order-based fallback pairs leftovers.
function assignSubagents(turns, subagents) {
  const agentCalls = [];
  const scan = (ts) => {
    for (const t of ts || [])
      for (const b of t.blocks || [])
        if (b.type === "tool_use" && b.name === "Agent") agentCalls.push(b);
  };
  scan(turns);
  for (const s of subagents || []) scan(s.turns);
  const byToolUse = new Map();
  const used = new Set();
  for (const call of agentCalls) {
    const prompt = normText(call.input?.prompt).slice(0, 100);
    if (!prompt) continue;
    const hit = (subagents || []).find((s, i) =>
      !used.has(i) && normText(s.dispatch).slice(0, 100).startsWith(prompt.slice(0, 60)));
    if (hit) { byToolUse.set(call.id, hit); used.add((subagents || []).indexOf(hit)); }
  }
  // leftovers in order
  const rest = (subagents || []).filter((_, i) => !used.has(i));
  for (const call of agentCalls) {
    if (byToolUse.has(call.id)) continue;
    const nxt = rest.shift();
    if (!nxt) break;
    byToolUse.set(call.id, nxt);
  }
  return { byToolUse, orphans: rest };
}

// Fetched threads, keyed by session+agent and shared across mounts. A live
// session pushes fresh metadata on every SSE tick, which rebuilds the subagent
// rows and remounts an open thread — without this the thread refetched itself
// on every tick. Storing the PROMISE also collapses two concurrent opens into
// one request.
const subagentCache = new Map();

function fetchSubagentThread(sessionId, agentId) {
  const key = `${sessionId}:${agentId}`;
  let hit = subagentCache.get(key);
  if (!hit) {
    hit = get(`/api/session/${encodeURIComponent(sessionId)}/subagent/${encodeURIComponent(agentId)}`);
    subagentCache.set(key, hit);
    hit.catch(() => subagentCache.delete(key));   // a failure stays retryable
  }
  return hit;
}

// A nested thread arrives as metadata only — its turns are fetched the first
// time someone opens it. 65 collapsed threads used to ship 10 MB of turns that
// nobody unfolded; now the session payload carries their headlines and each
// body costs one request, when asked for.
function SubagentThread({ sub, focus = false }) {
  const { sessionId } = React.useContext(SubagentsCtx);
  const [thread, setThread] = useState(sub.loaded === false ? null : sub);
  const [threadErr, setThreadErr] = useState(null);
  const inFlight = useRef(false);
  // Reset only when this row actually points at a different thread. Keying on
  // the `sub` object instead would drop a fetched thread on every live tick —
  // the metadata is rebuilt each time — and refetch it on the next render.
  useEffect(() => {
    setThread(sub.loaded === false ? null : sub);
  }, [sub.agent_id, sub.loaded]);   // eslint-disable-line react-hooks/exhaustive-deps
  const fetchThread = useCallback(() => {
    if (thread || inFlight.current || !sessionId) return;
    inFlight.current = true;
    fetchSubagentThread(sessionId, sub.agent_id)
      .then((d) => setThread(d))
      .catch((e) => setThreadErr(String(e.message || e)))
      .finally(() => { inFlight.current = false; });
  }, [thread, sessionId, sub.agent_id]);
  // Opened straight from a deep link: fetch without waiting for a click.
  useEffect(() => { if (focus) fetchThread(); }, [focus, fetchThread]);
  const groups = useMemo(() => groupTurns(mergeToolResults(thread?.turns)), [thread]);
  const ref = useRef(null);
  useEffect(() => {
    if (focus && ref.current) ref.current.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [focus]);
  return (
    <div ref={ref} className={focus ? "scroll-mt-4 rounded-xl ring-1 ring-amber-500/40" : ""}>
      <Collapsible
        icon="⤷"
        label={`subagent${sub.agent_type ? ` · ${sub.agent_type}` : ""}`}
        summary={`${sub.turn_count} turns - ${normText(sub.dispatch).slice(0, 90)}`}
        accent="amber"
        defaultOpen={focus}
        onOpen={fetchThread}
        className="mt-2 w-full"
      >
        <div className="ml-1 space-y-4 border-l-2 border-amber-500/25 pl-3">
          {!thread && !threadErr && (
            <p className="text-[11px] text-zinc-500">loading thread…</p>
          )}
          {threadErr && <p className="text-[11px] text-rose-400">{threadErr}</p>}
          {thread?.truncated && (
            <p className="text-[11px] text-amber-300/80">
              showing first {(thread.turns || []).length} of {thread.turn_count} turns
            </p>
          )}
          {groups.map((g) => <TurnGroup key={g.key} group={g} />)}
        </div>
      </Collapsible>
    </div>
  );
}

// A row taller than this is folded until asked for. Row heights measured on a
// real session: median 47px, p90 115px, max 2,661px. The virtualizer estimates
// an unmeasured row at the running mean (~94px), so drawing one 2,661px row
// moves the total size — and the text under the reader — by ~2,500px in a
// single frame. Bounding the rare giant is what makes the estimate usable;
// nothing else here touches 90% of rows.
const LONG_BLOCK_PX = 460;

function Clamped({ children }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (el) setLong(el.scrollHeight > LONG_BLOCK_PX + 48);
  }, [children]);
  return (
    <div className="w-full">
      <div ref={ref} style={open ? undefined : { maxHeight: LONG_BLOCK_PX, overflow: "hidden" }}>
        {children}
      </div>
      {long && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="mt-1 text-[11px] font-medium text-emerald-400/90 hover:text-emerald-300"
        >
          {open ? "fold this back" : "show the rest of this block"}
        </button>
      )}
    </div>
  );
}

const Markdown = ({ text, italic = false }) => (
  <div className={`md ${italic ? "italic text-zinc-400" : ""}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
  </div>
);

/* ---- collapsible ------------------------------------------------------- */
function Collapsible({ icon, label, summary, accent = "zinc", defaultOpen = false,
                      className = "", onOpen, children }) {
  const [open, setOpen] = useState(defaultOpen);
  // Fires only on the closed→open edge, so a lazily fetched body is requested
  // once, when it is first actually needed.
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) onOpen?.();
  };
  const ring = {
    zinc: "border-zinc-800 hover:border-zinc-700",
    violet: "border-violet-500/25 hover:border-violet-500/40",
    sky: "border-sky-500/25 hover:border-sky-500/40",
    rose: "border-rose-500/30 hover:border-rose-500/50",
    amber: "border-amber-500/30 hover:border-amber-500/50",
  }[accent] || "border-zinc-800";
  const labelColor = {
    zinc: "text-zinc-400", violet: "text-violet-300",
    sky: "text-sky-300", rose: "text-rose-300", amber: "text-amber-300",
  }[accent] || "text-zinc-400";
  return (
    <div className={`rounded-xl border bg-zinc-900/40 ${ring} transition-colors ${className}`}>
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left"
      >
        <span className={`text-[10px] transition-transform ${open ? "rotate-90" : ""} text-zinc-500`}>▶</span>
        <span className={`shrink-0 text-[11px] font-semibold uppercase tracking-wider ${labelColor}`}>
          {icon} {label}
        </span>
        {summary && !open && (
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-500">{summary}</span>
        )}
      </button>
      {open && <div className="border-t border-zinc-800/70 px-3.5 py-3">{children}</div>}
    </div>
  );
}

const Pre = ({ children }) => (
  <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-zinc-950/70 p-3 font-mono text-[12px] leading-relaxed text-zinc-300">
    {children}
  </pre>
);

function Screenshot({ path }) {
  const [broken, setBroken] = useState(false);
  const [loaded, setLoaded] = useState(false);
  if (!path) return null;
  const src = `/api/screenshot?path=${encodeURIComponent(path)}`;
  if (broken) {
    return <p className="font-mono text-xs text-zinc-600">🖼 screenshot file not found - {path}</p>;
  }
  // An image with no box reserved is 0px tall until it decodes, then suddenly
  // hundreds — inside a virtualized list that re-measure shoves every turn
  // below it, which reads as the text jumping. Hold a placeholder height until
  // it lands, and decode eagerly: virtualization already keeps far-off turns
  // unmounted, so `loading="lazy"` only delayed the images about to be read.
  // A FIXED box, not a max-height: the image then occupies the same space
  // before and after it decodes, so nothing below it moves. A growing image is
  // otherwise a 200-300px shove in the middle of the transcript, and the
  // virtualizer has no way to compensate for it. Click still opens full size.
  return (
    <a href={src} target="_blank" rel="noreferrer" title="Open full size"
       className="block h-[320px] w-full">
      <img
        src={src}
        alt="screenshot"
        onLoad={() => setLoaded(true)}
        onError={() => { setLoaded(true); setBroken(true); }}
        className={`h-full w-auto max-w-full rounded-lg border border-zinc-800 object-contain object-left ${
          loaded ? "" : "opacity-0"}`}
      />
    </a>
  );
}

/* ---- block renderers --------------------------------------------------- */
// Tools whose output is worth trying to render as a file tree.
const isSearchTool = (name) =>
  name === "Grep" || name === "Glob" ||
  (name === "Bash");  // Bash results are tree-rendered only if they parse as paths

function ToolUse({ b }) {
  const shot = isScreenshotTool(b.name);
  const shotPath = shot ? screenshotPath(b) : null;
  const { command, ...rest } = b.input || {};
  const r = b.result;
  const hasInput = command != null || Object.keys(b.input || {}).length > 0;
  const subMap = React.useContext(SubagentsCtx);
  const sub = b.name === "Agent" ? subMap.byToolUse.get(b.id) : null;
  // file-tree view of search results (Grep/Glob always try; Bash only when the
  // output actually looks like a path listing)
  const pathCounts = useMemo(
    () => (!r || r.is_error || !isSearchTool(b.name) ? new Map() : extractPaths(r.content)),
    [b.name, r]);
  const isEdit = b.name === "Edit" && (b.input?.old_string != null || b.input?.new_string != null);
  const focused = sub && subMap.focusToolUseId === b.id;

  return (
    <Collapsible
      icon={shot ? "📸" : b.name === "Agent" ? "🤖" : "🛠"}
      label={b.name || "tool"}
      summary={toolSummary(b.name, b.input)}
      accent={r?.is_error ? "rose" : "sky"}
      defaultOpen={Boolean(shotPath) || Boolean(focused)}
      className="w-full max-w-full"
    >
      {shotPath ? (
        <>
          <div className="mb-2 font-mono text-[11px] text-zinc-500">{shotPath}</div>
          <Screenshot path={shotPath} />
        </>
      ) : isEdit ? (
        <>
          <div className="mb-2 font-mono text-[11px] text-zinc-500">{b.input?.file_path}</div>
          {b.input?.old_string != null && (
            <pre className="mb-1.5 max-h-[16rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border-l-2 border-rose-500/50 bg-rose-500/[0.06] p-3 font-mono text-[12px] leading-relaxed text-rose-200/90">
              {b.input.old_string}
            </pre>
          )}
          {b.input?.new_string != null && (
            <pre className="max-h-[16rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border-l-2 border-emerald-500/50 bg-emerald-500/[0.06] p-3 font-mono text-[12px] leading-relaxed text-emerald-200/90">
              {b.input.new_string}
            </pre>
          )}
          {r?.is_error && (
            <div className="mt-2.5 border-t border-zinc-800/70 pt-2.5">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-rose-300">✕ error</div>
              <Pre>{r.content || "(empty)"}</Pre>
            </div>
          )}
        </>
      ) : (
        <>
          {command != null && <Pre>{String(command)}</Pre>}
          {Object.keys(rest).length > 0 && (
            <Pre>{JSON.stringify(command != null ? rest : b.input, null, 2)}</Pre>
          )}
          {!hasInput && <p className="text-xs text-zinc-600">(no input)</p>}
          {/* the tool's own output, folded in like Claude Code's UI */}
          {r ? (
            <div className="mt-2.5 border-t border-zinc-800/70 pt-2.5">
              <div className={`mb-1.5 text-[10px] font-semibold uppercase tracking-wider ${
                r.is_error ? "text-rose-300" : "text-zinc-500"
              }`}>
                {r.is_error ? "✕ error" : "↳ output"}
              </div>
              {pathCounts.size > 0 ? (
                <>
                  <FileTree counts={pathCounts} />
                  <Collapsible icon="↳" label="raw output" className="mt-2">
                    <Pre>{r.content || "(empty)"}</Pre>
                  </Collapsible>
                </>
              ) : (
                <Pre>{r.content || "(empty)"}</Pre>
              )}
            </div>
          ) : (
            <p className="mt-2 text-[11px] italic text-zinc-600">(no result captured)</p>
          )}
        </>
      )}
      {sub && <SubagentThread sub={sub} focus={Boolean(focused)} />}
    </Collapsible>
  );
}

function Block({ b, role, ts }) {
  const isUser = role === "user";
  const subMap = React.useContext(SubagentsCtx);
  const read = React.useContext(ReadCtx);
  if (b.type === "text") {
    // Injected context (IDE / command / system-reminder) — dim + collapsed.
    if (b.meta) {
      return (
        <Collapsible icon="⌗" label="injected context" summary="system / IDE / command context" className="w-full max-w-full">
          <Pre>{b.text}</Pre>
        </Collapsible>
      );
    }
    if (read.focusTools && !isUser) {
      const words = b.text.trim().split(/\s+/).length;
      return (
        <div className="fds-collapsed-prose fds-label">
          {words.toLocaleString()} words of prose hidden
        </div>
      );
    }
    return (
      <div className={`fds-read ${isUser ? "fds-prompt" : "fds-prose"}`}>
        <Clamped><Markdown text={b.text} /></Clamped>
      </div>
    );
  }
  if (b.type === "thinking") {
    // Extended thinking is usually persisted encrypted (signature only), so the
    // plaintext is empty — show a slim marker rather than an empty expander.
    if (!b.text || !b.text.trim()) {
      return <p className="fds-label">thinking · not recorded</p>;
    }
    if (read.focusTools) {
      return <div className="fds-collapsed-prose fds-label">thinking hidden</div>;
    }
    return (
      <div className="fds-read">
        <div className="fds-label" style={{ marginBottom: 4 }}>thinking</div>
        <div className="fds-think" style={{ borderLeft: "1px solid var(--fdx-rule)", paddingLeft: 12 }}>
          <Clamped><Markdown text={b.text} /></Clamped>
        </div>
      </div>
    );
  }
  if (b.type === "tool_use") {
    const sub = b.name === "Agent" ? subMap.byToolUse.get(b.id) : null;
    const focused = sub && subMap.focusToolUseId === b.id;
    return (
      <ToolCard
        block={b}
        startTs={ts || b._ts}
        defaultOpen={Boolean(focused) || read.focusTools}
        cursor={read.cursorKey === b.id}
        agent={sub ? <SubagentThread sub={sub} focus={Boolean(focused)} /> : null}
      />
    );
  }
  if (b.type === "tool_result") {
    // Fallback: an orphan result whose tool_use fell outside the window.
    return (
      <Collapsible icon={b.is_error ? "✕" : "↳"} label={b.is_error ? "error" : "result"}
                   summary={(b.content || "").split("\n")[0]} accent={b.is_error ? "rose" : "zinc"}
                   className="w-full max-w-full">
        <Pre>{b.content || "(empty)"}</Pre>
      </Collapsible>
    );
  }
  if (b.type === "image") {
    return <p className="text-xs italic text-zinc-500">🖼 [image omitted]</p>;
  }
  return null;
}

/* ---- turn group -------------------------------------------------------- */
// Rendered inside a react-virtuoso list, so it needs no anchor ref or
// content-visibility of its own: virtualization keeps off-screen turns out of
// the DOM entirely, and jump/scrollspy target group indices, not elements.
function SpeakerHeader({ group }) {
  const isUser = group.role === "user";
  return (
    <div className="flex items-center gap-8">
      <span className="fds-label" style={{ color: isUser ? "var(--fdx-signal-hover)" : "var(--fds-info)" }}>
        {isUser ? "You" : "Claude"}
      </span>
      {group.is_sidechain && <span className="fds-label" style={{ color: "var(--fds-warn)" }}>subagent</span>}
      <span className="fds-label">{fmtTs(group.ts)}</span>
    </div>
  );
}

// One list item = ONE block, not a whole speaker run.
//
// Merging a run of same-speaker turns into a single item produced items up to
// 4,495px tall (median 1,214px, measured on a real session) and left the
// virtualizer with ~8 items for a 150-turn window. Every time it measured one
// of those and replaced its estimate, the total size moved by thousands of
// pixels and the text jumped under the reader. Per-block rows keep the same
// look — the speaker header still appears once per run — while giving the list
// items whose height it can actually predict.
function TurnRow({ row }) {
  const { group, block, first } = row;
  const isUser = group.role === "user";
  if (group.is_meta) {
    return (
      <div className="mx-auto flex w-full max-w-full flex-col gap-2 opacity-70">
        <Block b={block} role={group.role} />
      </div>
    );
  }
  const isTool = block?.type === "tool_use";
  return (
    <div className="flex flex-col items-start gap-2"
         style={isUser ? { borderLeft: "2px solid var(--fdx-signal-hover)", paddingLeft: 14 } : undefined}>
      {first && <SpeakerHeader group={group} />}
      <div className={isTool ? "fds-indent" : "w-full"}>
        <Block b={block} role={group.role} />
      </div>
    </div>
  );
}

const TurnRowMemo = React.memo(TurnRow);

// Flatten groups into one row per block, remembering which group each row came
// from so clearance markers and jumps keep addressing groups.
function toRows(groups) {
  const rows = [];
  groups.forEach((g, gi) => {
    g.blocks.forEach((b, bi) => {
      rows.push({
        key: `${g.key}#${bi}`, group: g, block: b, gi,
        first: bi === 0, last: bi === g.blocks.length - 1,
      });
    });
  });
  return rows;
}

function TurnGroup({ group }) {
  const isUser = group.role === "user";
  const blocks = group.blocks.map((b, i) => <Block key={i} b={b} role={group.role} />);

  // Injected-context group: neither party's message — centered and dim.
  if (group.is_meta) {
    return <div className="mx-auto flex w-full max-w-full flex-col gap-2 opacity-70">{blocks}</div>;
  }

  return (
    <div className="flex flex-col items-start gap-2"
         style={isUser ? { borderLeft: "2px solid var(--fdx-signal-hover)", paddingLeft: 14 } : undefined}>
      <SpeakerHeader group={group} />
      {blocks}
    </div>
  );
}

/* ---- flow view ----------------------------------------------------------
   Linear diagram of the session: one row per user prompt / tool call, with
   in/out sizes at a glance; subagent steps indented under their Agent call.
   Click a tool row to expand the full ToolUse detail inline. */
function flowSteps(groups, byToolUse) {
  const steps = [];
  const walk = (gs, depth) => {
    for (const g of gs) {
      for (const b of g.blocks) {
        if (b.type === "text" && !b.meta && g.role === "user") {
          steps.push({ kind: "prompt", text: b.text, depth, ts: g.ts });
        } else if (b.type === "tool_use") {
          steps.push({ kind: "tool", b, depth, ts: g.ts });
          const sub = b.name === "Agent" ? byToolUse.get(b.id) : null;
          if (sub) {
            steps.push({ kind: "subagent", sub, depth: depth + 1 });
            walk(groupTurns(mergeToolResults(sub.turns)), depth + 1);
          }
        }
      }
    }
  };
  walk(groups, 0);
  return steps;
}

/* ---- clearance view -----------------------------------------------------
   A "clearance" is a real user-authored instruction: the one boundary that
   never multiplies when a subagent fans out into a dozen tool calls. Each
   clearance spans from its own prompt turn up to (not including) the next
   one, so a long burst of tool calls collapses into "one thing you asked
   for" rather than N anonymous turns. */

// The sticky page header occupies this much of the viewport; jump offsets and
// the scrollspy band are both derived from it so they never contradict.
// Flow and Clearance are parked while the reading view is rebuilt; both derive
// from `groups`, so flipping this back is all it takes to get them again.
const SHOW_LEGACY_VIEWS = false;
const CLR_HEADER_OFFSET = 132;

// A user text turn only starts a NEW clearance if it is a real instruction.
// Slash-command output and injected wrappers arrive as non-meta user turns too
// (e.g. "<local-command-stdout>Set model...", "<Caution>From now..."); counting
// those as clearances is exactly the noise this view exists to filter out, so
// their turns fold into the current clearance instead of starting a new one.
function isRealInstruction(text) {
  const t = (text || "").trim();
  if (!t) return false;
  if (/^<(local-command|command-name|command-message|command-args|local-command-std)/i.test(t)) return false;
  if (/^<caution\b/i.test(t)) return false;
  if (/^caveat:/i.test(t)) return false;
  if (/^\[request interrupted/i.test(t)) return false;
  if (/^<(system-reminder|task-notification|task-id|user-prompt-submit-hook)/i.test(t)) return false;
  return true;
}

function computeClearances(groups) {
  const out = [];
  let cur = null;
  let turnCursor = 0; // running grouped-turn ordinal, for the "turns X-Y" span
  groups.forEach((g, i) => {
    const promptBlock = g.role === "user" && !g.is_meta
      ? g.blocks.find((b) => b.type === "text" && !b.meta)
      : null;
    if (promptBlock && isRealInstruction(promptBlock.text)) {
      if (cur) { cur.endIndex = i; cur.endTurn = turnCursor; out.push(cur); }
      cur = {
        index: out.length, startIndex: i, endIndex: groups.length,
        startTurn: turnCursor + 1, endTurn: groups.length,
        ts: g.ts, label: normText(promptBlock.text).slice(0, 160),
        toolCount: 0, subagentCount: 0, errorCount: 0, turnCount: 0,
      };
    }
    if (cur) {
      cur.turnCount += 1;
      turnCursor += 1;
      for (const b of g.blocks) {
        if (b.type === "tool_use") {
          cur.toolCount += 1;
          if (b.name === "Agent") cur.subagentCount += 1;
          if (b.result?.is_error) cur.errorCount += 1;
        }
      }
    }
  });
  if (cur) { cur.endTurn = turnCursor; out.push(cur); }
  return out;
}

// Position each clearance along the route: pos (0..1) = fraction of the session
// elapsed at its start. Lets a fixed-width route line show the whole session.
function clearanceGeom(clearances) {
  const total = clearances.reduce((s, c) => s + c.turnCount, 0) || 1;
  let acc = 0;
  return clearances.map((c) => {
    const pos = acc / total;
    acc += c.turnCount;
    return pos;
  });
}

// Tool calls inside one clearance's span, consecutive repeats collapsed into a
// single "Name ×N" row (real aggregation). Carries is_error so a failed call is
// never hidden by the display cap, and subagent rows are flagged so they can be
// promoted above the cap.
function clearanceToolRows(groups, clearance, byToolUse) {
  if (!clearance) return [];
  const rows = [];
  for (let i = clearance.startIndex; i < clearance.endIndex; i++) {
    for (const b of groups[i].blocks) {
      if (b.type !== "tool_use") continue;
      if (b.name === "Agent") {
        const sub = byToolUse.get(b.id);
        rows.push({ kind: "subagent", label: sub?.agent_type || "subagent",
          detail: sub ? `${sub.turn_count} turns` : "" });
      } else {
        rows.push({ kind: "tool", name: b.name, summary: toolSummary(b.name, b.input),
          error: Boolean(b.result?.is_error) });
      }
    }
  }
  const collapsed = [];
  for (const r of rows) {
    const last = collapsed[collapsed.length - 1];
    if (r.kind === "tool" && last?.kind === "tool" && last.name === r.name && last.error === r.error) last.count += 1;
    else collapsed.push(r.kind === "tool" ? { ...r, count: 1 } : r);
  }
  return collapsed;
}

// Route line (fit-to-width): the whole session on one baseline, no scroll.
// Position along the line = progress through the session; tick height = tool
// intensity; the active station is a glowing coral dot. Every station is a
// hit target; in dense clusters the precise selector is the list below.
const CLR_TICK_MIN = 4;
const CLR_TICK_MAX = 26;

function ClearanceRoute({ clearances, positions, activeIdx, onJump, live }) {
  const maxTools = Math.max(1, ...clearances.map((c) => c.toolCount));
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-3 pt-4 pb-2">
      <div className="relative h-12 w-full" role="list" aria-label="Clearances">
        {/* baseline */}
        <div className="absolute inset-x-0 bottom-3 h-px bg-zinc-700/70" />
        {clearances.map((c, i) => {
          const active = i === activeIdx;
          const isLast = i === clearances.length - 1;
          const h = CLR_TICK_MIN + (c.toolCount / maxTools) * (CLR_TICK_MAX - CLR_TICK_MIN);
          const left = `${positions[i] * 100}%`;
          return (
            <button
              key={i} type="button" role="listitem" onClick={() => onJump(i)} title={`${i + 1}. ${c.label}`}
              aria-label={`Clearance ${i + 1}: ${c.label}`} aria-current={active ? "true" : undefined}
              className="group absolute bottom-0 flex -translate-x-1/2 flex-col items-center justify-end"
              style={{ left, height: "100%", width: 14, zIndex: active ? 3 : 1 }}
            >
              {/* tick (tool intensity) */}
              <span
                className={`absolute bottom-3 w-[2px] rounded-full transition-colors ${
                  active ? "bg-emerald-400" : c.errorCount ? "bg-rose-400/70" : "bg-zinc-600 group-hover:bg-zinc-400"}`}
                style={{ height: h }}
              />
              {/* station dot on the baseline */}
              <span
                className={`absolute bottom-3 h-2 w-2 -translate-y-1/2 translate-y-[4px] rounded-full border transition-colors ${
                  active
                    ? "border-emerald-400 bg-emerald-400 shadow-[0_0_7px_rgba(255,81,51,0.7)]"
                    : "border-zinc-600 bg-zinc-900 group-hover:border-zinc-400"} ${
                  active && live && isLast ? "animate-live-pulse" : ""}`}
              />
              {/* only the active station is labelled, to avoid 65 overlapping numbers */}
              {active && (
                <span className="absolute bottom-6 whitespace-nowrap font-mono text-[9px] font-semibold text-emerald-400">
                  {i + 1}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[9px] uppercase tracking-wider text-zinc-600">
        <span>start</span>
        <span>{clearances.length} clearances</span>
        <span>now</span>
      </div>
    </div>
  );
}

function ApproachRow({ r }) {
  if (r.kind === "subagent") {
    return (
      <div className="flex items-baseline gap-2 font-mono text-[11px]">
        <span className="shrink-0 text-amber-400">🤖 {r.label}</span>
        <span className="text-zinc-600">{r.detail}</span>
      </div>
    );
  }
  return (
    <div className="flex items-baseline gap-2 font-mono text-[11px]">
      <span className={`shrink-0 ${r.error ? "text-rose-400" : "text-zinc-300"}`}>{r.name}</span>
      {r.count > 1 && <span className="shrink-0 text-zinc-600">×{r.count}</span>}
      {r.error && <span className="shrink-0 rounded bg-rose-500/10 px-1 text-[9px] text-rose-400">error</span>}
      <span className="min-w-0 flex-1 truncate text-zinc-600">{r.summary}</span>
    </div>
  );
}

// The card has a FIXED height, so APPROACH is a scroll region that fills the
// remaining space rather than a cap+toggle that changes the card's height when
// you switch clearances. Subagents and errors are ordered first so they are
// never scrolled out of the initial view.
function ApproachRows({ rows }) {
  const ordered = useMemo(() => {
    const important = rows.filter((r) => r.kind === "subagent" || r.error);
    const normal = rows.filter((r) => r.kind !== "subagent" && !r.error);
    return [...important, ...normal];
  }, [rows]);
  return (
    <div className="mt-3 flex min-h-0 flex-1 flex-col border-t border-zinc-800/70 pt-3">
      <div className="mb-1 flex shrink-0 items-baseline justify-between text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        <span>Approach</span>
        <span className="font-mono">{rows.length}</span>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {ordered.map((r, i) => <ApproachRow key={i} r={r} />)}
      </div>
    </div>
  );
}

function ClearancePanel({ clearances, positions, activeIdx, onJump, groups, byToolUse, live }) {
  const active = clearances[activeIdx] || clearances[0] || null;
  const rows = useMemo(() => clearanceToolRows(groups, active, byToolUse), [groups, active, byToolUse]);
  const listRef = useRef(null);
  const activeRowRef = useRef(null);

  // Keep the active row visible in the list WITHOUT scrolling the page: adjust
  // only the list container's own scrollTop.
  useEffect(() => {
    const el = activeRowRef.current, cont = listRef.current;
    if (!el || !cont) return;
    const top = el.offsetTop, bottom = top + el.offsetHeight;
    if (top < cont.scrollTop) cont.scrollTop = top;
    else if (bottom > cont.scrollTop + cont.clientHeight) cont.scrollTop = bottom - cont.clientHeight;
  }, [activeIdx]);

  if (clearances.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 text-sm text-zinc-500">
        No user instructions found in this transcript.
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-4">
      <ClearanceRoute clearances={clearances} positions={positions} activeIdx={activeIdx} onJump={onJump} live={live} />

      {active && (
        /* double-bezel shell; FIXED height so switching clearances never
           resizes the card (APPROACH scrolls inside, label clamps to 2 lines) */
        <div className="rounded-2xl border border-zinc-800/80 bg-white/[0.03] p-1.5">
          <div className="flex h-[264px] flex-col overflow-hidden rounded-[calc(1rem-0.375rem)] bg-zinc-900/60 p-4 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)]">
            <div className="shrink-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                  Clearance {active.index + 1} of {clearances.length}
                </span>
                <span className="font-mono text-[10px] text-zinc-600">turns {active.startTurn}-{active.endTurn}</span>
              </div>
              <p className="mt-1.5 line-clamp-2 text-sm leading-snug text-zinc-100" title={active.label}>{active.label}</p>
              <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-zinc-500">
                <span>{active.turnCount} turns</span>
                <span>{active.toolCount} tool calls</span>
                {active.subagentCount > 0 && (
                  <span className="text-amber-400">{active.subagentCount} subagent{active.subagentCount > 1 ? "s" : ""}</span>
                )}
                {active.errorCount > 0 && (
                  <span className="text-rose-400">{active.errorCount} error{active.errorCount > 1 ? "s" : ""}</span>
                )}
                <span>{fmtTs(active.ts)}</span>
              </div>
            </div>
            {rows.length > 0
              ? <ApproachRows rows={rows} />
              : <div className="mt-3 flex flex-1 items-center justify-center border-t border-zinc-800/70 pt-3 font-mono text-[11px] text-zinc-600">no tool calls</div>}
          </div>
        </div>
      )}

      <div>
        <div className="mb-1.5 flex items-baseline justify-between font-mono text-[10px] uppercase tracking-wider text-zinc-500">
          <span>All clearances</span><span>{clearances.length}</span>
        </div>
        <div ref={listRef} className="relative h-[38vh] overflow-y-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
          {/* newest clearance first; the real index is preserved for jump/number/active */}
          {clearances.map((c, i) => ({ c, i })).reverse().map(({ c, i }) => {
            const on = i === activeIdx;
            return (
              <button
                key={i} type="button" onClick={() => onJump(i)} aria-current={on ? "true" : undefined}
                ref={on ? activeRowRef : undefined}
                className={`flex w-full items-baseline gap-2 border-t border-l-2 border-t-zinc-800/60 px-3 py-2 text-left transition-colors first:border-t-0 ${
                  on ? "border-l-emerald-400 bg-emerald-500/[0.08]" : "border-l-transparent hover:bg-zinc-800/40"}`}
              >
                <span className="shrink-0 font-mono text-[10px] text-zinc-600">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className={`block truncate text-xs ${on ? "text-emerald-300" : "text-zinc-300"}`}>{c.label}</span>
                  <span className="mt-0.5 block font-mono text-[10px] text-zinc-600">
                    {c.turnCount} turns / {c.toolCount} calls{c.subagentCount ? ` / ${c.subagentCount} sub` : ""}{c.errorCount ? ` / ${c.errorCount} err` : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function FlowRow({ step, n }) {
  const [open, setOpen] = useState(false);
  const pad = { paddingLeft: `${step.depth * 26}px` };
  if (step.kind === "prompt") {
    return (
      <div style={pad} className="flex items-baseline gap-2 py-1">
        <span className="w-7 shrink-0 text-right font-mono text-[10px] text-zinc-600">{n}</span>
        <span className="rounded bg-emerald-500/15 px-1.5 text-[10px] font-semibold uppercase text-emerald-400">you</span>
        <span className="min-w-0 truncate text-xs text-zinc-300">{normText(step.text).slice(0, 130)}</span>
      </div>
    );
  }
  if (step.kind === "subagent") {
    return (
      <div style={pad} className="flex items-baseline gap-2 py-1">
        <span className="w-7 shrink-0" />
        <span className="rounded bg-amber-500/10 px-1.5 text-[10px] font-semibold uppercase text-amber-400">
          ⤷ subagent{step.sub.agent_type ? ` · ${step.sub.agent_type}` : ""}
        </span>
        <span className="font-mono text-[11px] text-zinc-500">{step.sub.turn_count} turns</span>
      </div>
    );
  }
  const b = step.b;
  const r = b.result;
  const outChars = (r?.content || "").length;
  const paths = isSearchTool(b.name) && r && !r.is_error ? extractPaths(r.content) : new Map();
  const shot = isScreenshotTool(b.name);
  return (
    <div style={pad}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-baseline gap-2 rounded px-0 py-1 text-left hover:bg-zinc-800/30"
      >
        <span className="w-7 shrink-0 text-right font-mono text-[10px] text-zinc-600">{n}</span>
        <span className={`shrink-0 font-mono text-[11px] font-semibold ${r?.is_error ? "text-rose-400" : "text-sky-300"}`}>
          {shot ? "📸" : b.name === "Agent" ? "🤖" : "·"} {b.name}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500">
          {toolSummary(b.name, b.input)}
        </span>
        {paths.size > 0 && (
          <span className="shrink-0 rounded bg-emerald-500/10 px-1.5 text-[10px] text-emerald-400">{paths.size} files</span>
        )}
        {r?.is_error && <span className="shrink-0 rounded bg-rose-500/10 px-1.5 text-[10px] text-rose-400">error</span>}
        <span className="shrink-0 font-mono text-[10px] text-zinc-600">
          {outChars ? `${outChars >= 1000 ? `${(outChars / 1000).toFixed(1)}k` : outChars} ch` : ""}
        </span>
      </button>
      {open && <div className="mb-2 mt-1"><ToolUse b={b} /></div>}
    </div>
  );
}

function FlowView({ groups, byToolUse }) {
  const steps = useMemo(() => flowSteps(groups, byToolUse), [groups, byToolUse]);
  let n = 0;
  return (
    <div className="rounded-2xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3">
      {steps.map((s, i) => {
        if (s.kind !== "subagent") n += 1;
        return <FlowRow key={i} step={s} n={s.kind === "subagent" ? "" : n} />;
      })}
      {steps.length === 0 && <p className="py-6 text-center text-sm text-zinc-500">No steps.</p>}
    </div>
  );
}

/* ---- page -------------------------------------------------------------- */
export default function SessionDetail({ sessionId, onBack, initialView }) {
  // A subagent is addressed as `<parentId>~<agentId>`: load the PARENT
  // transcript (so the main agent's chat turns are all present) and focus the
  // chosen subagent thread (auto-expanded + scrolled into view).
  const sep = sessionId.indexOf("~");
  const parentId = sep === -1 ? sessionId : sessionId.slice(0, sep);
  const focusAgent = sep === -1 ? null : sessionId.slice(sep + 1);
  const [data, setData] = useState(null);   // header + subagent metadata
  // Turns and the groups derived from them move together, in one commit.
  //
  // `firstItemIndex` — Virtuoso's own prepend mechanism — is deliberately NOT
  // used: with `useWindowScroll` it fights the page scroll after a prepend and
  // leaves the render range desynced from the scroll offset, which shows up as
  // a viewport of blank space (measured: coverage 1.0 before a prepend, 0.0
  // after). We prepend plainly and re-anchor with `scrollToIndex` instead —
  // `startReached` only fires at the very top, so the item that was index 0 is
  // index `gained` afterwards, and putting it back at the top is exact.
  const [win, setWin] = useState({ turns: [], groups: [], rows: [] });
  const [winOffset, setWinOffset] = useState(0);   // window start within the session
  const [hasBefore, setHasBefore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const { turns, groups, rows } = win;
  const chatRef = useRef(null);          // the chat-view virtualized transcript
  const restoreIndex = useRef(null);     // groups gained by the last prepend
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [focusTools, setFocusTools] = useState(false);
  const [view, setView] = useState(
    initialView === "clearance" || initialView === "flow" ? initialView : "chat"
  ); // chat | flow | clearance
  const subAssign = useMemo(
    () => assignSubagents(turns, data?.subagents), [turns, data]);
  const clearances = useMemo(() => computeClearances(groups), [groups]);
  const clearancePositions = useMemo(() => clearanceGeom(clearances), [clearances]);
  const rowOfGroup = useMemo(() => {
    const m = new Map();
    rows.forEach((r, i) => { if (!m.has(r.gi)) m.set(r.gi, i); });
    return m;
  }, [rows]);
  const clearanceStartMap = useMemo(() => {
    const m = new Map();
    clearances.forEach((c, i) => m.set(c.startIndex, i));
    return m;
  }, [clearances]);
  const virtuosoRef = useRef(null);       // the clearance-view virtualized transcript
  const chatColRef = useRef(null);        // wrapper, for reading rendered clearance-start markers
  const [activeClearance, setActiveClearance] = useState(0);
  const jumpingRef = useRef(false);       // suppress scrollspy during a programmatic jump
  useEffect(() => { setActiveClearance(0); }, [sessionId]);
  // Scrollspy: the active clearance is the one you are currently reading, i.e.
  // the last clearance whose start marker has crossed above the header line.
  // We read it from the rendered [data-cstart] markers (virtualization keeps
  // only visible ones in the DOM); when none is above the line, we are deep
  // inside a long clearance whose start scrolled off - keep the last active.
  const recomputeActive = useCallback(() => {
    if (jumpingRef.current) return;
    const nodes = chatColRef.current?.querySelectorAll("[data-cstart]");
    if (!nodes || !nodes.length) return;
    let best = null;
    nodes.forEach((n) => {
      if (n.getBoundingClientRect().top <= CLR_HEADER_OFFSET + 6) {
        const idx = Number(n.dataset.cstart);
        if (best === null || idx > best) best = idx;
      }
    });
    if (best !== null) setActiveClearance(best);
  }, []);
  // Jump: ask the virtualizer to scroll to the clearance's first group. O(1)
  // regardless of session length - no walking the turns in between, which is
  // what used to lag. The negative offset leaves room for the sticky header so
  // the prompt lands just below it, not hidden under it.
  const jumpToClearance = useCallback((idx) => {
    setActiveClearance(idx);
    const c = clearances[idx];
    if (!c || !virtuosoRef.current) return;
    jumpingRef.current = true;
    // `scrollToIndex` addresses the DATA array — verified in the browser.
    // Animate the travel, then land exactly. The smooth pass commits to an
    // offset computed from ESTIMATED row heights; rows measured during the
    // animation move the target, so a jump would stop 300-500px short of the
    // prompt. The follow-up settles on the real offset once they are measured.
    const target = rowOfGroup.get(c.startIndex) ?? 0;
    virtuosoRef.current.scrollToIndex({ index: target, align: "start", behavior: "smooth" });
    // Then land on the real element. `scrollToIndex` aims at an offset derived
    // from estimated row heights and stops a few hundred pixels off; once the
    // row is actually in the DOM its own `scrollIntoView` is exact.
    clearTimeout(jumpToClearance._settle);
    jumpToClearance._settle = setTimeout(() => {
      const el = chatColRef.current?.querySelector(`[data-cstart="${idx}"]`);
      if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
      else virtuosoRef.current?.scrollToIndex({ index: target, align: "start" });
    }, 420);
    clearTimeout(jumpToClearance._t);
    jumpToClearance._t = setTimeout(() => { jumpingRef.current = false; }, 900);
  }, [clearances, rowOfGroup]);
  // What the footer lists. Fully loaded: the genuine orphans — threads whose
  // Agent call is nowhere in the transcript. Windowed: only a deep-linked
  // thread, so `#/session/<id>~<agent>` still opens one whose Agent call has
  // not been scrolled into yet, without listing all 65 as "orphans".
  const footerSubs = useMemo(() => {
    if (!hasBefore) return subAssign.orphans;
    if (!focusAgent) return [];
    const hit = (data?.subagents || []).find((s) => s.agent_id === focusAgent);
    if (!hit) return [];
    const inline = [...subAssign.byToolUse.values()].some((s) => s.agent_id === focusAgent);
    return inline ? [] : [hit];
  }, [hasBefore, subAssign, focusAgent, data]);
  const focusToolUseId = useMemo(() => {
    if (!focusAgent) return null;
    for (const [id, sub] of subAssign.byToolUse) if (sub.agent_id === focusAgent) return id;
    return null;
  }, [subAssign, focusAgent]);
  const focusSub = useMemo(
    () => (data?.subagents || []).find((s) => s.agent_id === focusAgent) || null,
    [data, focusAgent]);
  const ctx = useMemo(
    () => ({ ...subAssign, focusAgentId: focusAgent, focusToolUseId, sessionId: parentId }),
    [subAssign, focusAgent, focusToolUseId, parentId]);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const olderInFlight = useRef(false);
  // One place that turns a turn list into rendered groups, so every path below
  // produces `turns` and `groups` together.
  const shape = (list) => {
    const groups = groupTurns(mergeToolResults(list));
    return { turns: list, groups, rows: toRows(groups) };
  };
  // Same as `shape`, but keeps the identity of every row that did not change.
  // A live session re-shapes on each SSE tick; with fresh objects every rendered
  // row re-renders and gets re-measured, which moves the text while you read.
  const reshape = (prev, list) => {
    const next = shape(list);
    const n = Math.min(next.rows.length, prev.rows.length);
    for (let i = 0; i < n; i++) {
      if (next.rows[i].key !== prev.rows[i].key) break;
      next.rows[i] = prev.rows[i];
    }
    return next;
  };

  // First paint lands on the END of the session: `anchor=tail` returns the last
  // PAGE turns whatever its size, so a 38k-turn transcript costs what a 40-turn
  // one costs. Reading it from the head meant a 15 MB payload that still could
  // not reach the last turn.
  const load = useCallback(() => {
    return get(`/api/session/${encodeURIComponent(parentId)}?anchor=tail&limit=${PAGE}`)
      .then((d) => {
        if (!mounted.current) return;
        setData(d);
        restoreIndex.current = null;
        setWin(shape(d.turns || []));
        setWinOffset(d.offset || 0);
        setHasBefore(Boolean(d.has_before));
        setErr(null);
      })
      .catch((e) => { if (mounted.current) setErr(String(e.message || e)); })
      .finally(() => { if (mounted.current) setLoading(false); });
  }, [parentId]);

  // Reached the top of what is loaded → fetch the page before it and prepend.
  const loadOlder = useCallback(() => {
    if (olderInFlight.current || !hasBefore || winOffset <= 0) return;
    olderInFlight.current = true;
    setLoadingMore(true);
    const start = Math.max(0, winOffset - PAGE);
    get(`/api/session/${encodeURIComponent(parentId)}?offset=${start}&limit=${winOffset - start}`)
      .then((d) => {
        if (!mounted.current) return;
        const older = d.turns || [];
        if (older.length) {
          setWin((prev) => {
            const next = shape([...older, ...prev.turns]);
            // Grouping merges a run of same-speaker turns, so the number of
            // groups gained is not the number of turns fetched — and it is the
            // group count that the list indexes by.
            // measured in ROWS — that is what the list indexes by
            restoreIndex.current = Math.max(0, next.rows.length - prev.rows.length);
            return next;
          });
        }
        setWinOffset(start);
        setHasBefore(start > 0);
      })
      .catch((e) => { if (mounted.current) setErr(String(e.message || e)); })
      .finally(() => {
        olderInFlight.current = false;
        if (mounted.current) setLoadingMore(false);
      });
  }, [parentId, winOffset, hasBefore]);

  // Live-follow: only the tail moves, so re-fetch only the tail page and append
  // what we have not seen. Re-fetching the whole transcript on every file event
  // is what made a long live session unreadable. An append needs no
  // re-anchoring — only a prepend moves the indices under the reader.
  const loadTail = useCallback(() => {
    return get(`/api/session/${encodeURIComponent(parentId)}?anchor=tail&limit=${PAGE}`)
      .then((d) => {
        if (!mounted.current) return;
        setData(d);
        setWin((prev) => {
          const fresh = d.turns || [];
          if (!prev.turns.length) return shape(fresh);
          const seen = new Set(prev.turns.map((t) => t.uuid));
          // No overlap means the tail ran past the loaded window while the
          // reader sat further back — leave their position alone.
          if (!fresh.some((t) => seen.has(t.uuid))) return prev;
          const add = fresh.filter((t) => !seen.has(t.uuid));
          if (!add.length) return prev;
          return reshape(prev, [...prev.turns, ...add]);
        });
      })
      .catch(() => { /* a failed tick is not worth breaking the view over */ });
  }, [parentId]);

  // Header / Footer are memoized because Virtuoso keys them by component
  // IDENTITY: an object literal in the props makes a new component type each
  // render, remounting the footer — and with it the lazily fetched subagent
  // thread, which then refetched itself on every render.
  const listComponents = useMemo(() => ({
    Header: hasBefore
      ? () => (
          <div className="pb-5 text-center text-xs text-zinc-500">
            {loadingMore
              ? "loading earlier turns…"
              : `${winOffset.toLocaleString()} earlier turns — scroll up to load`}
          </div>
        )
      : undefined,
    // "Not linked to a visible Agent call" only means something once every turn
    // is loaded — with a window open, most Agent calls simply have not been
    // fetched yet, and listing their threads as orphans would bury the
    // transcript under 65 of them.
    Footer: footerSubs.length
      ? () => (
          <div className="space-y-2 border-t border-zinc-800/70 pt-4">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
              {hasBefore
                ? "Linked subagent (its Agent call is outside the loaded turns)"
                : "Subagents (not linked to a visible Agent call)"}
            </p>
            {footerSubs.map((sub) => (
              <SubagentThread key={sub.agent_id} sub={sub} focus={focusAgent === sub.agent_id} />
            ))}
          </div>
        )
      : undefined,
  }), [hasBefore, loadingMore, winOffset, footerSubs, focusAgent]);

  // Every tool call in the loaded window, in order. The keyboard walks this
  // list; J/K move the cursor and scroll the row into view.
  const [errorsOnly, setErrorsOnly] = useState(false);
  // Machine output scrolls sideways by default because columns line up that
  // way. A long command or a wide log is the opposite case, so the reader can
  // flip the whole surface to wrapping and back.
  const [wrapAll, setWrapAll] = useState(false);
  const [filters, setFilters] = useState({ prose: true, thinking: true, tools: true });
  // Errors-only keeps the prompts as landmarks: a list of failures with no idea
  // which instruction caused them is not worth reading. The chips are subtractive
  // on top of it, and a filter that would empty the stream is ignored.
  const viewRows = useMemo(() => {
    let keep = rows;
    if (errorsOnly) {
      keep = keep.filter((r) =>
        (r.block?.type === "tool_use" && r.block?.result?.is_error) || r.group?.role === "user");
    }
    if (!filters.prose) keep = keep.filter((r) => !(r.block?.type === "text" && r.group?.role !== "user"));
    if (!filters.thinking) keep = keep.filter((r) => r.block?.type !== "thinking");
    if (!filters.tools) keep = keep.filter((r) => r.block?.type !== "tool_use");
    return keep.length ? keep : rows;
  }, [rows, errorsOnly, filters]);
  // Group index -> its first row IN THE FILTERED LIST, so a jump lands right
  // even when chips are on.
  const viewRowOfGroup = useMemo(() => {
    const m = new Map();
    viewRows.forEach((r, i) => { if (!m.has(r.gi)) m.set(r.gi, i); });
    return m;
  }, [viewRows]);
  const prompts = clearances;
  const promptRowIndex = clearanceStartMap;
  const [activePrompt, setActivePrompt] = useState(0);
  // Which prompt is being read. Not from `rangeChanged`: that reports the
  // RENDERED range, which `increaseViewportBy` pushes 4000px above the viewport,
  // so it would always name a prompt the reader has already scrolled past. Read
  // the markers actually on screen instead.
  const jumpingPrompt = useRef(false);
  const recomputePrompt = useCallback(() => {
    if (jumpingPrompt.current) return;
    const host = chatColRef.current;
    const scroller = host?.querySelector("[data-testid='virtuoso-scroller']") || host;
    if (!scroller) return;
    const top = scroller.getBoundingClientRect().top;
    let best = null;
    host.querySelectorAll("[data-cstart]").forEach((n) => {
      if (n.getBoundingClientRect().top <= top + 8) {
        const idx = Number(n.dataset.cstart);
        if (best === null || idx > best) best = idx;
      }
    });
    if (best !== null) setActivePrompt(best);
  }, []);
  // Land on the END of what that prompt produced, not its beginning. The answer
  // is the last thing in the run; opening at the top means scrolling past every
  // tool call to find out how it turned out.
  const jumpToPrompt = useCallback((i) => {
    const c = prompts[i];
    if (!c) return;
    const start = viewRowOfGroup.get(c.startIndex);
    if (start === undefined) return;
    const next = prompts[i + 1] ? viewRowOfGroup.get(prompts[i + 1].startIndex) : undefined;
    const end = (next === undefined ? viewRows.length : next) - 1;
    setActivePrompt(i);
    jumpingPrompt.current = true;
    chatRef.current?.scrollToIndex({ index: Math.max(start, end), align: "end", behavior: "smooth" });
    clearTimeout(jumpToPrompt._settle);
    jumpToPrompt._settle = setTimeout(() => {
      chatRef.current?.scrollToIndex({ index: Math.max(start, end), align: "end" });
      setTimeout(() => { jumpingPrompt.current = false; }, 500);
    }, 420);
  }, [prompts, viewRowOfGroup, viewRows.length]);

  // One panel, three tabs. Two rails cost 520px of a 1900px screen to show
  // fifteen numbers; the stream is the thing being read.
  const PANELS = ["prompts", "session", "tools"];
  const [panel, setPanel] = useState("prompts");
  useEffect(() => {
    const onTab = (e) => {
      if (e.key !== "Tab" || e.metaKey || e.ctrlKey || e.altKey) return;
      // Only when nothing is focused. Stealing Tab from a focused control would
      // break keyboard navigation of the page to save one click.
      if (document.activeElement && document.activeElement !== document.body) return;
      e.preventDefault();
      setPanel((p) => PANELS[(PANELS.indexOf(p) + (e.shiftKey ? PANELS.length - 1 : 1)) % PANELS.length]);
    };
    window.addEventListener("keydown", onTab);
    return () => window.removeEventListener("keydown", onTab);
  }, []);

  // The shape of the work for the WHOLE session, not the loaded window: the
  // ledger already has one row per tool_use.
  const [toolMix, setToolMix] = useState(null);
  useEffect(() => {
    setToolMix(null);
    get(`/api/session/${encodeURIComponent(parentId)}/tools`)
      .then((t) => { if (mounted.current) setToolMix(t); })
      .catch(() => { /* a session ingested before tool_calls existed has none */ });
  }, [parentId]);

  // Tokens and cost are not in the transcript; they are in the ledger, which
  // aggregates per session anyway.
  const [usage, setUsage] = useState(null);
  useEffect(() => {
    setUsage(null);
    get(`/api/session/${encodeURIComponent(parentId)}/usage`)
      .then((u) => { if (mounted.current) setUsage(u); })
      .catch(() => { /* a session with no ledger row still reads fine */ });
  }, [parentId]);
  const readings = useMemo(() => {
    const calls = rows.filter((r) => r.block?.type === "tool_use").length;
    const errs = rows.filter((r) => r.block?.result?.is_error).length;
    const out = [];
    if (usage) {
      out.push({ label: "tokens", value: fmtCompact(usage.context) });
      out.push({ label: "cost", value: `$${(usage.cost || 0).toFixed(2)}` });
    }
    out.push({ label: "tool calls", value: calls.toLocaleString() });
    out.push({ label: "errors", value: String(errs), tone: errs ? "var(--fds-err)" : undefined });
    out.push({ label: "loaded", value: `${turns.length} / ${Number(data?.turn_count || 0).toLocaleString()}` });
    return out;
  }, [usage, rows, turns.length, data]);
  const toolRows = useMemo(
    () => viewRows.map((r, i) => ({ i, b: r.block }))
                  .filter(({ b }) => b?.type === "tool_use"), [viewRows]);
  const [cursor, setCursor] = useState(-1);
  const cursorKey = cursor >= 0 && toolRows[cursor] ? toolRows[cursor].b.id : null;
  const stepTool = useCallback((delta) => {
    if (!toolRows.length) return;
    setCursor((c) => {
      const next = Math.max(0, Math.min(toolRows.length - 1, (c < 0 ? (delta > 0 ? -1 : toolRows.length) : c) + delta));
      chatRef.current?.scrollToIndex({ index: toolRows[next].i, align: "center" });
      return next;
    });
  }, [toolRows]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const k = e.key.toLowerCase();
      if (k === "f") { setFocusTools((f) => !f); e.preventDefault(); }
      else if (k === "w") { setWrapAll((v) => !v); e.preventDefault(); }
      else if (k === "j") { stepTool(1); e.preventDefault(); }
      else if (k === "k") { stepTool(-1); e.preventDefault(); }
      else if (k === "e") { setErrorsOnly((v) => !v); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stepTool]);
  const readCtx = useMemo(() => ({ focusTools, cursorKey }), [focusTools, cursorKey]);

  // Put the reading position back after a prepend: the group that was at the
  // top is now `gained` items further down.
  useEffect(() => {
    const gained = restoreIndex.current;
    if (gained == null) return;
    restoreIndex.current = null;
    if (gained <= 0) return;
    const list = view === "clearance" ? virtuosoRef.current : chatRef.current;
    requestAnimationFrame(() => list?.scrollToIndex({ index: gained, align: "start" }));
  }, [groups, view]);

  // (Re)load when the viewed session changes.
  useEffect(() => {
    setLoading(true);
    setData(null);
    setWin({ turns: [], groups: [], rows: [] });
    setErr(null);
    load();
  }, [sessionId, load]);

  useEffect(() => subscribe(() => loadTail(), setLive), [loadTail]);

  return (
    <div className={`fds fds-page ${wrapAll ? "fds-wrap-all" : ""}`}>
      <header className="fds-header">
        <div className="fds-hrow">
          <button type="button" onClick={onBack} className="fds-label fds-back">← logbook</button>
          <span className="fds-live" title="Auto-updates as the session file changes">
            <span className="fds-live-dot" data-on={live ? "true" : "false"} />
            <span className="fds-label">{live ? "following" : "offline"}</span>
          </span>
        </div>

        {data && (
          <div className="fds-hrow fds-hrow-title">
            <div className="min-w-0">
              <h1 className="fds-title">{data.title || "Untitled session"}</h1>
              <div className="fds-meta">
                <span>{(data.session_id || "").slice(0, 8)}</span>
                {data.project && <><i>·</i><span>{data.project}</span></>}
                {data.git_branch && <><i>·</i><span>{data.git_branch}</span></>}
                <i>·</i><span>{fmtRange(data.first_ts, data.last_ts)}</span>
                <i>·</i><span>{Number(data.turn_count || 0).toLocaleString()} turns</span>
                {(data.subagents || []).length > 0 && (
                  <><i>·</i><span className="fds-meta-hot">
                    {data.subagents.length} subagent{data.subagents.length > 1 ? "s" : ""}
                  </span></>
                )}
                {data.version && <><i>·</i><span>v{data.version}</span></>}
              </div>
            </div>
            <div className="fds-views" role="group" aria-label="View">
              {["chat", ...(SHOW_LEGACY_VIEWS ? ["flow", "clearance"] : [])].map((v) => (
                <button key={v} type="button" className="fds-pill" data-on={view === v ? "true" : "false"}
                        aria-pressed={view === v} onClick={() => setView(v)}>
                  {v === "chat" ? "read" : v}
                </button>
              ))}
              <button type="button" className="fds-pill" data-focus={focusTools ? "true" : "false"}
                      aria-pressed={focusTools} onClick={() => setFocusTools((f) => !f)}
                      title="Fold the prose, open every tool call (F)">
                focus tools
              </button>
            </div>
          </div>
        )}
      </header>

      {loading && <p className="fds-note">Loading transcript…</p>}
      {err && (
        <div className="fds-note" style={{ color: "var(--fds-err)" }}>
          {err.includes("404") ? "Session not found (no matching .jsonl on disk)." : `Could not load transcript (${err}).`}
        </div>
      )}

      {data && (
        <ReadCtx.Provider value={readCtx}>
        <SubagentsCtx.Provider value={ctx}>
          <div className="fds-body">
            <main className="fds-stream" ref={chatColRef}>
              {data.truncated && (
                <div className="fds-day">
                  <span className="fds-label">
                    {turns.length.toLocaleString()} of {Number(data.turn_count).toLocaleString()} turns loaded, the newest ones
                  </span>
                  <span className="fds-day-rule" />
                </div>
              )}

              {focusAgent && (
                <div className="fds-day" style={{ marginTop: 8 }}>
                  <span className="fds-label" style={{ color: "var(--fds-warn)" }}>
                    focused on the {focusSub?.agent_type || "subagent"} thread
                  </span>
                  <span className="fds-day-rule" />
                  <button type="button" className="fds-label" style={{ color: "var(--fds-info)" }}
                          onClick={() => { window.location.hash = `#/session/${encodeURIComponent(parentId)}`; }}>
                    show the full session
                  </button>
                </div>
              )}

              {view === "flow" ? (
                <FlowView groups={groups} byToolUse={subAssign.byToolUse} />
              ) : viewRows.length === 0 ? (
                <p className="fds-note">Nothing to show with the current filters.</p>
              ) : (
                // The list owns its scrollport instead of scrolling the page.
                // With `useWindowScroll` the document height is shared with the
                // rest of the page, so when Virtuoso re-estimates item sizes
                // the document shrinks under the reader, the browser clamps
                // scrollY, and the rendered range stops matching where you are
                // looking - measured as a viewport of blank that never recovers.
                <Virtuoso
                  style={{ height: "100%", overflowAnchor: "none" }}
                  data={viewRows}
                  ref={chatRef}
                  // Render well beyond the viewport so a turn is MEASURED before
                  // it is read. Heights vary by two orders of magnitude, and an
                  // item first drawn at its estimate and corrected a frame later
                  // is exactly the flicker you see scrolling into a long block.
                  increaseViewportBy={{ top: 4000, bottom: 2000 }}
                  // Measured row heights: median 52px, mean 75px. Estimating
                  // from the mean made almost every measured row come out
                  // smaller than guessed, shrinking the total with no
                  // compensation - the text sliding under the reader.
                  defaultItemHeight={52}
                  computeItemKey={(i, r) => r?.key ?? i}
                  // `align: "end"` so the newest block sits ON the bottom edge.
                  // Index alone puts it at the TOP of the viewport, which left
                  // a screen of slack under it and made "am I at the bottom"
                  // ambiguous for followOutput.
                  initialTopMostItemIndex={focusAgent
                    ? 0 : { index: Math.max(0, viewRows.length - 1), align: "end" }}
                  followOutput="auto"
                  startReached={loadOlder}
                  // Only when the scroll settles. Recomputing on every
                  // rangeChanged sets state mid-scroll, and a re-render in the
                  // middle of a measurement pass is worth ~900px of jump.
                  isScrolling={(on) => { if (!on) recomputePrompt(); }}
                  itemContent={(i, r) => (
                    <div className={r.last ? "pb-5" : "pb-2"} data-gkey={r.key}
                         data-cstart={r.first && promptRowIndex.has(r.gi) ? promptRowIndex.get(r.gi) : undefined}>
                      <TurnRowMemo row={r} />
                    </div>
                  )}
                  components={listComponents}
                />
              )}
            </main>

            <aside className="fds-rail fds-rail-r">
              <div className="fds-tabs" role="tablist" aria-label="Session panel">
                {PANELS.map((t) => (
                  <button key={t} type="button" role="tab" className="fds-tab"
                          aria-selected={panel === t} data-on={panel === t ? "true" : "false"}
                          onClick={() => setPanel(t)}>{t}</button>
                ))}
              </div>

              <div className="fds-panel-body">
              {panel === "prompts" && (
                <>
                  {prompts.length === 0 && <div className="fds-label">no prompt in the loaded window</div>}
                  {prompts.map((p, i) => (
                    <button key={i} type="button" className="fds-prow"
                            data-on={i === activePrompt ? "true" : "false"}
                            onClick={() => jumpToPrompt(i)} title={p.label}>
                      <span className="fds-prow-bar" />
                      <span className="min-w-0 flex-1">
                        <span className="fds-prow-label">{p.label || "(no text)"}</span>
                        <span className="fds-prow-meta">
                          <span className="fds-label">{fmtClock(p.ts)}</span>
                          <span className="fds-label">{p.toolCount} tools</span>
                          {p.errorCount > 0 && (
                            <span className="fds-label" style={{ color: "var(--fds-err)" }}>{p.errorCount} err</span>
                          )}
                        </span>
                      </span>
                    </button>
                  ))}
                </>
              )}

              {panel === "session" && (
                <>
                  <div>
                    {readings.map((r) => (
                      <div className="fds-reading" key={r.label}>
                        <span className="fds-label">{r.label}</span>
                        <span className="fds-reading-value" style={r.tone ? { color: r.tone } : undefined}>{r.value}</span>
                      </div>
                    ))}
                  </div>
                  <div className="fds-label" style={{ marginTop: 8 }}>filter the stream</div>
                  <div className="fds-chips">
                    {[["prose", filters.prose, () => setFilters((f) => ({ ...f, prose: !f.prose }))],
                      ["thinking", filters.thinking, () => setFilters((f) => ({ ...f, thinking: !f.thinking }))],
                      ["tools", filters.tools, () => setFilters((f) => ({ ...f, tools: !f.tools }))],
                      ["errors only", errorsOnly, () => setErrorsOnly((v) => !v)]].map(([label, on, onClick]) => (
                      <button key={label} type="button" className="fds-chip" data-on={on ? "true" : "false"}
                              aria-pressed={Boolean(on)} onClick={onClick}>
                        {label}
                      </button>
                    ))}
                  </div>
                </>
              )}

              {panel === "tools" && <ToolMix mix={toolMix} />}
              </div>

              <div className="fds-keys">
                <div className="fds-label">keys</div>
                {[["F", "focus tools"], ["J / K", "next, previous call"],
                  ["E", "errors only"], ["W", "wrap long lines"],
                  ["TAB", "switch this panel"]].map(([k, d]) => (
                  <span className="fds-keys-item" key={k}>
                    <span className="fds-key">{k}</span>
                    <span className="fds-keys-text">{d}</span>
                  </span>
                ))}
              </div>
            </aside>
          </div>
        </SubagentsCtx.Provider>
        </ReadCtx.Provider>
      )}
    </div>
  );
}
