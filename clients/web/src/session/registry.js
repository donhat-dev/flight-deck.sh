/**
 * Which renderer draws a tool result.
 *
 * Matched on the tool first, then on the SHAPE of the result - so an MCP nobody
 * has heard of that returns an array of objects still gets a table, and one
 * that returns a widget envelope still gets a widget.
 */
import { extractPaths } from "./paths.js";
import { hasAnsi, stripAnsi } from "./ansi.js";
import { unifiedDiff } from "./diff.js";
import { pickLanguage, highlight } from "./syntax.js";

export const isScreenshotTool = (name) =>
  typeof name === "string" && name.includes("take_screenshot");

/** `Read` returns the file with a line-number gutter baked into the text
 *  ("   12\u2192code"). Strip it so the code can be highlighted, and keep the
 *  first number so the gutter we draw ourselves says the truth. */
function stripReadGutter(text) {
  const lines = String(text || "").split("\n");
  const m = /^\s*(\d+)\u2192/.exec(lines[0] || "");
  if (!m) return null;
  const numbered = lines.filter((l) => /^\s*\d+\u2192/.test(l));
  if (numbered.length < Math.max(1, Math.floor(lines.length * 0.6))) return null;
  return {
    startLine: Number(m[1]) || 1,
    text: lines.map((l) => l.replace(/^\s*\d+\u2192/, "")).join("\n"),
  };
}

/** True for a result that says one short thing: "clicked uid=3_12", "OK", a
 *  path. 32% of all results in a measured session are under 200 characters, and
 *  every one of them was costing a click to read. */
function isOneLiner(text) {
  const t = String(text || "").trim();
  return t.length > 0 && t.length <= 160 && !t.includes("\n");
}

/** What a command's output turns out to be, when it carries no colours of its
 *  own. Deliberately conservative: a wrong guess paints ordinary log lines in
 *  keyword colours, which is worse than leaving them plain. */
export function sniffOutputLanguage(text) {
  const t = String(text || "").trimStart();
  if (t.length < 12) return null;
  if (t.startsWith("<?xml") || /^<[a-zA-Z][\w:-]*[\s>/]/.test(t)) return "xml";
  if (t[0] === "{" || t[0] === "[") {
    try { JSON.parse(t); return "json"; } catch { return null; }
  }
  if (t.startsWith("---\n") || t.startsWith("apiVersion:") || t.startsWith("version:")) return "yaml";
  if (t.startsWith("diff --git") || /^(?:\+\+\+|---) /.test(t)) return null;   // the diff renderer owns those
  // A python traceback is python, and it is the single most common thing a
  // failing Bash call prints in this estate.
  if (t.startsWith("Traceback (most recent call last)")) return "python";
  return null;
}

/** A screenshot's path lives in the input, or in the "Saved screenshot to X" text. */
export function screenshotPath(input, resultText) {
  const fromInput = input?.filePath || input?.path;
  if (fromInput) return fromInput;
  const m = /(?:Saved screenshot to|screenshot to)\s+(\S+?\.(?:png|jpe?g|webp|gif))/i.exec(resultText || "");
  return m ? m[1] : null;
}

/** ```fd-widget ... ``` fence, anywhere in the result. */
export function parseWidget(text) {
  if (typeof text !== "string" || !text.includes("fd-widget")) return null;
  const m = /```fd-widget\s*\n([\s\S]*?)```/.exec(text);
  if (!m) return null;
  try {
    const spec = JSON.parse(m[1]);
    return spec && typeof spec === "object" ? spec : null;
  } catch { return null; }
}

function parseJson(text) {
  if (typeof text !== "string") return null;
  const t = text.trim();
  if (!t || (t[0] !== "{" && t[0] !== "[")) return null;
  if (t.length > 400_000) return null;
  try { return JSON.parse(t); } catch { return null; }
}

const isRowArray = (v) =>
  Array.isArray(v) && v.length > 0 && v.length <= 500 &&
  v.every((r) => r && typeof r === "object" && !Array.isArray(r));

/**
 * @returns {{kind: string, props: object}} kind is one of
 *   image · diff · tree · widget · table · json · web · terminal
 */
