#!/usr/bin/env python3
"""ML-6 web route check.

Every ``/api/...`` path the web client (clients/web/src) requests must exist
in ``api/openapi/python.json`` or ``api/openapi/spend.yaml``.

The check scans .js/.jsx sources with a small tokenizer (comments skipped;
only string and template literals count). Template ``${...}`` interpolations
are resolved two ways:

- a ``${x}`` that fills a whole path segment and cannot be expanded is a path
  parameter -> ``{p}``;
- a ``${x}`` bound to a function parameter is expanded at that function's
  call sites in the same file (``gitOp("fetch")`` -> ``/api/repo/fetch``);
  literal arguments produce literal paths, so a new route suffix a caller
  passes is checked too;
- anything else inside the path part (before ``?``) that cannot be resolved
  fails the check -- no wildcards.

Matching against server paths: equal segments match; a server ``{param}``
segment matches any concrete client segment; a client ``{p}`` only matches a
server ``{param}`` (never a literal, which would hide an unexpanded
interpolation).

Routes the client calls but the server lacks are listed in
``api/conformance/web_routes_allowlist.txt`` (``path\tfile:line``); the list
may only shrink: an entry that stops occurring fails the check.

Usage: check_web_routes.py [--dump] [ROOT]   (ROOT = worktree root)
--dump prints every extracted path with its call site instead of checking.
"""
import json
import re
import sys
from pathlib import Path

import yaml

IDENT = r"[A-Za-z_$][\w$]*"
SIMPLE_EXPR = re.compile(rf"^{IDENT}(?:\.{IDENT}|\[[^\]]+\])*$")
PARAM_WRAP = re.compile(
    rf"^(?:encodeURIComponent|encodeURI|String|Number|encodeURIComponent)\s*\((.*)\)$",
    re.S,
)

# ---------------------------------------------------------------- tokenizer

def scan_file(text):
    """Yield (line, parts) for every string/template literal containing /api/.

    parts is a list of ("lit", text) | ("expr", source, offset) entries.
    """
    i, n = 0, len(text)
    out = []

    def lineof(pos):
        return text.count("\n", 0, pos) + 1

    def read_string(pos, quote):
        """pos at opening ' or ". Returns (text, end_pos_after_quote)."""
        j = pos + 1
        buf = []
        while j < n:
            c = text[j]
            if c == "\\":
                buf.append(text[j + 1] if j + 1 < n else "")
                j += 2
                continue
            if c == quote:
                return "".join(buf), j + 1
            if c == "\n":  # unterminated
                return "".join(buf), j
            buf.append(c)
            j += 1
        return "".join(buf), j

    def read_template(pos):
        """pos at opening `. Returns (parts, end_pos_after_backtick)."""
        j = pos + 1
        parts = []
        buf = []
        while j < n:
            c = text[j]
            if c == "\\":
                buf.append(text[j:j + 2])
                j += 2
                continue
            if c == "`":
                if buf:
                    parts.append(("lit", "".join(buf)))
                return parts, j + 1
            if c == "$" and j + 1 < n and text[j + 1] == "{":
                if buf:
                    parts.append(("lit", "".join(buf)))
                    buf = []
                start = j + 2
                depth = 1
                k = start
                while k < n and depth:
                    ch = text[k]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                    elif ch in "'\"":
                        _, k = read_string(k, ch)
                        continue
                    elif ch == "`":
                        _, k = read_template(k)
                        continue
                    k += 1
                parts.append(("expr", text[start:k - 1], start))
                j = k
                continue
            buf.append(c)
            j += 1
        if buf:
            parts.append(("lit", "".join(buf)))
        return parts, j

    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i = text.find("\n", i)
            if i < 0:
                break
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in "'\"":
            ln = lineof(i)
            s, i = read_string(i, c)
            if "/api/" in s:
                out.append((ln, [("lit", s)]))
            continue
        if c == "`":
            ln = lineof(i)
            parts, i = read_template(i)
            head = parts[0][1] if parts and parts[0][0] == "lit" else ""
            if "/api/" in head:
                out.append((ln, parts))
            continue
        i += 1
    return out

# ----------------------------------------------------- call-site expansion

FUNC_PATS = [
    # const name = (a, b) => / const name = async (a) => / const name = async a =>
    re.compile(rf"(?:const|let|var)\s+({IDENT})\s*=\s*(?:async\s+)?\(([^()]*)\)\s*=>"),
    re.compile(rf"(?:const|let|var)\s+({IDENT})\s*=\s*(?:async\s+)?({IDENT})\s*=>"),
    re.compile(rf"function\s+({IDENT})\s*\(([^()]*)\)"),
    re.compile(rf"(?:async\s+)?({IDENT})\s*=\s*(?:async\s+)?\(([^()]*)\)\s*=>"),
]


