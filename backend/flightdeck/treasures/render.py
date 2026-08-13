"""Wrap content into a self-contained artifact with pandoc.

One transform, not a chain: `content (markdown | html fragment) + template +
tokens.css` -> a single HTML file with every asset inlined as a data URI.
Verified during design: pandoc's --embed-resources recurses into the linked
stylesheet, so `@font-face url(x.woff2)` becomes data:font/woff2;base64 and no
external reference survives.

pandoc resolves relative asset paths against its working directory, so the
template dir is copied into the caller's workdir before invoking it.
"""
import os
import re
import shutil
import base64
import subprocess
from html import escape as html_escape
from pathlib import Path

from . import lint

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEMPLATE_FILE = TEMPLATES / "artifact.html"
TOKENS_CSS = TEMPLATES / "tokens.css"
FONTS_DIR = TEMPLATES / "fonts"
TABLE_WRAP_LUA = TEMPLATES / "wrap-tables.lua"

# claude.ai caps a rendered artifact at 16 MiB; warn from 80% up.
SIZE_WARN_BYTES = int(0.8 * 16 * 1024 * 1024)

# src=/<link href=/url() pointing at a real host — things the browser fetches
# passively on load, which --embed-resources was supposed to inline. A plain
# <a href> is deliberately NOT matched: it is user-initiated navigation (a
# citation link), never a passive fetch, so it does not break self-containment
# — same reasoning as excluding w3.org below, applied to a different false
# positive (a report citing another artifact was wrongly flagged "not
# self-contained" for the mere presence of that link).
EXTERNAL_REF_RE = re.compile(
    r"""src\s*=\s*["'](?!data:)https?://(?!www\.w3\.org/)[^"']+"""
    r"""|<link\b[^>]*\bhref\s*=\s*["'](?!data:)https?://(?!www\.w3\.org/)[^"']+"""
    r"""|url\(\s*["']?(?!data:)https?://(?!www\.w3\.org/)[^)"']+""",
    re.IGNORECASE)

# Remote assets pandoc will actually fetch during the build: a markdown image
# (`![alt](url)`) or a raw HTML `src=` attribute. A plain markdown/HTML link
# (`[text](url)` / `<a href=url>`) is excluded on purpose — pandoc never fetches
# it, so warning "fetched remote asset" about it would be false: no fetch
# happens. Excludes w3.org for the same reason EXTERNAL_REF_RE does.
_REMOTE_IN_SOURCE_RE = re.compile(
    r"""(?:!\[[^\]]*\]\(|\bsrc\s*=\s*["']?)"""
    r"""(https?://(?!www\.w3\.org/)[^\s)\"'<>]+)""",
    re.IGNORECASE)


