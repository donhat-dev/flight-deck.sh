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


def render(source_text: str, *, source_format: str, title: str,
           language: str = "en", kind: str = "report", status: str = "draft",
           font: str = "space-grotesk", custom_head: str | None = None,
           workdir: str) -> dict:
    """Render `source_text` into a self-contained HTML string.

    source_format: "markdown" or "html" (an HTML fragment, not a document).
    font: one of FONTS — a `body.font-{font}` class in tokens.css, so this is
          a plain enum value, never raw markup.
    custom_head: raw HTML spliced in right before `</head>` — NOT passed
                 through pandoc's `-M` (which HTML-escapes metadata text), so
                 arbitrary tags/attributes survive verbatim.
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
    proc = subprocess.run(argv, cwd=str(work), capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:500]}")

    html = proc.stdout
    if custom_head:
        html = html.replace("</head>", f"{custom_head}\n</head>", 1)
    warnings = []
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
    return {"html": html, "bytes": size, "warnings": warnings}


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
                    font: str | None = None, workdir: str) -> dict:
    """Render body-only HTML for a host that owns the page frame.

    The Artifact tool supplies `<!doctype>`, `<head>` and `<body>` and takes only
    what goes INSIDE the body, so a full document loses three things at once: the
    `<body class>` every typography rule hangs off, the page background, and the
    colour mode. This mode hands all three to a wrapper we do control.
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

    warnings = []
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
    return {"html": html, "warnings": warnings,
            "render_bytes": len(html.encode("utf-8"))}
