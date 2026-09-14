import { describe, it, expect } from "vitest";
import { pickRenderer, resultBadge, toolLabel, toolTarget, parseWidget } from "./registry.js";

const res = (content, is_error = false) => ({ content, is_error });

describe("pickRenderer", () => {
  it("draws an Edit as a diff, not two blobs", () => {
    const r = pickRenderer("Edit", { file_path: "a.py", old_string: "x", new_string: "y" }, res("ok"));
    expect(r.kind).toBe("diff");
    expect(r.props.filePath).toBe("a.py");
  });

  it("draws a Write as a diff against nothing", () => {
    const r = pickRenderer("Write", { file_path: "a.py", content: "x\ny" }, res("ok"));
    expect(r.kind).toBe("diff");
    expect(r.props.oldStr).toBe("");
  });

  it("draws a path listing as a tree", () => {
    const out = "src/a.py\nsrc/b.py\nsrc/c.py";
    expect(pickRenderer("Grep", { pattern: "x" }, res(out)).kind).toBe("tree");
  });

  it("keeps real shell output in the terminal", () => {
    expect(pickRenderer("Bash", { command: "ls" }, res("a.txt\nb.txt\nsomething else")).kind).toBe("terminal");
  });

  it("puts a one-line answer in the header instead of behind a triangle", () => {
    const r = pickRenderer("mcp__chrome-devtools__click", { uid: "3_12" }, res("Clicked uid=3_12"));
    expect(r.kind).toBe("inline");
    expect(r.props.text).toBe("Clicked uid=3_12");
    // a failure always keeps its body, however short it is
    expect(pickRenderer("Bash", { command: "x" }, res("boom", true)).kind).toBe("terminal");
  });

  it("draws a Read as code in the language of the file", () => {
    const r = pickRenderer("Read", { file_path: "/a/b.py" }, res("     1\u2192import os\n     2\u2192x = 1"));
    expect(r.kind).toBe("code");
    expect(r.props.lang).toBe("python");
    expect(r.props.startLine).toBe(1);
    expect(r.props.text).toBe("import os\nx = 1");
  });

  it("draws a snapshot and a todo list as themselves", () => {
    expect(pickRenderer("mcp__chrome-devtools__take_snapshot", {}, res("root\n  button uid=1_2")).kind).toBe("snapshot");
    const todo = pickRenderer("TodoWrite", { todos: [{ content: "a", status: "completed" }] }, res("ok"));
    expect(todo.kind).toBe("todo");
  });

  it("draws an array of objects as a table", () => {
    const r = pickRenderer("mcp__x__y", {}, res(JSON.stringify([{ a: 1 }, { a: 2 }])));
    expect(r.kind).toBe("table");
    expect(r.props.rows).toHaveLength(2);
  });

  it("draws any other JSON as a tree, not a wall", () => {
    expect(pickRenderer("mcp__x__y", {}, res('{"a":{"b":1}}')).kind).toBe("json");
  });

  it("draws a widget envelope as a widget and keeps the rest as raw", () => {
    const body = 'before\n```fd-widget\n{"v":1,"type":"table","rows":[]}\n```\nafter';
    const r = pickRenderer("mcp__flightdeck__session_search", {}, res(body));
    expect(r.kind).toBe("widget");
    expect(r.props.spec.type).toBe("table");
    expect(r.props.raw).toContain("before");
  });

  it("falls back to the terminal, wrapped, for prose from a tool", () => {
    const r = pickRenderer("SomeTool", {}, res("a sentence\nover two lines that is long enough to need a body"));
    expect(r.kind).toBe("terminal");
    expect(r.props.wrap).toBe(true);
  });

  it("finds a screenshot path in the result text", () => {
    const r = pickRenderer("mcp__chrome__take_screenshot", {}, res("Saved screenshot to /tmp/a.png"));
    expect(r.kind).toBe("image");
    expect(r.props.path).toBe("/tmp/a.png");
  });
});

describe("header slots", () => {
  it("splits an MCP name into server and tool", () => {
    expect(toolLabel("mcp__flightdeck__session_search")).toEqual({ server: "flightdeck", tool: "session_search" });
    expect(toolLabel("Bash")).toEqual({ server: null, tool: "Bash" });
  });

  it("names the one thing acted on", () => {
    expect(toolTarget("Bash", { command: "ls -la\nsecond line" })).toBe("ls -la");
    expect(toolTarget("Grep", { pattern: "foo" })).toBe("/foo/");
  });

  it("says FAILED before anything else", () => {
    const r = pickRenderer("Bash", { command: "x" }, res("boom", true));
    expect(resultBadge("Bash", {}, res("boom", true), r)).toMatchObject({ text: "FAILED", tone: "err" });
  });

  it("counts the diff for an edit badge", () => {
    const input = { old_string: "a\nb", new_string: "a\nb\nc\nd" };
    const r = pickRenderer("Edit", input, res("ok"));
    expect(resultBadge("Edit", input, res("ok"), r).text).toBe("+2 -0");
  });
});

describe("parseWidget", () => {
  it("returns null for text that only mentions the word", () => {
    expect(parseWidget("we should add an fd-widget someday")).toBe(null);
  });
  it("returns null for a malformed envelope rather than throwing", () => {
    expect(parseWidget("```fd-widget\n{nope}\n```")).toBe(null);
  });
});
