/**
 * One colour per tool.
 *
 * The status dot already carries ok/failed, so this is identity, not state:
 * after a hundred cards the eye finds "the Edit ones" by colour long before it
 * reads the word. Measured on one session, six tools are 95% of every call
 * (Bash, Edit, Read, Write, and the chrome-devtools pair), so those get chosen
 * hues rather than generated ones.
 */

const KNOWN = {
  Bash: "var(--fds-a3)",        // amber, the shell
  Read: "var(--fds-a6)",        // cyan, looking
  Edit: "var(--fds-b2)",        // green, changing
  MultiEdit: "var(--fds-b2)",
  Write: "var(--fds-a2)",       // deeper green, creating
  NotebookEdit: "var(--fds-b2)",
  Grep: "var(--fds-a4)",        // blue, searching
  Glob: "var(--fds-a4)",
  Agent: "var(--fds-a5)",       // violet, delegating
  Task: "var(--fds-a5)",
  TodoWrite: "var(--fds-b3)",
  WebFetch: "var(--fds-b4)",
  WebSearch: "var(--fds-b4)",
  ToolSearch: "var(--fds-b5)",
  AskUserQuestion: "var(--fdx-signal-hover)",
};

// A stable hue per name for everything else, so an MCP tool nobody planned for
// still gets a consistent colour across sessions rather than a grey.
const hue = (name) => {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
};

/** @param {string} name raw tool name, MCP prefix and all */
export function toolColor(name) {
  const raw = String(name || "");
  if (KNOWN[raw]) return KNOWN[raw];
  const m = /^mcp__([^_]+(?:_[^_]+)*?)__(.+)$/.exec(raw);
  // An MCP server's tools share the server's hue: they are one thing to the
  // reader, and twelve chrome-devtools calls in a row should not be a rainbow.
  const key = m ? m[1] : raw;
  if (!key) return "var(--fdx-text-muted)";
  return `hsl(${hue(key)} 62% 66%)`;
}

export const KNOWN_TOOLS = Object.keys(KNOWN);