def split_args(s):
    """Split a JS argument list on top-level commas."""
    args, depth, buf, j = [], 0, [], 0
    while j < len(s):
        c = s[j]
        if c in "'\"`":
            q = c
            buf.append(c)
            j += 1
            while j < len(s) and s[j] != q:
                if s[j] == "\\":
                    buf.append(s[j:j + 2])
                    j += 2
                    continue
                buf.append(s[j])
                j += 1
            if j < len(s):
                buf.append(s[j])
                j += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        j += 1
    if "".join(buf).strip():
        args.append("".join(buf).strip())
    return args


def enclosing_param(text, pos, name):
    """If `name` is a parameter of the function enclosing `pos`, return
    (func_name, arg_index); else None."""
    best = None
    for pat in FUNC_PATS:
        for m in pat.finditer(text):
            if m.start() >= pos:
                continue
            params = [p.strip() for p in m.group(2).split(",")]
            idx = None
            for k, p in enumerate(params):
                if p == name or (p.startswith("{") and re.search(rf"\b{name}\b", p)):
                    idx = k
            if idx is None:
                continue
            # the function body must actually contain pos: check the next
            # param list or top-level ';'/'function' boundary after m.end()
            if best is None or m.start() > best[0]:
                best = (m.start(), m.group(1), idx)
    if best is None:
        return None
    # verify pos is inside this function: next function start must be after pos
    for pat in FUNC_PATS:
        for m in pat.finditer(text):
            if best[0] < m.start() < pos:
                return None
    return best[1], best[2]


def literal_values(text, fname, idx):
    """Literal first-arg-style values passed to fname(...) at call sites."""
    vals = []
    for m in re.finditer(rf"(?<![\w$.]){re.escape(fname)}\s*\(", text):
        # read the balanced argument list
        j = m.end()
        depth = 1
        buf = []
        while j < len(text) and depth:
            c = text[j]
            if c in "'\"`":
                q = c
                buf.append(c)
                j += 1
                while j < len(text) and text[j] != q:
                    if text[j] == "\\":
                        buf.append(text[j:j + 2])
                        j += 2
                        continue
                    buf.append(text[j])
                    j += 1
                if j < len(text):
                    buf.append(text[j])
                j += 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(c)
            j += 1
        args = split_args("".join(buf))
        if idx < len(args):
            a = args[idx]
            if a and a[0] in "'\"" and a[-1] == a[0]:
                vals.append(a[1:-1])
            elif a and a[0] == "`" and "${" not in a:
                vals.append(a[1:-1])
    return [v for v in dict.fromkeys(vals)]


def eval_expr(expr, text, pos, depth=0):
    """Resolve a ${...} source snippet.

    Returns (values, kind): kind is "lit" (concrete strings), "param"
    (path parameter) or None when unresolvable.
    """
    if depth > 6:
        return None, None
    e = expr.strip()
    if not e:
        return [""], "lit"
    # conditional -> union of both branches (handles `cond ? "?x" : ""`)
    branches = split_ternary(e)
    if branches:
        vals = []
        for branch in branches:
            bv, bk = eval_expr(branch, text, pos, depth + 1)
            if bk == "lit":
                vals.extend(bv)
            elif bk == "param":
                vals.append("\x00P\x00")  # param marker inside a literal
            else:
                return None, None
        return [v for v in dict.fromkeys(vals)], "lit"
    m = PARAM_WRAP.match(e)
    if m:
        return eval_expr(m.group(1), text, pos, depth + 1)
    if e and e[0] in "'\"`" and e[-1] == e[0]:
        inner = e[1:-1]
        if "${" in inner:
            return expand_inline_template(inner, text, pos, depth + 1)
        return [inner], "lit"
    if SIMPLE_EXPR.match(e):
        name = e.split(".")[0].split("[")[0]
        ep = enclosing_param(text, pos, name)
        if ep:
            vals = literal_values(text, ep[0], ep[1])
            if vals:
                return vals, "lit"
        return None, "param"
    return None, None


