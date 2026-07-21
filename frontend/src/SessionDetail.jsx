import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Virtuoso } from "react-virtuoso";
import { get, subscribe } from "./api.js";

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
        results[b.tool_use_id] = { content: b.content, is_error: b.is_error };

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
    if (last && last.role === t.role && last.is_meta === t.is_meta &&
        last.is_sidechain === t.is_sidechain) {
      last.blocks.push(...t.blocks);
      last.last_ts = t.ts || last.last_ts;
    } else {
      groups.push({
        role: t.role, is_meta: t.is_meta, is_sidechain: t.is_sidechain,
        ts: t.ts, last_ts: t.ts, blocks: [...t.blocks],
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
const SubagentsCtx = React.createContext({
  byToolUse: new Map(), orphans: [], focusToolUseId: null, focusAgentId: null });

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

function SubagentThread({ sub, focus = false }) {
  const groups = useMemo(() => groupTurns(mergeToolResults(sub.turns)), [sub]);
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
        className="mt-2 w-full"
      >
        <div className="ml-1 space-y-4 border-l-2 border-amber-500/25 pl-3">
          {sub.truncated && (
            <p className="text-[11px] text-amber-300/80">showing first {sub.turns.length} of {sub.turn_count} turns</p>
          )}
          {groups.map((g, i) => <TurnGroup key={i} group={g} />)}
        </div>
      </Collapsible>
    </div>
  );
}

const Markdown = ({ text, italic = false }) => (
  <div className={`md ${italic ? "italic text-zinc-400" : ""}`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
  </div>
);

/* ---- collapsible ------------------------------------------------------- */
function Collapsible({ icon, label, summary, accent = "zinc", defaultOpen = false, className = "", children }) {
  const [open, setOpen] = useState(defaultOpen);
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
        onClick={() => setOpen((o) => !o)}
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
  if (!path) return null;
  const src = `/api/screenshot?path=${encodeURIComponent(path)}`;
  if (broken) {
    return <p className="font-mono text-xs text-zinc-600">🖼 screenshot file not found - {path}</p>;
  }
  return (
    <a href={src} target="_blank" rel="noreferrer" title="Open full size">
      <img
        src={src}
        alt="screenshot"
        loading="lazy"
        onError={() => setBroken(true)}
        className="max-h-[560px] w-auto max-w-full rounded-lg border border-zinc-800"
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

function Block({ b, role }) {
  const isUser = role === "user";
  if (b.type === "text") {
    // Injected context (IDE / command / system-reminder) — dim + collapsed.
    if (b.meta) {
      return (
        <Collapsible icon="⌗" label="injected context" summary="system / IDE / command context" className="w-full max-w-full">
          <Pre>{b.text}</Pre>
        </Collapsible>
      );
    }
    return (
      <div className={`max-w-full rounded-2xl border px-4 py-3 ${
        isUser ? "border-emerald-500/25 bg-emerald-500/[0.07]" : "border-zinc-800/80 bg-zinc-900/30"
      }`}>
        <Markdown text={b.text} />
      </div>
    );
  }
  if (b.type === "thinking") {
    // Extended thinking is usually persisted encrypted (signature only), so the
    // plaintext is empty — show a slim marker rather than an empty expander.
    if (!b.text || !b.text.trim()) {
      return <p className="text-[11px] italic text-zinc-600">✳ thinking - not recorded (encrypted)</p>;
    }
    return (
      <Collapsible icon="✳" label="thinking" summary={b.text.split("\n")[0]} accent="violet" className="w-full max-w-full">
        <Markdown text={b.text} italic />
      </Collapsible>
    );
  }
  if (b.type === "tool_use") return <ToolUse b={b} />;
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
function TurnGroup({ group }) {
  const isUser = group.role === "user";
  const blocks = group.blocks.map((b, i) => <Block key={i} b={b} role={group.role} />);

  // Injected-context group: neither party's message — centered and dim.
  if (group.is_meta) {
    return <div className="mx-auto flex w-full max-w-full flex-col gap-2 opacity-70">{blocks}</div>;
  }

  return (
    <div className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
      <div className={`flex items-center gap-2 ${isUser ? "flex-row-reverse" : ""}`}>
        <span
          className={`inline-flex h-5 items-center rounded-md px-1.5 text-[10px] font-semibold uppercase tracking-wider ${
            isUser ? "bg-emerald-500/15 text-emerald-400" : "bg-zinc-800 text-zinc-300"
          }`}
        >
          {isUser ? "You" : "Claude"}
        </span>
        {group.is_sidechain && (
          <span className="rounded bg-amber-500/10 px-1.5 text-[10px] font-medium uppercase tracking-wide text-amber-400">
            subagent
          </span>
        )}
        <span className="font-mono text-[10px] text-zinc-600">{fmtTs(group.ts)}</span>
      </div>
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
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [view, setView] = useState(
    initialView === "clearance" || initialView === "flow" ? initialView : "chat"
  ); // chat | flow | clearance
  const groups = useMemo(() => groupTurns(mergeToolResults(data?.turns)), [data]);
  const subAssign = useMemo(
    () => assignSubagents(data?.turns, data?.subagents), [data]);
  const clearances = useMemo(() => computeClearances(groups), [groups]);
  const clearancePositions = useMemo(() => clearanceGeom(clearances), [clearances]);
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
    virtuosoRef.current.scrollToIndex({ index: c.startIndex, align: "start", offset: -CLR_HEADER_OFFSET });
    clearTimeout(jumpToClearance._t);
    jumpToClearance._t = setTimeout(() => { jumpingRef.current = false; }, 450);
  }, [clearances]);
  const focusToolUseId = useMemo(() => {
    if (!focusAgent) return null;
    for (const [id, sub] of subAssign.byToolUse) if (sub.agent_id === focusAgent) return id;
    return null;
  }, [subAssign, focusAgent]);
  const focusSub = useMemo(
    () => (data?.subagents || []).find((s) => s.agent_id === focusAgent) || null,
    [data, focusAgent]);
  const ctx = useMemo(
    () => ({ ...subAssign, focusAgentId: focusAgent, focusToolUseId }),
    [subAssign, focusAgent, focusToolUseId]);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const load = useCallback(() => {
    return get(`/api/session/${encodeURIComponent(parentId)}`)
      .then((d) => { if (mounted.current) { setData(d); setErr(null); } })
      .catch((e) => { if (mounted.current) setErr(String(e.message || e)); })
      .finally(() => { if (mounted.current) setLoading(false); });
  }, [parentId]);

  // (Re)load when the viewed session changes.
  useEffect(() => {
    setLoading(true); setData(null); setErr(null);
    load();
  }, [sessionId, load]);

  // Live-follow: the backend emits an SSE event whenever a watched .jsonl
  // changes (new turns appended). Re-fetch this transcript on each tick.
  // Landing at the latest turn on load and following the live tail are now
  // handled by the chat Virtuoso (initialTopMostItemIndex + followOutput).
  useEffect(() => subscribe(() => load(), setLive), [load]);

  return (
    <div className={`mx-auto w-full ${view === "clearance" ? "" : "max-w-[900px]"}`}>
      <div className="sticky top-0 z-20 -mt-6 bg-zinc-950/95 pt-6 backdrop-blur-xl md:-mt-8 md:pt-8">
        <div className="mb-4 flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1.5 text-sm text-zinc-400 transition-colors hover:text-emerald-400"
          >
            ← Logbook
          </button>
          <span className="hidden items-center gap-2 text-xs sm:flex" title="Auto-updates as the session file changes">
            <span className={`h-1.5 w-1.5 rounded-full ${live ? "animate-live-pulse bg-emerald-400" : "bg-zinc-600"}`} />
            <span className="text-zinc-500">{live ? "following" : "offline"}</span>
          </span>
        </div>

        {data && (
          <header className="border-b border-zinc-800/80 pb-5">
            <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
              {data.title || <span className="text-zinc-500">Untitled session</span>}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
              <span className="font-mono text-zinc-400">{(data.session_id || "").slice(0, 8)}</span>
              {data.project && <><span className="text-zinc-700">·</span><span className="font-mono">{data.project}</span></>}
              {data.git_branch && <><span className="text-zinc-700">·</span><span>⎇ {data.git_branch}</span></>}
              <span className="text-zinc-700">·</span>
              <span>{fmtRange(data.first_ts, data.last_ts)}</span>
              <span className="text-zinc-700">·</span>
              <span className="font-mono">{data.turn_count} turns</span>
              {(data.subagents || []).length > 0 && (
                <><span className="text-zinc-700">·</span>
                <span className="rounded bg-amber-500/10 px-1.5 text-[10px] font-medium uppercase tracking-wide text-amber-400">
                  {data.subagents.length} subagent{data.subagents.length > 1 ? "s" : ""}
                </span></>
              )}
              {data.version && <><span className="text-zinc-700">·</span><span>v{data.version}</span></>}
              <span className="flex-1" />
              <div role="group" aria-label="View" className="flex rounded-lg border border-zinc-800 bg-zinc-900/60 p-0.5">
                {["chat", "flow", "clearance"].map((v) => (
                  <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}
                    className={`rounded-md px-2 py-0.5 text-[11px] font-medium capitalize transition-colors ${
                      view === v ? "bg-emerald-500/15 text-emerald-400" : "text-zinc-400 hover:text-zinc-200"}`}>
                    {v}
                  </button>
                ))}
              </div>
            </div>
          </header>
        )}
      </div>

      {loading && <p className="mt-6 text-sm text-zinc-500">Loading transcript…</p>}

      {err && (
        <div className="mt-6 rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
          {err.includes("404") ? "Session not found (no matching .jsonl on disk)." : `Could not load transcript (${err}).`}
        </div>
      )}

      {data && (
        <div className="pt-6">
          {data.truncated && (
            <div className="mb-5 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-2.5 text-xs text-amber-300/90">
              Showing the first {data.returned} of {data.turn_count} turns (large session; capped for the browser).
            </div>
          )}

          {focusAgent && (
            <div className="mb-5 flex items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.06] px-4 py-2.5 text-xs">
              <span className="text-amber-300/90">
                ⤷ Focused on the <b>{focusSub?.agent_type || "subagent"}</b> subagent - the main agent's turns are shown around it.
              </span>
              <button
                type="button"
                onClick={() => { window.location.hash = `#/session/${encodeURIComponent(parentId)}`; }}
                className="shrink-0 rounded-md px-2 py-0.5 font-medium text-amber-300 transition-colors hover:bg-amber-500/15"
              >
                show full session
              </button>
            </div>
          )}

          <SubagentsCtx.Provider value={ctx}>
            {view === "flow" ? (
              <FlowView groups={groups} byToolUse={subAssign.byToolUse} />
            ) : view === "clearance" ? (
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-[6fr_4fr]">
                <div className="min-w-0" ref={chatColRef}>
                  {/* mobile: a compact jump-strip stands in for the side panel */}
                  <div className="mb-4 flex gap-1 overflow-x-auto pb-1 lg:hidden">
                    {clearances.map((c, i) => (
                      <button
                        key={i} type="button" onClick={() => jumpToClearance(i)} title={c.label}
                        className={`shrink-0 rounded-full border px-2.5 py-1 font-mono text-[10px] transition-colors ${
                          i === activeClearance
                            ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                            : "border-zinc-800 text-zinc-500"}`}
                      >
                        {i + 1}
                      </button>
                    ))}
                  </div>
                  {groups.length === 0 ? (
                    <p className="py-10 text-center text-sm text-zinc-500">No renderable messages in this session.</p>
                  ) : (
                    <Virtuoso
                      ref={virtuosoRef}
                      useWindowScroll
                      data={groups}
                      computeItemKey={(i) => i}
                      rangeChanged={recomputeActive}
                      isScrolling={(s) => { if (!s) recomputeActive(); }}
                      itemContent={(i, g) => {
                        const cIdx = clearanceStartMap.get(i);
                        return (
                          <div className="pb-5" data-cstart={cIdx !== undefined ? cIdx : undefined}>
                            <TurnGroup group={g} />
                          </div>
                        );
                      }}
                    />
                  )}
                </div>
                <aside className="hidden min-w-0 lg:block">
                  <div className="sticky top-28 min-w-0">
                    <ClearancePanel
                      clearances={clearances} positions={clearancePositions}
                      activeIdx={activeClearance} onJump={jumpToClearance}
                      groups={groups} byToolUse={subAssign.byToolUse} live={live}
                    />
                  </div>
                </aside>
              </div>
            ) : groups.length === 0 ? (
              <p className="py-10 text-center text-sm text-zinc-500">No renderable messages in this session.</p>
            ) : (
              <Virtuoso
                useWindowScroll
                data={groups}
                computeItemKey={(i) => i}
                initialTopMostItemIndex={focusAgent ? 0 : groups.length - 1}
                followOutput="auto"
                itemContent={(i, g) => <div className="pb-5"><TurnGroup group={g} /></div>}
                components={{
                  Footer: subAssign.orphans.length
                    ? () => (
                        <div className="space-y-2 border-t border-zinc-800/70 pt-4">
                          <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                            Subagents (not linked to a visible Agent call)
                          </p>
                          {subAssign.orphans.map((s) => (
                            <SubagentThread key={s.agent_id} sub={s} focus={focusAgent === s.agent_id} />
                          ))}
                        </div>
                      )
                    : undefined,
                }}
              />
            )}
          </SubagentsCtx.Provider>
        </div>
      )}
    </div>
  );
}
