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
import subprocess
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEMPLATE_FILE = TEMPLATES / "artifact.html"
TOKENS_CSS = TEMPLATES / "tokens.css"
FONTS_DIR = TEMPLATES / "fonts"

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
    for font in FONTS_DIR.glob("*.woff2"):
        shutil.copy2(font, dest_fonts / font.name)

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