# A leading YAML frontmatter block: `---`, key/value lines, closing `---`.
# Required to look like YAML (at least one `key:` line) so a document opening
# with a genuine horizontal rule is left alone.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>(?:.*\r?\n)*?)---[ \t]*\r?\n", re.MULTILINE)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block. It is metadata, not document body,
    and the title/lang/kind we render with are passed explicitly."""
    m = _FRONTMATTER_RE.match(text)
    if m and re.search(r"^[A-Za-z_][\w-]*\s*:", m.group("body"), re.MULTILINE):
        return text[m.end():]
    return text


def font_paths() -> list[str]:
    """The woff2 files tokens.css references. Used by the coverage test."""
    return sorted(str(p) for p in FONTS_DIR.glob("*.woff2"))


_FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.S)


def _face_family(block: str) -> str:
    m = re.search(r"font-family:\s*['\"]([^'\"]+)['\"]", block)
    return m.group(1) if m else ""


def _face_ranges(block: str) -> list[tuple[int, int]]:
    """The face's `unicode-range`, as inclusive codepoint pairs.

    An empty list means the face declared no range, which is a claim that it
    covers everything — so it can never be proven unused and is always kept.
    """
    m = re.search(r"unicode-range:\s*([^;}]+)", block)
    if not m:
        return []
    out = []
    for part in m.group(1).split(","):
        part = part.strip().lstrip("Uu+")
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            out.append((int(lo, 16), int(hi or lo, 16)))
        except ValueError:
            return []          # unparsable: treat as "covers everything"
    return out


def _reachable_families(font: str, markup: str) -> set[str]:
    """Which embedded families this document can possibly render with.

    Two of the three are unreachable unless something specific is true: JetBrains
    Mono only when it IS the body font (code spans take the system mono, not this
    face), and Playfair only through the hero component's accent line, which is
    the single selector in the sheet that names it.
    """
    fams = set()
    if font == "space-grotesk":
        fams.add("Space Grotesk")
    elif font == "jetbrains-mono":
        fams.add("JetBrains Mono")
    if 'data-component="hero"' in markup:
        fams.add("Playfair Display")
    return fams


def _visible_codepoints(html: str) -> set[int]:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return {ord(c) for c in re.sub(r"<[^>]+>", " ", body)}


def prune_faces(html: str, *, font: str) -> tuple[str, list[str]]:
    """Drop every @font-face the document cannot render a character with.

    Two independent conditions, both of which have to hold for a face to stay:
    its family has to be reachable at all, and at least one character actually in
    the document has to fall inside the face's `unicode-range`. The second is what
    removes a Vietnamese subset from an English document without anyone declaring
    a language, and it is read off the sheet's own ranges rather than a hardcoded
    table, so a new subset is covered the day it is added.

    Fail-open by construction: a face with no parsable range is kept, and so is
    every face when the caller cannot tell which family is in play.

    The hero check runs against the markup with `<style>` stripped out, not the
    whole `html` string: tokens.css spells its hero rule as the literal selector
    text `[data-component="hero"] h1 em { ... }`, which the embedded stylesheet
    carries verbatim in every render regardless of whether the document uses a
    hero. Checking the raw `html` therefore always finds the substring and never
    prunes Playfair — verified against a real render of a plain English,
    no-hero document, which kept 2 faces instead of 1 before this strip was
    added. Same shape of fix `_visible_codepoints` already applies for its own
    text scan, just missing here.
    """
    fams = _reachable_families(font, re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S))
    cps = _visible_codepoints(html)
    dropped, kept_bytes, out = [], 0, html
    for block in _FACE_RE.findall(html):
        fam = _face_family(block)
        ranges = _face_ranges(block)
        used = fam in fams and (not ranges or any(
            lo <= cp <= hi for cp in cps for lo, hi in ranges))
        if used:
            kept_bytes += len(block)
            continue
        src = re.search(r"fonts/([\w.-]+\.woff2)", block)
        dropped.append(f"{fam} ({src.group(1) if src else '?'})")
        out = out.replace(block, "", 1)
    notes = []
    if dropped:
        saved = len(html) - len(out)
        notes.append(f"pruned {len(dropped)} unused font face(s), {saved:,} chars: "
                     + ", ".join(dropped))
    return out, notes


def move_faces_last(html: str) -> str:
    """Put the @font-face rules at the END of the document instead of the head.

    Nothing about the rendering changes — verified in a browser: same resolved
    family, same face loaded, identical heading geometry — because `@font-face` is
    document-scoped wherever its `<style>` sits. What changes is the order a
    READER meets the file in. With the faces in the head, the first word of the
    document sat at 90% of the file, so any truncated read (an agent fetching the
    artifact gets a head-truncated preview) spent itself on base64 before reaching
    a heading. Moved to the end, the document starts at about 7%.
    """
    faces = _FACE_RE.findall(html)
    if not faces:
        return html
    for block in faces:
        html = html.replace(block, "", 1)
    tail = "<style>" + "".join(faces) + "</style>"
    if "</body>" in html:
        return html.replace("</body>", tail + "\n</body>", 1)
    return html + "\n" + tail          # fragment mode owns no </body>


_RAW_SRC_RE = re.compile(
    r"""<(?:img|iframe|embed|source|video|audio|script|link|object)\b[^>]*?"""
    r"""\s(?:src|href|data|poster)\s*=\s*["']([^"']+)["']""", re.I)

_SRC_OK_PREFIX = ("http://", "https://", "data:", "mailto:", "//", "#")


def _refuse_unreadable_src(source_text: str, work: Path) -> None:
    """Refuse a raw-HTML asset reference pandoc would try to read off disk.

    `--embed-resources` inlines what the markup points at, which means a relative
    or root-relative path is a FILE READ, not a URL. A document that quotes a
    deep link — `<iframe src="/nakivo/embed/...">` written unfenced — therefore
    kills the whole render, and the message pandoc gives back names neither the
    document nor the tag. Refusing here, with the offending value in the text,
    turns that into something the author can act on. `lint.ComponentError` because
    it is the one error the API already answers with 400 rather than 500.
    """
    for value in _RAW_SRC_RE.findall(source_text):
        v = value.strip()
        if not v or v.lower().startswith(_SRC_OK_PREFIX):
            continue
        if (work / v.lstrip("/")).is_file() or Path(v).is_file():
            continue
        raise lint.ComponentError(
            f"raw HTML points at {v!r}, which is not a file pandoc can read — "
            f"--embed-resources treats it as a path, not a URL, and the render "
            f"aborts. Fence it as code, make it a plain link, or place the asset "
            f"in the artifact's assets/ directory.")


def pandoc_path() -> str:
    """Resolve the pandoc binary: env override, ~/.flightdeck/bin, then PATH."""
    env = os.environ.get("TREASURES_PANDOC")
    candidates = [env] if env else []
    candidates.append(str(Path.home() / ".flightdeck" / "bin" / "pandoc"))
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    found = shutil.which("pandoc")
    if found:
        return found
    raise RuntimeError(
        "pandoc not found. Run `make pandoc` (installs the pinned static "
        "binary into ~/.flightdeck/bin, no root needed) or set TREASURES_PANDOC.")


FONTS = ("default", "space-grotesk", "jetbrains-mono")


_MAIN_OPEN = '<main class="doc">'
_MAIN_CLOSE = "</main>"


def inject_body_defaults(html: str, *, header: str | None, footer: str | None,
                         notes: str | None) -> str:
    """Splice the site-wide defaults into the document body.

    Anchored on `<main class="doc">` and `</main>`, which both the standalone
    template and the fragment assembly emit — so one implementation serves both
    modes rather than two that can drift.

    The notes go in a COLLAPSED <details>, and that is a measured choice, not a
    style preference: `<details>` content is the only channel that survives every
    extraction path an agent might use (a `<meta>` tag, a JSON-LD block, an HTML
    comment and an `aria-label` were each measured LOST through `pandoc html ->
    plain`), while collapsed it costs one line of the page. Real DOM text is what
    makes it readable; the `id` is what makes it findable.

    Returns `html` UNCHANGED when nothing is configured, or when the anchor is
    missing (the caller is the one that turns that second case into a visible
    warning, by noticing the string came back byte-identical).
    """
    if not header and not footer and not notes:
        return html
    if _MAIN_OPEN not in html or _MAIN_CLOSE not in html:
        return html
    if header:
        html = html.replace(_MAIN_OPEN, _MAIN_OPEN + header, 1)
    tail = footer or ""
    if notes:
        # Escaped-as-text inside a <pre>, never run through pandoc: the notes
        # are markdown TEXT, and a <pre> is what keeps their own line breaks
        # while guaranteeing nothing an author wrote can inject markup.
        tail += (
            '<details id="agent-notes"><summary>Agent notes</summary>\n'
            '<div class="agent-notes-body">\n'
            f"<pre>{html_escape(notes)}</pre>\n"
            "</div>\n</details>")
    if tail:
        html = html.replace(_MAIN_CLOSE, tail + _MAIN_CLOSE, 1)
    return html


def render(source_text: str, *, source_format: str, title: str,
           language: str = "en", kind: str = "report", status: str = "draft",
           font: str = "space-grotesk", custom_head: str | None = None,
           default_header_html: str | None = None,
           default_footer_html: str | None = None,
           agent_notes: str | None = None,
           workdir: str) -> dict:
    """Render `source_text` into a self-contained HTML string.

    source_format: "markdown" or "html" (an HTML fragment, not a document).
    font: one of FONTS — a `body.font-{font}` class in tokens.css, so this is
          a plain enum value, never raw markup.
    custom_head: raw HTML spliced in right before `</head>` — NOT passed
                 through pandoc's `-M` (which HTML-escapes metadata text), so
                 arbitrary tags/attributes survive verbatim. PER-ARTIFACT.
    default_header_html, default_footer_html, agent_notes: the SITE-WIDE
                 Treasures defaults (treasures/store.py CONFIG_KEYS), spliced
                 into the visible <body> via `inject_body_defaults` — do NOT
                 confuse these with `custom_head` above: that one is
                 per-artifact and targets <head>, these three are site-wide
                 and target <body>. All default to None so every existing
                 caller/test keeps its current output.
    workdir: a real directory; the template + fonts are copied in so pandoc can
             resolve the relative asset paths, and any `assets/` the caller has
             already placed there is picked up too.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TOKENS_CSS, work / "tokens.css")
    dest_fonts = work / "fonts"
    dest_fonts.mkdir(exist_ok=True)
    for font_file in FONTS_DIR.glob("*.woff2"):
        shutil.copy2(font_file, dest_fonts / font_file.name)

    ext = "md" if source_format == "markdown" else "html"
    src = work / f"source.{ext}"
    body = _strip_frontmatter(source_text) if source_format == "markdown" else source_text
    src.write_text(body, encoding="utf-8")

    argv = [pandoc_path(), src.name]
    if source_format == "html":
        argv += ["-f", "html"]
    else:
        # Disable the YAML metadata block: real documents (SKILL.md, specs) open
        # with frontmatter whose unquoted `key: value with: colons` is invalid
        # YAML, and pandoc would abort the whole render. Title/lang/kind come
        # from -M anyway, so the block carries nothing we need.
        argv += ["-f", "markdown-yaml_metadata_block"]
    argv += [
        "--standalone", "--embed-resources",
        # Never reflow output text. pandoc's default --wrap=auto breaks lines
        # at ~72 columns even inside a passed-through raw HTML block — verified
        # to inject a raw newline into an inline <svg>'s <text> content (safe
        # there, since SVG collapses whitespace, but the same reflow landing
        # inside a <style> block's CSS string would be a syntax error). Output
        # is machine-read HTML, never eyeballed as source, so there is no
        # downside to leaving it unwrapped.
        "--wrap=none",
        "--template", str(TEMPLATE_FILE),
        "--lua-filter", str(TABLE_WRAP_LUA),
        "-c", "tokens.css",
        "-M", f"title={title}",
        "-M", f"lang={language}",
        "-M", f"kind={kind}",
        "-M", f"status={status}",
        "-M", f"font={font}",
    ]
    _refuse_unreadable_src(source_text, work)
    proc = subprocess.run(argv, cwd=str(work), capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:500]}")

    html = proc.stdout
    if custom_head:
        html = html.replace("</head>", f"{custom_head}\n</head>", 1)
    before_defaults = html
    html = inject_body_defaults(html, header=default_header_html,
                                footer=default_footer_html, notes=agent_notes)
    anchor_missing = bool(
        (default_header_html or default_footer_html or agent_notes)
        and html == before_defaults)
    html, pruned = prune_faces(html, font=font)
    html = move_faces_last(html)
    warnings = []
    if anchor_missing:
        warnings.append(
            'could not place body defaults: no <main class="doc"> anchor')
    if proc.stderr.strip():
        warnings.append(f"pandoc: {proc.stderr.strip()[:300]}")
    for url in sorted(set(_REMOTE_IN_SOURCE_RE.findall(source_text))):
        warnings.append(f"fetched remote asset during wrap: {url}")
    leftovers = EXTERNAL_REF_RE.findall(html)
    if leftovers:
        warnings.append(
            f"{len(leftovers)} external reference(s) survived — the artifact is "
            f"NOT self-contained: {leftovers[:3]}")
    size = len(html.encode("utf-8"))
    if size > SIZE_WARN_BYTES:
        warnings.append(
            f"rendered size {size / 1048576:.1f} MiB approaches the 16 MiB cap")
    return {"html": html, "bytes": size, "warnings": warnings, "pruned": pruned}


