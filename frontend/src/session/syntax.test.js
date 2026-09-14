import { describe, it, expect } from "vitest";
import { pickLanguage, highlight, highlightCommand } from "./syntax.js";

const rt = (src, lang) =>
  highlight(src, lang).map((l) => l.map((t) => t.t).join("")).join("\n");
const classOf = (src, lang, text) => {
  for (const line of highlight(src, lang)) for (const tk of line) if (tk.t === text) return tk.c;
  return undefined;
};

describe("pickLanguage", () => {
  it("maps every extension family", () => {
    const cases = {
      "a/b.py": "python", "x.PYI": "python",
      "s.xml": "xml", "p.html": "xml", "i.svg": "xml",
      "d.json": "json", "n.ipynb": "json",
      "m.js": "js", "c.tsx": "js", "e.mjs": "js",
      "t.css": "css", "u.scss": "css",
      "q.sql": "sql", "y.yml": "yaml", "z.yaml": "yaml",
      "r.md": "markdown", "w.sh": "shell", "v.toml": "toml", "k.conf": "ini",
    };
    for (const [path, lang] of Object.entries(cases)) expect(pickLanguage(path)).toBe(lang);
  });

  it("knows the extensionless names that matter", () => {
    expect(pickLanguage("/srv/Dockerfile")).toBe("shell");
    expect(pickLanguage("Makefile")).toBe("shell");
    expect(pickLanguage(".env")).toBe("ini");
    expect(pickLanguage(".env.local")).toBe("ini");
    expect(pickLanguage(".gitignore")).toBe("plain");
  });

  it("sniffs the text when the path says nothing", () => {
    expect(pickLanguage("", '<?xml version="1.0"?>')).toBe("xml");
    expect(pickLanguage("out", "<html><body>")).toBe("xml");
    expect(pickLanguage("out", '{"a":1}')).toBe("json");
    expect(pickLanguage("out", "#!/usr/bin/env bash\nls")).toBe("shell");
    expect(pickLanguage("out", "just words")).toBe("plain");
  });

  it("gives up rather than guessing", () => {
    expect(pickLanguage("archive.zzz")).toBe("plain");
    expect(pickLanguage(null)).toBe("plain");
    expect(pickLanguage(undefined, undefined)).toBe("plain");
  });
});

/* The round trip is the contract: colour may be wrong, text may not be. */
const SAMPLES = {
  python: `# a comment with a "quote" in it
import os
from x import y

class Thing:
    """doc with # a hash"""
    def run(self, n=3):
        s = 'a # b'
        if n > 1 and self.ok:
            return {"k": [1, 2.5]}
        return None
`,
  xml: `<?xml version="1.0"?>
<!-- a comment with 'quotes' -->
<odoo>
  <record id="a.b" model="ir.ui.view">
    <field name="arch" type="xml">&amp;</field>
    <!-- <field name="dead"/> -->
  </record>
</odoo>
`,
  markdown: `# Title
Some **bold** and *em* text with \`code\`.

- one
- two with [a link](http://x.y)

> quoted # not a heading

\`\`\`py
x = 1
\`\`\`
`,
  js: `// a comment with "quotes"
import x from "y";
const s = 'a // b';
/* block
   comment */
export function run(a, b) {
  return \`t \${a}\`;
}
`,
  json: `{
  "name": "x",
  "n": -12.5e3,
  "ok": true,
  "list": [1, null, "s"],
  "nested": {"a": "b"}
}
`,
  shell: `#!/usr/bin/env bash
# a comment with 'quotes'
set -euo pipefail
export NAME="a # b"
if [ -f "$HOME/.env" ]; then
  cat "\${HOME}/.env" | grep -v '^#' > /tmp/out
fi
`,
  yaml: `# a comment
version: "3.8"
services:
  odoo:
    image: odoo:12
    ports:
      - "8069:8069"
    enabled: true
`,
  css: `/* a comment with "quotes" */
.fds-card {
  border: 1px solid var(--fdx-rule);
  padding: 8px 12px;
}
`,
  sql: `-- a comment
SELECT id, name
FROM messages
WHERE session_id = 'a''b'
GROUP BY id
LIMIT 10;
`,
  toml: `# a comment
[tool.poetry]
name = "x"
version = "1.2.3"
enabled = true
`,
  ini: `; a comment
[server]
host = localhost
port = 8069
`,
};

describe("highlight round trip", () => {
  for (const [lang, src] of Object.entries(SAMPLES)) {
    it(`preserves every character of ${lang}`, () => {
      expect(rt(src, lang)).toBe(src);
    });
  }

  it("preserves plain text and unknown languages", () => {
    const s = "line one\n\nline three";
    expect(rt(s, "plain")).toBe(s);
    expect(rt(s, "brainfuck")).toBe(s);
  });

  it("keeps CRLF, tabs and unicode", () => {
    const s = "a\r\n\tb = 'ước'\r\nc";
    expect(rt(s, "python")).toBe(s);
  });
});

