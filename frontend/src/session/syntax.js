/**
 * Syntax colour, chosen by what the file is.
 *
 * Measured over one real session: 1099 Read/Edit/Write of `.py`, 806 of `.md`,
 * 206 of `.xml`, then a long tail. All of it was being drawn as one grey block,
 * so the extension is the cheapest signal available and it is already in the
 * tool input.
 *
 * Hand-rolled on purpose. The bundle is 767 kB before any of this; a
 * highlighter library would be the largest single thing in it, to colour text
 * that is already truncated at 24k characters server-side.
 *
 * THE CONTRACT IS THE ROUND TRIP: the tokens of a line, concatenated, equal
 * that line. A highlighter that silently drops a character corrupts the record
 * of what a tool actually did, which is worse than no colour at all.
 */

const EXT = {
  xml: "xml", html: "xml", htm: "xml", svg: "xml", xsl: "xml", xhtml: "xml",
  json: "json", jsonl: "json", ipynb: "json", webmanifest: "json",
  py: "python", pyi: "python", pyw: "python",
  js: "js", jsx: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js",
  css: "css", scss: "css", less: "css",
  sql: "sql", psql: "sql",
  yaml: "yaml", yml: "yaml",
  md: "markdown", mdx: "markdown", markdown: "markdown",
  sh: "shell", bash: "shell", zsh: "shell", fish: "shell",
  toml: "toml",
  ini: "ini", cfg: "ini", conf: "ini", properties: "ini",
};

const BASENAME = { dockerfile: "shell", makefile: "shell", ".gitignore": "plain" };

/** Which tokenizer to use for a path, falling back to sniffing the text. */
export function pickLanguage(pathOrName, hintText) {
  const raw = String(pathOrName == null ? "" : pathOrName);
  const base = raw.split(/[\\/]/).pop() || "";
  const lower = base.toLowerCase();

  if (BASENAME[lower]) return BASENAME[lower];
  if (lower === ".env" || lower.startsWith(".env.")) return "ini";

  const dot = lower.lastIndexOf(".");
  if (dot > 0) {
    const ext = lower.slice(dot + 1);
    if (EXT[ext]) return EXT[ext];
  }

  const hint = String(hintText == null ? "" : hintText).trimStart();
  if (!hint) return "plain";
  if (hint.startsWith("<?xml") || /^<[a-zA-Z!/]/.test(hint)) return "xml";
  if (hint[0] === "{" || hint[0] === "[") {
    try { JSON.parse(hint); return "json"; } catch { /* not JSON after all */ }
  }
  if (hint.startsWith("#!") && /\b(?:ba|z|)sh\b/.test(hint.slice(0, 80))) return "shell";
  return "plain";
}

/* ---- the scanner ------------------------------------------------------- */

// Sticky regexes, tried in order at the current position. A global regex
// re-scanned from the top of the string for every token is quadratic on a large
// file; anchoring with `y` keeps one pass.
const scan = (text, rules) => {
  const out = [];
  let plain = "";
  let i = 0;
  const flush = () => { if (plain) { out.push({ t: plain, c: null }); plain = ""; } };
  while (i < text.length) {
    let hit = null;
    for (const [re, cls] of rules) {
      re.lastIndex = i;
      const m = re.exec(text);
      if (m && m[0].length > 0) { hit = [m, cls]; break; }
    }
    if (hit) {
      flush();
      const [m, cls] = hit;
      if (cls === "kw-fn") {
        // "def parse" and "function parse" are two things wearing one match:
        // the keyword, then the name it introduces.
        const cut = m[0].search(/\s/);
        out.push({ t: m[0].slice(0, cut), c: "kw" });
        const rest = m[0].slice(cut);
        const nameAt = rest.search(/\S/);
        out.push({ t: rest.slice(0, nameAt), c: null });
        out.push({ t: rest.slice(nameAt), c: "fn" });
      } else if (cls === "sh-dq") {
        // A shell double-quoted string still expands variables, and "$HOME/x"
        // is the single most common thing in a transcript's commands. Colour
        // the expansion inside the string rather than flattening the lot.
        const inner = /\$\{[^}\n]*\}|\$[\w@#?*!-]+/g;
        let at = 0;
        let v;
        while ((v = inner.exec(m[0])) !== null) {
          if (v.index > at) out.push({ t: m[0].slice(at, v.index), c: "str" });
          out.push({ t: v[0], c: "var" });
          at = v.index + v[0].length;
        }
        if (at < m[0].length) out.push({ t: m[0].slice(at), c: "str" });
      } else {
        out.push({ t: m[0], c: typeof cls === "function" ? cls(m) : cls });
      }
      i += m[0].length;
    } else {
      plain += text[i];
      i += 1;
    }
  }
  flush();
  return out;
};