def _top_level_ops(s):
    """Yield (index, char) for ?, : outside strings/nested brackets."""
    depth, i = 0, 0
    while i < len(s):
        c = s[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < len(s) and s[i] != q:
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in "?:":
            yield i, c
        i += 1


def split_ternary(e):
    """Return [then, else] when `e` is `cond ? a : b` at top level, else None.
    Ignores `?.` and `??`."""
    ops = [(i, c) for i, c in _top_level_ops(e)]
    q = [i for i, c in ops
         if c == "?" and e[i:i + 2] not in ("?.", "??")]
    if len(q) != 1:
        return None
    qi = q[0]
    colons = [i for i, c in ops if c == ":" and i > qi]
    if len(colons) != 1:
        return None
    then, otherwise = e[qi + 1:colons[0]], e[colons[0] + 1:]
    return [then, otherwise]


def expand_inline_template(inner, text, pos, depth):
    """Evaluate a nested `a${x}b` template to literal strings ('\\x00P\\x00'
    marks a param position)."""
    out = [""]
    i = 0
    while i < len(inner):
        if inner.startswith("${", i):
            j = inner.find("}", i)
            vals, kind = eval_expr(inner[i + 2:j], text, pos, depth + 1)
            if kind == "lit":
                out = [o + v for o in out for v in vals]
            elif kind == "param":
                out = [o + "\x00P\x00" for o in out]
            else:
                return None, None
            i = j + 1
        else:
            out = [o + inner[i] for o in out]
            i += 1
    return out, "lit"


# ------------------------------------------------------------- path build

def build_paths(parts, text):
    """Turn a template's parts into candidate path strings ('{p}' marks a
    parameter segment). Returns (paths, unresolved_exprs)."""
    candidates = [""]
    unresolved = []
    done = False
    for kind, *rest in parts:
        if done:
            break
        if kind == "lit":
            s = rest[0]
            if "?" in s:
                s = s[: s.index("?")]
                done = True
            candidates = [c + s for c in candidates]
            continue
        expr, pos = rest
        vals, ek = eval_expr(expr, text, pos)
        if ek == "lit":
            new = []
            for c in candidates:
                for v in vals:
                    seg = c + v
                    # a literal value containing '?' ends the path part
                    new.append(seg)
            candidates = new
        elif ek == "param":
            candidates = [c + "\x00P\x00" for c in candidates]
        else:
            unresolved.append(expr.strip()[:60])
    out = []
    for c in candidates:
        path = c.split("?", 1)[0]
        out.append(path)
    return out, unresolved


def normalize(path):
    """'{p}'-mark every \\x00P\\x00 segment; drop query, collapse slashes."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    segs = [s for s in path.split("/") if s != ""]
    norm = []
    for s in segs:
        if s == "\x00P\x00":
            norm.append("{p}")
        elif "\x00P\x00" in s:
            # param fused with literal text -> whole segment a param
            norm.append("{p}")
        else:
            norm.append(s)
    return "/" + "/".join(norm)


def matches(client_path, server_paths):
    cp = [s for s in client_path.split("/") if s]
    for sp in server_paths:
        ss = [s for s in sp.split("/") if s]
        if len(cp) != len(ss):
            continue
        ok = True
        for c, s in zip(cp, ss):
            if c == s or (s.startswith("{") and c != ""):
                # client {p} vs server literal is NOT allowed (wildcard hole)
                if c == "{p}" and not s.startswith("{"):
                    ok = False
                    break
                continue
            ok = False
            break
        if ok:
            return True
    return False


def main():
    args = [a for a in sys.argv[1:] if a != "--dump"]
    dump = "--dump" in sys.argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    src = root / "clients" / "web" / "src"
    server_paths = set(json.load(open(root / "api" / "openapi" / "python.json"))["paths"])
    server_paths |= set(yaml.safe_load(open(root / "api" / "openapi" / "spend.yaml"))["paths"])

    allow_file = root / "api" / "conformance" / "web_routes_allowlist.txt"
    allowed = {}
    if allow_file.exists():
        for line in allow_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                allow_file_path, _, site = line.partition("\t")
                allowed[allow_file_path.strip()] = site.strip()

    seen = {}          # normalized path -> first call site
    unresolved = []    # (file, line, expr)
    for f in sorted(src.rglob("*")):
        if f.suffix not in (".js", ".jsx"):
            continue
        text = f.read_text()
        for line, parts in scan_file(text):
            raws, unres = build_paths(parts, text)
            for raw in raws:
                p = normalize(raw)
                if p.startswith("/api"):
                    seen.setdefault(p, f"{f.relative_to(src)}:{line}")
            for e in unres:
                unresolved.append(f"{f.relative_to(src)}:{line}: ${{{e}}}")

    if dump:
        for p, site in sorted(seen.items()):
            mark = "" if matches(p, server_paths) else "  <-- UNMATCHED"
            print(f"{p}  <- {site}{mark}")
        for u in unresolved:
            print(f"UNRESOLVED {u}")
        return 0

    unmatched = {p: s for p, s in seen.items() if not matches(p, server_paths)}
    new_violations = {p: s for p, s in unmatched.items() if p not in allowed}
    stale = {p: s for p, s in allowed.items() if p not in unmatched}

    rc = 0
    for p, site in sorted(new_violations.items()):
        print(f"UNMATCHED {p}  ({site})")
        rc = 1
    for u in unresolved:
        print(f"UNRESOLVED {u}")
        rc = 1
    for p, site in sorted(stale.items()):
        print(f"STALE-ALLOWLIST {p}  ({site})")
        rc = 1
    print(f"checked {len(seen)} distinct /api paths; "
          f"{len(unmatched)} unmatched ({len(unmatched) - len(new_violations)} allowlisted)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