describe("highlight classes", () => {
  it("colours python", () => {
    const s = SAMPLES.python;
    expect(classOf(s, "python", "def")).toBe("kw");
    expect(classOf(s, "python", "run")).toBe("fn");
    expect(classOf(s, "python", "self")).toBe("var");
    expect(classOf(s, "python", "'a # b'")).toBe("str");
    expect(classOf(s, "python", '# a comment with a "quote" in it')).toBe("com");
  });

  it("colours xml", () => {
    const s = SAMPLES.xml;
    expect(classOf(s, "xml", "record")).toBe("tag");
    expect(classOf(s, "xml", "model")).toBe("attr");
    expect(classOf(s, "xml", '"ir.ui.view"')).toBe("str");
    expect(classOf(s, "xml", "&amp;")).toBe("punc");
  });

  it("colours json keys apart from json strings", () => {
    const s = SAMPLES.json;
    expect(classOf(s, "json", '"name"')).toBe("attr");
    expect(classOf(s, "json", '"x"')).toBe("str");
    expect(classOf(s, "json", "true")).toBe("kw");
    expect(classOf(s, "json", "-12.5e3")).toBe("num");
  });

  it("colours shell", () => {
    const s = SAMPLES.shell;
    expect(classOf(s, "shell", "$HOME")).toBe("var");
    expect(classOf(s, "shell", "then")).toBe("kw");
    expect(classOf(s, "shell", "'a # b'")).toBeUndefined();   // inside a double-quoted string
    expect(classOf(s, "shell", '"a # b"')).toBe("str");
  });

  it("colours markdown, yaml, css and sql", () => {
    expect(classOf(SAMPLES.markdown, "markdown", "# Title")).toBe("kw");
    expect(classOf(SAMPLES.markdown, "markdown", "**bold**")).toBe("str");
    expect(classOf(SAMPLES.yaml, "yaml", "version")).toBe("attr");
    expect(classOf(SAMPLES.yaml, "yaml", "true")).toBe("kw");
    expect(classOf(SAMPLES.css, "css", "border")).toBe("attr");
    expect(classOf(SAMPLES.sql, "sql", "SELECT")).toBe("kw");
    expect(classOf(SAMPLES.sql, "sql", "'a''b'")).toBe("str");
  });

  it("only ever uses the agreed class names", () => {
    const allowed = new Set(["kw", "str", "num", "com", "tag", "attr", "punc", "fn", "var", null]);
    for (const [lang, src] of Object.entries(SAMPLES)) {
      for (const line of highlight(src, lang)) {
        for (const tk of line) expect(allowed.has(tk.c), `${lang}: ${tk.c}`).toBe(true);
      }
    }
  });
});

describe("highlight edges", () => {
  it("survives nothing", () => {
    expect(highlight("", "python")).toEqual([[]]);
    expect(highlight(null, "python")).toEqual([[]]);
    expect(highlight(undefined, "xml")).toEqual([[]]);
  });

  it("counts lines the way split does", () => {
    expect(highlight("\n\n", "python")).toHaveLength(3);
  });

  it("does not lose an unterminated construct", () => {
    expect(rt('x = "never closed', "python")).toBe('x = "never closed');
    expect(rt("/* never closed", "js")).toBe("/* never closed");
    expect(rt("<!-- never closed", "xml")).toBe("<!-- never closed");
    expect(rt("'''open", "python")).toBe("'''open");
  });

  it("gives up on a file too large to be read anyway", () => {
    const big = "x = 1\n".repeat(40000);
    expect(big.length).toBeGreaterThan(200000);
    const out = highlight(big, "python");
    expect(out[0]).toEqual([{ t: "x = 1", c: null }]);
  });

  it("flattens a pathological line instead of drawing thousands of spans", () => {
    const line = "{},".repeat(2500);
    const out = highlight(line, "json");
    expect(out).toHaveLength(1);
    expect(out[0]).toHaveLength(1);
    expect(out[0][0].t).toBe(line);
  });

  it("stays fast on a hostile input", () => {
    const hostile = `${'"'.repeat(5000)}\n${"\\".repeat(5000)}\n${"({[".repeat(2000)}`;
    const t0 = Date.now();
    expect(rt(hostile, "js")).toBe(hostile);
    expect(Date.now() - t0).toBeLessThan(500);
  });
});

describe("highlightCommand", () => {
  const CMD = `cd /x && python3 - <<'PYEOF'
import pathlib  # a note
p = pathlib.Path("a.py")
PYEOF
echo ok`;

  it("keeps every character of the command", () => {
    const back = highlightCommand(CMD).map((l) => l.map((t) => t.t).join("")).join("\n");
    expect(back).toBe(CMD);
  });

  it("colours the heredoc body as python, not as shell", () => {
    const lines = highlightCommand(CMD);
    const py = lines[1];
    expect(py[0]).toEqual({ t: "import", c: "kw" });
    expect(py.some((t) => t.c === "com" && t.t.includes("# a note"))).toBe(true);
    expect(lines[2].some((t) => t.c === "str" && t.t === '"a.py"')).toBe(true);
  });

  it("reads the language from the command, then from the tag", () => {
    const js = highlightCommand("node - <<'JSEOF'\nconst a = 1;\nJSEOF");
    expect(js[1].some((t) => t.c === "kw" && t.t === "const")).toBe(true);
    const sql = highlightCommand("psql -c x <<'SQL'\nSELECT 1;\nSQL");
    expect(sql[1].some((t) => t.c === "kw" && t.t === "SELECT")).toBe(true);
    const md = highlightCommand("cat > notes.md <<'EOF'\n# Title\nEOF");
    expect(md[1][0]).toEqual({ t: "# Title", c: "kw" });
  });

  it("survives a heredoc that is never closed", () => {
    const open = "python3 - <<'PYEOF'\nx = 1";
    expect(highlightCommand(open).map((l) => l.map((t) => t.t).join("")).join("\n")).toBe(open);
  });

  it("handles an empty or absent command", () => {
    expect(highlightCommand("")).toEqual([[]]);
    expect(highlightCommand(null)).toEqual([[]]);
  });
});