// Tokens are cut into lines AFTER scanning, so a triple-quoted string or a
// block comment that spans lines stays one token's worth of colour.
const toLines = (tokens) => {
  const lines = [[]];
  for (const tk of tokens) {
    const parts = tk.t.split("\n");
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) lines.push([]);
      if (parts[i]) lines[lines.length - 1].push({ t: parts[i], c: tk.c });
    }
  }
  return lines;
};

const plainLines = (text) => String(text).split("\n").map((l) => (l ? [{ t: l, c: null }] : []));

const words = (list) => new RegExp(`\\b(?:${list.join("|")})\\b`, "y");

/* ---- per language ------------------------------------------------------ */

const PY_KW = ["def", "class", "import", "from", "return", "if", "elif", "else", "for", "while",
  "try", "except", "finally", "with", "as", "lambda", "yield", "raise", "pass", "break",
  "continue", "in", "not", "and", "or", "is", "None", "True", "False", "global", "nonlocal",
  "assert", "del", "async", "await"];

const JS_KW = ["const", "let", "var", "function", "return", "if", "else", "for", "while",
  "import", "from", "export", "default", "class", "extends", "new", "await", "async", "of",
  "try", "catch", "finally", "throw", "typeof", "instanceof", "null", "undefined", "true",
  "false", "this", "in", "delete", "yield", "switch", "case", "break", "continue"];

const SH_KW = ["if", "then", "elif", "else", "fi", "for", "in", "do", "done", "while", "until",
  "case", "esac", "function", "export", "local", "return", "source"];

const SQL_KW = ["select", "from", "where", "join", "left", "right", "inner", "outer", "on",
  "group", "order", "by", "having", "insert", "into", "values", "update", "set", "delete",
  "create", "table", "index", "view", "alter", "drop", "limit", "offset", "and", "or", "not",
  "null", "as", "distinct", "union", "all", "case", "when", "then", "end"];