# ---------------------------------------------------------------- fragment mode

# Selectors the token sheet aims at the document root or at <body>. In a fragment
# neither element belongs to us: the Artifact frame supplies <body>, and we cannot
# put a class on it. Each one moves to the wrapper.
_FRAGMENT_SELECTOR_MAP = (
    ("html, body { background: #fff; }", ".doc-root { background: #fff; }"),
    ("html { background: var(--paper); }",
     # `isolation: isolate` is load-bearing, not decoration. The sheet layers the
     # paper on <html> and keeps <body> transparent so the `z-index:-1` aurora can
     # sit between them, and that works only because the ROOT element's background
     # is painted as the canvas — before any negative-z descendant. A plain <div>
     # has no such privilege: its background is painted with the in-flow blocks,
     # i.e. AFTER negative-z children, which would bury the aurora underneath it.
     # Making the wrapper its own stacking context restores the order
     # (own background -> negative-z children -> content) with one element instead
     # of two. Measured, not assumed: without it the wash is invisible.
     ".doc-root { background: var(--paper); isolation: isolate; }"),
    ("body::before", ".doc-root::before"),
    ("body::after", ".doc-root::after"),
    ("body.font-", ".doc-root.font-"),
    ("body {", ".doc-root {"),
)


def _inline_fonts(css: str) -> str:
    """Replace `url('fonts/x.woff2')` with a data: URI.

    `--embed-resources` does this for us in document mode, but it only applies to a
    stylesheet pandoc itself pulls in via `-c`, and a fragment has no <head> to link
    from. Without this the fonts would be the one external reference left in an
    artifact that claims to be self-contained.
    """
    def sub(m):
        name = m.group("name")
        data = (FONTS_DIR / name).read_bytes()
        return ("url('data:font/woff2;base64,"
                + base64.b64encode(data).decode("ascii") + "')")

    return re.sub(r"""url\(\s*['"]fonts/(?P<name>[\w.-]+\.woff2)['"]\s*\)""", sub, css)