export function pickRenderer(name, input, result) {
  const text = result?.content ?? "";
  const tool = String(name || "");

  if (isScreenshotTool(tool)) {
    const path = screenshotPath(input, text);
    if (path) return { kind: "image", props: { path } };
  }

  if (tool === "TodoWrite" && Array.isArray(input?.todos)) {
    return { kind: "todo", props: { todos: input.todos } };
  }

  if (tool.includes("take_snapshot") && text) {
    return { kind: "snapshot", props: { text } };
  }

  if ((tool === "Edit" || tool === "MultiEdit") &&
      (input?.old_string != null || input?.new_string != null)) {
    return { kind: "diff", props: { oldStr: input.old_string ?? "", newStr: input.new_string ?? "", filePath: input.file_path } };
  }
  if (tool === "Write" && input?.content != null) {
    return { kind: "diff", props: { oldStr: "", newStr: input.content, filePath: input.file_path } };
  }

  // A file read is code, and code has a language. The extension decides it.
  if ((tool === "Read" || tool === "NotebookRead") && !result?.is_error) {
    const path = input?.file_path || input?.path || input?.notebook_path;
    const gutter = stripReadGutter(text);
    if (path && /\.(png|jpe?g|gif|webp|svg|pdf)$/i.test(path)) {
      return { kind: "image", props: { path } };
    }
    if (path && (gutter || text)) {
      return {
        kind: "code",
        props: {
          text: gutter ? gutter.text : text,
          startLine: gutter ? gutter.startLine : 1,
          lang: pickLanguage(path, text),
        },
      };
    }
  }

  const widget = parseWidget(text);
  if (widget) return { kind: "widget", props: { spec: widget, raw: text.replace(/```fd-widget[\s\S]*?```/, "").trim() } };

  if (tool === "WebFetch" || tool === "WebSearch") {
    return { kind: "web", props: { url: input?.url || input?.query, text: stripAnsi(text) } };
  }

  if (!result?.is_error && (tool === "Grep" || tool === "Glob" || tool === "Bash")) {
    const counts = extractPaths(text);
    if (counts.size >= 2) return { kind: "tree", props: { counts } };
  }

  const json = parseJson(text);
  if (json) {
    if (isRowArray(json)) return { kind: "table", props: { rows: json } };
    return { kind: "json", props: { value: json } };
  }

  // A one-line answer belongs in the header, not behind a disclosure triangle.
  if (!result?.is_error && isOneLiner(text)) {
    return { kind: "inline", props: { text: stripAnsi(text).trim() } };
  }

  // Terminal is the fallback, and it is a real renderer: it keeps the colours
  // the producer already wrote. Anything not from a shell wraps instead of
  // scrolling, because it is prose in a mono voice, not columns.
  //
  // `cat x.xml`, `docker compose config` and `python -c 'print(json)'` all end
  // up here with no ANSI of their own. Sniffing what the output turns out to be
  // costs one regex and colours a third of the Bash calls that used to be grey.
  const shellish = tool === "Bash" || hasAnsi(text);
  const lang = hasAnsi(text) ? null : sniffOutputLanguage(text);
  return {
    kind: "terminal",
    props: { text, wrap: !shellish, lang, tone: result?.is_error ? "err" : undefined },
  };
}

/** The badge on the right of the header: same slot, every call. */
export function resultBadge(name, input, result, renderer) {
  if (!result) return null;
  if (result.is_error) return { text: "FAILED", tone: "err" };
  if (renderer.kind === "diff") {
    // The real counts, not a line-count subtraction: replacing ten lines with
    // ten others is +10 -10, and reading it as "+0 -0" would be a lie.
    const { added, removed } = unifiedDiff(renderer.props.oldStr, renderer.props.newStr);
    return { text: `+${added} -${removed}`, tone: "ok" };
  }
  if (renderer.kind === "tree") {
    const n = renderer.props.counts.size;
    return { text: `${n} FILE${n > 1 ? "S" : ""}`, tone: "info" };
  }
  if (renderer.kind === "code") {
    const n = String(renderer.props.text || "").split("\n").length;
    return { text: `${n} LINES`, tone: "info" };
  }
  if (renderer.kind === "todo") {
    const t = renderer.props.todos;
    const done = t.filter((x) => x.status === "completed").length;
    return { text: `${done}/${t.length}`, tone: done === t.length ? "ok" : "warn" };
  }
  if (renderer.kind === "inline") return null;
  const m = /(?:^|\n)\s*exit(?: code)?[: ]\s*(\d+)/i.exec(result.content || "");
  if (m) return { text: `EXIT ${m[1]}`, tone: m[1] === "0" ? "ok" : "err" };
  return { text: "DONE", tone: "ok" };
}

/** The one thing the call acted on. Never the whole input JSON. */
export function toolTarget(name, input) {
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
  return k ? first(JSON.stringify(input[k])).slice(0, 120) : "";
}

/** `mcp__flightdeck__session_search` reads as FLIGHTDECK · session_search. */
export function toolLabel(name) {
  const m = /^mcp__([^_]+(?:_[^_]+)*?)__(.+)$/.exec(String(name || ""));
  if (m) return { server: m[1].replace(/[-_]/g, " "), tool: m[2] };
  return { server: null, tool: String(name || "tool") };
}