const RULES = {
  xml: [
    [/<!--[\s\S]*?(?:-->|$)/y, "com"],
    [/<\?[\s\S]*?(?:\?>|$)/y, "com"],
    [/<!\[CDATA\[[\s\S]*?(?:\]\]>|$)/y, "str"],
    [/<\/?/y, "punc"],
    [/[a-zA-Z_][\w.:-]*(?=[\s/>=]|$)/y, (m) => (m.input[m.index - 1] === "<" ||
      (m.input[m.index - 2] === "<" && m.input[m.index - 1] === "/") ? "tag" : "attr")],
    [/"[^"]*"?|'[^']*'?/y, "str"],
    [/&[a-zA-Z#0-9]+;?/y, "punc"],
    [/[=/>]/y, "punc"],
  ],
  json: [
    [/"(?:[^"\\]|\\.)*"?(?=\s*:)/y, "attr"],
    [/"(?:[^"\\]|\\.)*"?/y, "str"],
    [/-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y, "num"],
    [words(["true", "false", "null"]), "kw"],
    [/[{}[\],:]/y, "punc"],
  ],
  python: [
    [/#[^\n]*/y, "com"],
    [/[rbfu]{0,2}"""[\s\S]*?(?:"""|$)|[rbfu]{0,2}'''[\s\S]*?(?:'''|$)/y, "str"],
    [/[rbfu]{0,2}"(?:[^"\\\n]|\\.)*"?|[rbfu]{0,2}'(?:[^'\\\n]|\\.)*'?/y, "str"],
    [/(?:def|class)\s+[A-Za-z_]\w*/y, "kw-fn"],
    [words(["self", "cls"]), "var"],
    [words(PY_KW), "kw"],
    [/@[A-Za-z_][\w.]*/y, "fn"],
    [/\b\d[\w.]*\b/y, "num"],
    [/[(){}[\],:=+\-*/%<>!&|^~]/y, "punc"],
  ],
  js: [
    [/\/\/[^\n]*/y, "com"],
    [/\/\*[\s\S]*?(?:\*\/|$)/y, "com"],
    [/`(?:[^`\\]|\\.)*`?/y, "str"],
    [/"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?/y, "str"],
    [/\bfunction\s+[A-Za-z_$][\w$]*/y, "kw-fn"],
    [words(JS_KW), "kw"],
    [/[A-Za-z_$][\w$]*(?=\s*\()/y, "fn"],
    [/\b\d[\w.]*\b/y, "num"],
    [/[(){}[\],;:=+\-*/%<>!&|?^~.]/y, "punc"],
  ],
  css: [
    [/\/\*[\s\S]*?(?:\*\/|$)/y, "com"],
    [/"[^"\n]*"?|'[^'\n]*'?/y, "str"],
    [/[.#]?[-\w]+(?=[^;{}\n]*\{)/y, "tag"],
    [/[-\w]+(?=\s*:)/y, "attr"],
    [/-?\d[\w.%]*/y, "num"],
    [/[{}:;,()]/y, "punc"],
  ],
  sql: [
    [/--[^\n]*/y, "com"],
    [/\/\*[\s\S]*?(?:\*\/|$)/y, "com"],
    [/'(?:[^']|'')*'?/y, "str"],
    [new RegExp(`\\b(?:${SQL_KW.join("|")})\\b`, "iy"), "kw"],
    [/\b\d+(?:\.\d+)?\b/y, "num"],
    [/[(),;.*=<>]/y, "punc"],
  ],
  yaml: [
    [/#[^\n]*/y, "com"],
    [/(?<=^|\n)\s*-(?=\s)/y, "punc"],
    [/[\w.-]+(?=\s*:(?:\s|$))/y, "attr"],
    [/"[^"\n]*"?|'[^'\n]*'?/y, "str"],
    [words(["true", "false", "null", "yes", "no", "on", "off"]), "kw"],
    [/~/y, "kw"],
    [/\b\d[\w.]*\b/y, "num"],
    [/[:{}[\],&*|>]/y, "punc"],
  ],
  toml: [
    [/#[^\n]*/y, "com"],
    [/(?<=^|\n)\s*\[[^\]\n]*\]?/y, "tag"],
    [/[\w.-]+(?=\s*=)/y, "attr"],
    [/"""[\s\S]*?(?:"""|$)|"[^"\n]*"?|'[^'\n]*'?/y, "str"],
    [words(["true", "false"]), "kw"],
    [/\b\d[\w.:+-]*\b/y, "num"],
    [/[=[\],{}]/y, "punc"],
  ],
  ini: [
    [/[#;][^\n]*/y, "com"],
    [/(?<=^|\n)\s*\[[^\]\n]*\]?/y, "tag"],
    [/[\w.-]+(?=\s*=)/y, "attr"],
    [/"[^"\n]*"?|'[^'\n]*'?/y, "str"],
    [/\b\d[\w.]*\b/y, "num"],
    [/=/y, "punc"],
  ],
  markdown: [
    [/(?<=^|\n)#{1,6}[^\n]*/y, "kw"],
    [/(?<=^|\n)\s*>[^\n]*/y, "com"],
    [/```[\s\S]*?(?:```|$)/y, "num"],
    [/`[^`\n]*`?/y, "num"],
    [/\*\*[^*\n]+\*\*|__[^_\n]+__/y, "str"],
    [/\*[^*\n]+\*|_[^_\n]+_/y, "str"],
    [/\]\([^)\n]*\)?/y, "attr"],
    [/(?<=^|\n)\s*(?:[-*+]|\d+\.)(?=\s)/y, "punc"],
    [/\[|\]/y, "punc"],
  ],
  shell: [
    [/#[^\n]*/y, "com"],
    [/'[^']*'?/y, "str"],
    [/"(?:[^"\\]|\\.)*"?/y, "sh-dq"],
    [/\$\{[^}\n]*\}?|\$\(|\$[\w@#?*!-]+/y, "var"],
    // The first word after a newline, a pipe or a semicolon is the command
    // being run, and that is the word the reader is looking for. A keyword in
    // that position is still a keyword.
    [/(?<=(?:^|\n|;|\||&)[ \t]*)[\w./-]+/y, (m) => (SH_KW.includes(m[0]) ? "kw" : "fn")],
    [words(SH_KW), "kw"],
    [/&&|\|\||[|;<>&]+/y, "punc"],
    [/\b\d+\b/y, "num"],
  ],
};

const MAX_TEXT = 200_000;
const MAX_LINE_TOKENS = 2_000;

/**
 * @param {string} text
 * @param {string} lang one of the values `pickLanguage` returns
 * @returns {Array<Array<{t: string, c: string|null}>>} tokens per line
 */
export function highlight(text, lang) {
  const src = text == null ? "" : String(text);
  const rules = RULES[lang];
  // Past this size the colour is not worth the pause, and the reader is looking
  // at a file they will scroll rather than read.
  if (!rules || src.length > MAX_TEXT) return plainLines(src);

  let tokens;
  try {
    tokens = scan(src, rules);
  } catch {
    // A tokenizer must never take the transcript down with it.
    return plainLines(src);
  }

  const lines = toLines(tokens);
  // A line shredded into thousands of one-character tokens is a pathological
  // input, not code; render it flat rather than making the browser lay out
  // thousands of spans.
  return lines.map((l) => (l.length > MAX_LINE_TOKENS
    ? [{ t: l.map((t) => t.t).join(""), c: null }]
    : l));
}

/* ---- a shell command, with whatever is embedded in it ------------------- */

// `python3 - <<'PYEOF'` is a python file wearing a bash costume. Measured on
// one session: Bash is 64% of every tool call, and a large share of the long
// ones are heredocs carrying python, node or SQL. Colouring the wrapper and
// leaving the payload grey colours the least interesting half.
const HEREDOC = /<<-?\s*(['"]?)([A-Za-z_]\w*)\1/;

/** Which language a heredoc body is written in, judged by the command that
 *  opens it and then by the tag as a hint. */
function heredocLanguage(commandLine, tag) {
  const c = commandLine;
  if (/\bpython3?\b/.test(c)) return "python";
  if (/\bnode\b/.test(c)) return "js";
  if (/\b(?:psql|sqlite3|mysql)\b/.test(c)) return "sql";
  if (/\bjq\b/.test(c)) return "json";
  const redirect = /(?:>|>>|\btee\b)\s*("[^"]+"|'[^']+'|\S+)/.exec(c);
  if (redirect) {
    const lang = pickLanguage(redirect[1].replace(/^['"]|['"]$/g, ""));
    if (lang !== "plain") return lang;
  }
  if (/^(?:PY|PYEOF|PYTHON)/i.test(tag)) return "python";
  if (/^(?:JS|NODE)/i.test(tag)) return "js";
  if (/^SQL/i.test(tag)) return "sql";
  if (/^(?:JSON)/i.test(tag)) return "json";
  if (/^(?:MD|MARKDOWN)/i.test(tag)) return "markdown";
  if (/^(?:SH|BASH|EOF|SNIPEOF)/i.test(tag)) return "shell";
  return "plain";
}

/**
 * Highlight a shell command, switching language inside heredoc bodies.
 * Same return shape as `highlight`, so a caller renders one thing either way.
 */
export function highlightCommand(command) {
  const src = command == null ? "" : String(command);
  if (src.length > MAX_TEXT) return plainLines(src);
  const srcLines = src.split("\n");
  const out = [];
  for (let i = 0; i < srcLines.length; i++) {
    const line = srcLines[i];
    const open = HEREDOC.exec(line);
    out.push(...highlight(line, "shell"));
    if (!open) continue;

    // Everything up to the terminator belongs to the embedded language. The
    // terminator itself is shell again, so it stays outside the body.
    const tag = open[2];
    const lang = heredocLanguage(line, tag);
    const body = [];
    let j = i + 1;
    for (; j < srcLines.length; j++) {
      if (srcLines[j].trim() === tag) break;
      body.push(srcLines[j]);
    }
    if (body.length) out.push(...highlight(body.join("\n"), lang));
    if (j < srcLines.length) out.push(...highlight(srcLines[j], "shell"));
    i = j;
  }
  return out;
}