def fragment_css() -> str:
    """The token sheet, rewritten so it can live inside someone else's page."""
    css = TOKENS_CSS.read_text(encoding="utf-8")

    # `body { background: transparent }` is deliberate in the document build — it
    # is what lets the aurora show through to the paper on <html>. With both
    # elements collapsed into one wrapper it becomes a self-override: the body
    # block comes second and would erase the paper the html block just set, which
    # is exactly what left the wrapper transparent the first time. The assertion
    # guards the assumption that there is only one such declaration to remove.
    assert css.count("background: transparent;") == 1, (
        "tokens.css gained another `background: transparent` — check which rule "
        "it belongs to before removing it here")
    css = css.replace("  background: transparent;\n", "")
    # Comments go first, for two reasons: they are dead weight in a published
    # artifact, and the sheet explains its own document-mode decisions in prose that
    # mentions `<html>` and `<body>` — which made the frame check below cry wolf.
    # Stripping before the fonts are inlined keeps the regex away from base64 (`*`
    # is not in the base64 alphabet, but there is no reason to run it over 140 KB of
    # payload either).
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = _inline_fonts(css)

    # The scroll-progress bar is driven by `animation-timeline: scroll(root)`, so in
    # a fragment it would report the HOST page's scroll, not the document's. Dropped
    # rather than re-pointed: there is no element in a fragment that owns the
    # document's own scroll.
    css = re.sub(r"html::before\s*\{[^}]*\}", "", css)
    css = re.sub(r"@keyframes fd-scroll-progress\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)

    for old, new in _FRAGMENT_SELECTOR_MAP:
        css = css.replace(old, new)

    # Tokens move OFF :root and onto the wrapper, then are repeated under both theme
    # attributes. On the wrapper they already beat anything an ancestor sets, because
    # a custom property declared closer to the element wins for its subtree. The two
    # repeats exist for the aggressive host rule — `[data-theme="dark"] .doc-root`
    # (0,2,0) outranks a bare `.doc-root` (0,1,0), so without them a viewer in dark
    # mode could still recolour the document.
    css = css.replace(
        ":root {",
        '.doc-root,\n[data-theme="light"] .doc-root,\n[data-theme="dark"] .doc-root {\n'
        "  /* Pinned light. The house look has no dark variant, and a viewer's dark\n"
        "     mode would otherwise repaint form controls and the canvas around type\n"
        "     that stayed dark-on-light. */\n"
        "  color-scheme: light;",
        1)
    return css


def render_fragment(source_text: str, *, source_format: str, title: str,
                    kind: str = "report", status: str = "draft",
                    font: str | None = None,
                    default_header_html: str | None = None,
                    default_footer_html: str | None = None,
                    agent_notes: str | None = None,
                    workdir: str) -> dict:
    """Render body-only HTML for a host that owns the page frame.

    The Artifact tool supplies `<!doctype>`, `<head>` and `<body>` and takes only
    what goes INSIDE the body, so a full document loses three things at once: the
    `<body class>` every typography rule hangs off, the page background, and the
    colour mode. This mode hands all three to a wrapper we do control.

    default_header_html/default_footer_html/agent_notes: same SITE-WIDE
    defaults as `render()` above (see its docstring for the custom_head
    distinction) — spliced into this fragment's own `<main class="doc">` via
    `inject_body_defaults`.
    """
    font = font or "space-grotesk"
    if font not in FONTS:
        raise ValueError(f"unknown font {font!r} — one of {', '.join(FONTS)}")

    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    ext = "md" if source_format == "markdown" else "html"
    src = work / f"source.{ext}"
    body = _strip_frontmatter(source_text) if source_format == "markdown" else source_text
    src.write_text(body, encoding="utf-8")

    argv = [pandoc_path(), src.name]
    argv += ["-f", "html"] if source_format == "html" else \
            ["-f", "markdown-yaml_metadata_block"]
    # No --standalone and no --template: those are what produce the frame we must
    # not emit. --embed-resources still inlines images referenced from the content.
    argv += ["--embed-resources", "--wrap=none", "-t", "html5",
             "--lua-filter", str(TABLE_WRAP_LUA)]
    _refuse_unreadable_src(source_text, work)
    proc = subprocess.run(argv, cwd=str(work), capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:500]}")

    inner = proc.stdout.strip()
    css = fragment_css()
    html = (
        f"<style>\n{css}\n</style>\n"
        f'<div class="doc-root kind-{kind} font-{font}">\n'
        f'<nav>\n  <span class="brand">{html_escape(title)}</span>\n'
        f'  <span class="tag tag-{status}">{html_escape(status)}</span>\n</nav>\n'
        f'<main class="doc">\n{inner}\n</main>\n'
        f"</div>\n"
    )
    before_defaults = html
    html = inject_body_defaults(html, header=default_header_html,
                                footer=default_footer_html, notes=agent_notes)
    anchor_missing = bool(
        (default_header_html or default_footer_html or agent_notes)
        and html == before_defaults)
    html, pruned = prune_faces(html, font=font)
    html = move_faces_last(html)

    warnings = []
    if anchor_missing:
        warnings.append(
            'could not place body defaults: no <main class="doc"> anchor')
    if proc.stderr.strip():
        warnings.append(f"pandoc: {proc.stderr.strip()[:300]}")
    # Only the MARKUP can carry a frame; the embedded CSS legitimately contains the
    # strings. Checking the whole output reported a frame that was not there.
    markup = html[html.index("</style>"):].lower()
    for tag in ("<!doctype", "<html", "<head", "<body"):
        if tag in markup:
            warnings.append(f"fragment still contains {tag} — the host owns that")
    leftovers = EXTERNAL_REF_RE.findall(html)
    if leftovers:
        warnings.append(
            f"{len(leftovers)} external reference(s) survived — NOT self-contained: "
            f"{leftovers[:3]}")
    return {"html": html, "warnings": warnings, "pruned": pruned,
            "render_bytes": len(html.encode("utf-8"))}
