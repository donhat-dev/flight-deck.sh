"""Wrap content into a self-contained artifact with pandoc.

One transform, not a chain: `content (markdown | html fragment) + template +
tokens.css` -> a single HTML file with every asset inlined as a data URI.
Verified during design: pandoc's --embed-resources recurses into the linked
stylesheet, so `@font-face url(x.woff2)` becomes data:font/woff2;base64 and no
external reference survives.

pandoc resolves relative asset paths against its working directory, so the
template dir is copied into the caller's workdir before invoking it.
"""
import json
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


# ------------------------------------------------------------- source assets
#
# A source references its own sibling files as plain relative paths (a
# diagram sitting next to the markdown that embeds it). pandoc resolves those
# paths against ITS OWN cwd, which is the throwaway workdir this module
# builds per render — not the source's directory — so a reference that is
# perfectly valid on disk resolved to nothing and `--embed-resources` left
# the tag untouched, shipping a broken image. Copying the referenced files
# into the workdir first, under the same relative path, is what makes pandoc
# resolve them exactly as the author wrote them.

_MD_IMG_RE = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
_HTML_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', re.I)
_IGNORE_ASSET_PREFIXES = ("data:", "http:", "https:", "//", "#")


def _local_asset_refs(text: str) -> list[str]:
    """Local asset paths referenced from markdown `![alt](path)` or raw HTML
    `<img src="path">`. A remote URL, a data URI, a protocol-relative
    reference or a same-page anchor is never a file pandoc has to read, so
    each is excluded rather than chased."""
    refs = [m.group(1) for m in _MD_IMG_RE.finditer(text)]
    refs += [m.group(1) for m in _HTML_IMG_SRC_RE.finditer(text)]
    seen: list[str] = []
    for r in refs:
        if r.lower().startswith(_IGNORE_ASSET_PREFIXES):
            continue
        if r not in seen:
            seen.append(r)
    return seen


def _resolves_inside(base: Path, candidate: Path) -> bool:
    """Same escape guard as
    .claude/skills/artifact-build/scripts/inline_assets.py's `asset_sub`:
    `base` must be the candidate itself or one of its parents."""
    return candidate == base or base in candidate.parents


def _copy_local_assets(source_text: str, asset_dir: str | None, work: Path) -> list[str]:
    """Copy every local asset `source_text` references from `asset_dir` into
    `work`, preserving the relative path exactly as written, so pandoc
    resolves it exactly as the author wrote it.

    A reference that resolves outside `asset_dir` is refused and reported —
    never copied, same fail-closed shape as `_refuse_unreadable_src` below.
    A reference that resolves inside `asset_dir` but names a file that does
    not exist is reported too: today that fails silently into a broken
    `<img>`, which is the whole defect this function exists to close.
    """
    warnings: list[str] = []
    if not asset_dir:
        return warnings
    base = Path(asset_dir).resolve()
    if not base.is_dir():
        return warnings
    for rel in _local_asset_refs(source_text):
        candidate = (base / rel).resolve()
        if not _resolves_inside(base, candidate):
            warnings.append(
                f"asset {rel!r} resolves outside its source directory "
                f"({base}) — refusing to copy it")
            continue
        if not candidate.is_file():
            warnings.append(f"referenced asset not found: {rel}")
            continue
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, dest)
    return warnings


# ------------------------------------------------------------- SVG inlining

_SVG_OPEN_RE = re.compile(r"<svg\b([^>]*)>", re.I)

# Two shapes worth matching, tried in this order at each position:
#
# 1. Pandoc's own "implicit figure" wrapper. A markdown image standing alone
#    in its own paragraph — exactly how every diagram in this document is
#    written — gets auto-wrapped by pandoc as
#    `<figure><img .../><figcaption aria-hidden="true">ALT</figcaption></figure>`
#    *before* this function ever runs. Replacing only the inner `<img>` would
#    leave that wrapper in place around our own `<figure class="diagram">`,
#    nesting one figure inside another with two copies of the caption — verified
#    to also make pandoc's own `html -> plain` drop BOTH captions rather than
#    keep either. Matching and replacing the whole wrapper is what avoids that.
# 2. A bare `<img>` with no such wrapper — what a caller passes directly (the
#    test suite), or an image that shares a paragraph with other text, which
#    pandoc never auto-wraps.
_IMG_TAG_RE = re.compile(
    r"<figure>\s*(?P<img1><img\b[^>]*>)\s*<figcaption\b[^>]*>.*?</figcaption>\s*</figure>"
    r"|(?P<img2><img\b[^>]*>)",
    re.I | re.S)
_ID_ATTR_RE = re.compile(r"""\bid=(["'])([^"']+)\1""")
_URL_REF_RE = re.compile(r"""url\((["']?)#([^)"']+)\1\)""")
_HREF_REF_RE = re.compile(r"""\b(xlink:href|href)=(["'])#([^"']+)\2""")


def _attr_val(tag_or_attrs: str, name: str) -> str | None:
    m = (re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag_or_attrs, re.I) or
         re.search(rf"\b{name}\s*=\s*'([^']*)'", tag_or_attrs, re.I))
    return m.group(1) if m else None


def _drop_attr(attrs: str, name: str) -> str:
    attrs = re.sub(rf'\s+{name}\s*=\s*"[^"]*"', "", attrs, flags=re.I)
    attrs = re.sub(rf"\s+{name}\s*=\s*'[^']*'", "", attrs, flags=re.I)
    return attrs


def _resolve_svg_bytes(src: str, asset_dir: str | None) -> bytes | None:
    """The raw SVG bytes an `<img>` src points at, or None when it is not an
    inlineable local SVG.

    Two shapes count: a `data:image/svg+xml;base64,...` URI (what
    `--embed-resources` produces once `_copy_local_assets` has put the file
    where pandoc can see it) and a still-unresolved relative `*.svg` path
    (the file existed but, for whatever reason, was never embedded — e.g. a
    caller that inlines without having run the asset-copy step first).
    """
    low = src.lower()
    if low.startswith("data:image/svg+xml"):
        if ";base64," not in src:
            return None
        try:
            return base64.b64decode(src.split(";base64,", 1)[1])
        except Exception:
            return None
    if low.startswith(_IGNORE_ASSET_PREFIXES):
        return None
    if not low.endswith(".svg") or not asset_dir:
        return None
    base = Path(asset_dir).resolve()
    if not base.is_dir():
        return None
    candidate = (base / src).resolve()
    if not _resolves_inside(base, candidate) or not candidate.is_file():
        return None
    return candidate.read_bytes()


def _namespace_ids(svg_text: str, prefix: str) -> str:
    """Rewrite every `id="x"` to `id="{prefix}x"` and every reference to it
    (`url(#x)`, `href="#x"`, `xlink:href="#x"`) to match.

    Mandatory, not defensive: `id="ref"` (an arrowhead marker) appears in
    several of these diagrams independently, so inlining two of them
    unprefixed puts two `<marker id="ref">` in one document and every
    `marker-end="url(#ref)"` binds to whichever one the browser saw first —
    both diagrams would silently share one arrowhead until one of them
    changes it. The prefix is derived from the figure's position in the
    document (`d1-`, `d2-`, …), never random or time-based, so a render
    stays byte-identical across repeats.
    """
    ids = {m.group(2) for m in _ID_ATTR_RE.finditer(svg_text)}
    if not ids:
        return svg_text

    def repl_id(m):
        return f"id={m.group(1)}{prefix}{m.group(2)}{m.group(1)}"

    svg_text = _ID_ATTR_RE.sub(repl_id, svg_text)

    def repl_url(m):
        quote, old_id = m.group(1), m.group(2)
        if old_id in ids:
            return f"url({quote}#{prefix}{old_id}{quote})"
        return m.group(0)

    svg_text = _URL_REF_RE.sub(repl_url, svg_text)

    def repl_href(m):
        attr, quote, old_id = m.group(1), m.group(2), m.group(3)
        if old_id in ids:
            return f"{attr}={quote}#{prefix}{old_id}{quote}"
        return m.group(0)

    svg_text = _HREF_REF_RE.sub(repl_href, svg_text)
    return svg_text


def _make_responsive(svg_text: str, *, alt: str) -> str:
    """Keep `viewBox`, drop fixed `width`/`height` off the ROOT `<svg>` only,
    so tokens.css's `svg { max-width: 100% }` governs instead. A root with no
    `viewBox` but real width/height gets one synthesised first
    (`0 0 W H`) — dropping the fixed size without it would collapse the
    drawing to nothing. Adds `role="img"`/`aria-label` when there is alt text
    to attach it to.
    """
    m = _SVG_OPEN_RE.search(svg_text)
    if not m:
        return svg_text
    attrs = m.group(1)
    view_box = _attr_val(attrs, "viewBox")
    width = _attr_val(attrs, "width")
    height = _attr_val(attrs, "height")
    if not view_box and width and height:
        w = re.sub(r"[a-zA-Z%]+$", "", width)
        h = re.sub(r"[a-zA-Z%]+$", "", height)
        attrs += f' viewBox="0 0 {w} {h}"'
    attrs = _drop_attr(attrs, "width")
    attrs = _drop_attr(attrs, "height")
    if alt:
        attrs += f' role="img" aria-label="{html_escape(alt, quote=True)}"'
    return svg_text[:m.start()] + f"<svg{attrs}>" + svg_text[m.end():]


def _process_svg(svg_text: str, *, prefix: str, alt: str) -> str:
    """Turn one raw SVG file's markup into the `<figure>` this document ships."""
    # 1. Strip anything executable. These artifacts get published; an SVG is
    #    markup, not a trusted blob.
    svg_text = re.sub(r"<script\b[^>]*>.*?</script>", "", svg_text, flags=re.I | re.S)
    svg_text = re.sub(r'''\s+on[a-zA-Z]+\s*=\s*"[^"]*"''', "", svg_text, flags=re.I)
    svg_text = re.sub(r"""\s+on[a-zA-Z]+\s*=\s*'[^']*'""", "", svg_text, flags=re.I)
    # 2. Namespace ids so same-named markers/gradients across figures never collide.
    svg_text = _namespace_ids(svg_text, prefix)
    # 3. Drop the XML prolog and any DOCTYPE — illegal inside HTML.
    svg_text = re.sub(r"<\?xml[^>]*\?>\s*", "", svg_text)
    svg_text = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg_text, flags=re.I)
    # 4 & 6. Responsive sizing + accessible name on the root element.
    svg_text = _make_responsive(svg_text.strip(), alt=alt)
    # 5. Wrap in <figure>. The <figcaption> is deliberate, not decorative: of
    #    every channel tried (a bare <img alt>, a <meta>, an aria-label alone),
    #    only real DOM text inside a <figcaption> reaches a machine reader at all.
    #
    #    Be exact about how far that goes, because the first version of this
    #    comment overclaimed it. Measured: a <figcaption> survives `pandoc -t
    #    markdown`, a DOM/accessibility read, and a plain grep. It does NOT
    #    survive `pandoc -t plain`, whose writer drops a Figure's caption — and
    #    pandoc's HTML *reader* treats any inline <svg> as an opaque image, so
    #    the <text> labels below never reach a pandoc AST in any output mode.
    #    Inlining still wins for them, but for a different reason than pandoc:
    #    sealed in base64 they are unreachable to everything, and as markup they
    #    are at least greppable and visible to a DOM reader.
    #
    #    Emitted only when the <img> carried non-empty alt text.
    caption = f"<figcaption>{html_escape(alt)}</figcaption>" if alt else ""
    return f'<figure class="diagram">{svg_text}{caption}</figure>'


def inline_svg_images(html: str, *, asset_dir: str | None = None) -> tuple[str, int, int]:
    """Replace every `<img>` whose source is an SVG with the file's own `<svg>`
    markup, so a diagram becomes real DOM rather than an opaque image.

    Why, measured on the first real document this ran against: four diagrams
    cost 63,660 chars as base64 `<img>` and 47,676 chars inlined — base64
    inflates text-that-is-already-text by about 34%, and inlining put 272
    `<text>` labels into the DOM (including every diagram title and a full
    sentence describing the mechanism) where they had been sealed inside an
    opaque image. A PNG or other raster reference is untouched: SVG is the
    special case because it is markup already, not because inlining is
    generally better.

    Returns `(html, count, bytes_saved)` — `count` figures were inlined,
    `bytes_saved` is the total chars saved against the base64 form (or spent,
    for a tiny SVG whose inline markup is longer than its `<img>` tag).
    """
    count = 0
    bytes_saved = 0

    def repl(m):
        nonlocal count, bytes_saved
        whole = m.group(0)
        tag = m.group("img1") or m.group("img2")
        src = _attr_val(tag, "src")
        if not src:
            return whole
        svg_bytes = _resolve_svg_bytes(src, asset_dir)
        if svg_bytes is None:
            return whole
        try:
            svg_text = svg_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return whole
        alt = _attr_val(tag, "alt") or ""
        count += 1
        figure_html = _process_svg(svg_text, prefix=f"d{count}-", alt=alt)
        bytes_saved += len(whole) - len(figure_html)
        return figure_html

    return _IMG_TAG_RE.sub(repl, html), count, bytes_saved


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


# ------------------------------------------------------- published identity
#
# A published artifact is generic by design (no time context, no internal
# refs, no source paths — see docs on the local agent-notes.md sidecar that
# service.py writes beside it). But an agent that fetches the artifact still
# needs to know what it is, so identity travels INSIDE it, twice: as JSON-LD
# for a reader of the raw HTML, and as real DOM text for anything that
# converts the page to plain text. Both are built from `doc_meta`, which
# callers must restrict to the eight published-tier fields (see `render()`'s
# docstring) — nothing here ever sees a tag, an origin path or a session id.

# Order matches the JSON-LD shape in the design doc: name, genre,
# creativeWorkStatus, version, inLanguage, identifier, dateCreated. `sha256`
# is appended separately, from `source_checksum`.
_JSON_LD_FIELDS = (
    ("title", "name"),
    ("kind", "genre"),
    ("status", "creativeWorkStatus"),
    ("version", "version"),
    ("language", "inLanguage"),
    ("id", "identifier"),
    ("authored_at", "dateCreated"),
)


def _json_ld_script(doc_meta: dict) -> str:
    """A schema.org TechArticle `<script type="application/ld+json">`, built
    ONLY from the published-tier keys already in `doc_meta`.

    A key whose value is missing is omitted entirely rather than emitted as
    `null`. NEVER add `render_checksum` here: it is the checksum of the file
    this very block sits inside, so by construction it can never be correct
    at the time this string is built — a checksum computed before the file
    is finished cannot describe the finished file. That is exactly the kind
    of field someone adds later without thinking, so this comment is the
    tripwire.

    The whole payload is escaped against `</script>` breakout — a title
    containing a literal `</script>` must not be able to end the tag early.
    """
    data = {"@context": "https://schema.org", "@type": "TechArticle"}
    for src_key, ld_key in _JSON_LD_FIELDS:
        val = doc_meta.get(src_key)
        if val not in (None, ""):
            data[ld_key] = val
    checksum = doc_meta.get("source_checksum")
    if checksum:
        data["sha256"] = {"@type": "PropertyValue", "name": "source-sha256",
                          "value": checksum}
    # json.dumps does not HTML-escape; the `</` replace is what keeps a `<`
    # or a `</script>` inside a title from ever closing this tag early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


# The <dl> carries a SUBSET of the JSON-LD fields — title is dropped because
# it already reads as the nav brand / <h1>, so listing it again here would be
# noise rather than new information.
_IDENTITY_DL_FIELDS = ("kind", "status", "version", "language", "id", "authored_at")


def _identity_dl_html(doc_meta: dict) -> str:
    """The published-tier identity as a real `<dl>` — the same fields as the
    JSON-LD block, but as literal DOM text, so they survive whatever the
    JSON-LD does not (a plain grep, `pandoc -t markdown`, a DOM-only reader).
    This is also what finally gets `kind` out of being CSS-class-only
    (`<body class="kind-…">` was the only place it lived before this).

    Returns "" when every field is missing, so the caller never emits an
    empty `<dl></dl>`.
    """
    rows = []
    for key in _IDENTITY_DL_FIELDS:
        val = doc_meta.get(key)
        if val in (None, ""):
            continue
        rows.append(f"<dt>{html_escape(key)}</dt><dd>{html_escape(str(val))}</dd>")
    return ("<dl>" + "".join(rows) + "</dl>") if rows else ""


def _reading_guide(markup: str) -> list[str]:
    """One line per component/block THIS document's markup actually uses —
    generated, never a fixed list.

    `<style>` is stripped before scanning: the embedded tokens.css text names
    every selector unconditionally (`data-component="hero"`, `.table-wrap`,
    `figure.diagram` all appear in the sheet's own CSS regardless of whether
    the document uses them), the same trap `_reachable_families` above
    guards against for font pruning.
    """
    body = re.sub(r"<style[^>]*>.*?</style>", "", markup, flags=re.S)
    lines = []
    for name in lint.COMPONENTS:
        if f'data-component="{name}"' in body:
            lines.append(
                f'<code>data-component="{name}"</code> marks a {name} block.')
    if 'class="table-wrap"' in body:
        lines.append(
            '<code>.table-wrap</code> wraps a table that is allowed to be '
            'wider than the prose column.')
    if 'class="diagram"' in body:
        lines.append(
            '<code>figure.diagram</code> is an inline SVG; its '
            '<code>&lt;figcaption&gt;</code> carries the description.')
    return lines


def reading_guide_lines(html: str) -> list[str]:
    """Public wrapper around `_reading_guide`, so `service.py` can reuse the
    exact same detection when it writes the local agent-notes.md sidecar —
    the reading guide should say the same thing in both places."""
    return _reading_guide(html)


def _agent_notes_details(markup: str, doc_meta: dict | None,
                         notes: str | None) -> str:
    """Assemble the `<details id="agent-notes">` block: published identity,
    then a reading guide generated from `markup`, then the operator's own
    note last — pipeline-generated content stays at the top, where it is
    stable across whatever the operator edits."""
    parts = []
    if doc_meta:
        dl = _identity_dl_html(doc_meta)
        if dl:
            parts.append(dl)
    guide = _reading_guide(markup)
    guide.append(
        "This collapsed block is generated: it carries this artifact's own "
        "identity above, and this reading guide names the structural "
        "markup actually present in the document.")
    parts.append("<ul>" + "".join(f"<li>{line}</li>" for line in guide) + "</ul>")
    if notes:
        # Escaped-as-text inside a <pre>, never run through pandoc: the notes
        # are markdown TEXT, and a <pre> is what keeps their own line breaks
        # while guaranteeing nothing an author wrote can inject markup.
        parts.append(f"<pre>{html_escape(notes)}</pre>")
    body = "\n".join(parts)
    return ('<details id="agent-notes"><summary>Agent notes</summary>\n'
            '<div class="agent-notes-body">\n'
            f"{body}\n"
            "</div>\n</details>")


def inject_body_defaults(html: str, *, header: str | None, footer: str | None,
                         notes: str | None, doc_meta: dict | None = None) -> str:
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

    `doc_meta`, when given, adds the published-tier identity (kind/status/
    version/language/id/authored_at) and a reading guide to the SAME
    `<details>` block — see `_agent_notes_details`. The block is emitted
    whenever there is identity to show, even with no operator note at all.

    Returns `html` UNCHANGED when nothing is configured, or when the anchor is
    missing (the caller is the one that turns that second case into a visible
    warning, by noticing the string came back byte-identical).
    """
    if not header and not footer and not notes and not doc_meta:
        return html
    if _MAIN_OPEN not in html or _MAIN_CLOSE not in html:
        return html
    if header:
        html = html.replace(_MAIN_OPEN, _MAIN_OPEN + header, 1)
    tail = footer or ""
    if notes or doc_meta:
        tail += _agent_notes_details(html, doc_meta, notes)
    if tail:
        html = html.replace(_MAIN_CLOSE, tail + _MAIN_CLOSE, 1)
    return html


def render(source_text: str, *, source_format: str, title: str,
           language: str = "en", kind: str = "report", status: str = "draft",
           font: str = "space-grotesk", custom_head: str | None = None,
           default_header_html: str | None = None,
           default_footer_html: str | None = None,
           agent_notes: str | None = None,
           asset_dir: str | None = None,
           doc_meta: dict | None = None,
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
    asset_dir: the directory the source's OWN relative asset paths (a sibling
             diagram, say) resolve against — normally the source file's own
             directory. Each reference found in `source_text` is copied into
             `workdir` under its same relative path before pandoc runs, so
             `--embed-resources` can resolve it exactly as written. None
             means the source has no such directory to draw from (e.g. it
             arrived as inline `content=` with no file on disk).
    doc_meta: ONLY the eight published-tier identity fields — title, kind,
             status, version, language, id, authored_at, source_checksum.
             Everything context-bearing (tags, origin_path, source_path,
             origin_kind/origin_id, published_url, updated_at, staleness) is
             deliberately NOT accepted here: this function is never given
             those fields at all, which is the cheapest way to guarantee they
             can never leak into a published artifact. `service.py` builds the
             full local record separately, in `agent-notes.md`. See
             `_json_ld_script` and `_agent_notes_details` for what this
             produces.
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
    asset_warnings = _copy_local_assets(body, asset_dir, work)

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
    html, svg_inlined, svg_bytes_saved = inline_svg_images(html, asset_dir=asset_dir)
    if custom_head:
        html = html.replace("</head>", f"{custom_head}\n</head>", 1)
    if doc_meta:
        # NOT through pandoc's `-M` — that HTML-escapes metadata text and would
        # corrupt the JSON, same reasoning as `custom_head` above.
        html = html.replace("</head>", f"{_json_ld_script(doc_meta)}\n</head>", 1)
    before_defaults = html
    html = inject_body_defaults(html, header=default_header_html,
                                footer=default_footer_html, notes=agent_notes,
                                doc_meta=doc_meta)
    anchor_missing = bool(
        (default_header_html or default_footer_html or agent_notes or doc_meta)
        and html == before_defaults)
    html, pruned = prune_faces(html, font=font)
    html = move_faces_last(html)
    warnings = list(asset_warnings)
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
    return {"html": html, "bytes": size, "warnings": warnings, "pruned": pruned,
            "svg_inlined": svg_inlined, "svg_bytes_saved": svg_bytes_saved}


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
                    asset_dir: str | None = None,
                    doc_meta: dict | None = None,
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

    asset_dir: same meaning as `render()`'s — the directory the source's own
    relative asset paths resolve against, copied into `workdir` before pandoc
    runs.

    doc_meta: same eight published-tier fields as `render()`'s — see that
    docstring. A fragment has no `<head>` to splice the JSON-LD into, so it is
    placed right after the embedded `<style>` block instead, still outside the
    visible content.
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
    asset_warnings = _copy_local_assets(body, asset_dir, work)

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
    inner, svg_inlined, svg_bytes_saved = inline_svg_images(inner, asset_dir=asset_dir)
    css = fragment_css()
    json_ld = f"{_json_ld_script(doc_meta)}\n" if doc_meta else ""
    html = (
        f"<style>\n{css}\n</style>\n"
        f"{json_ld}"
        f'<div class="doc-root kind-{kind} font-{font}">\n'
        f'<nav>\n  <span class="brand">{html_escape(title)}</span>\n'
        f'  <span class="tag tag-{status}">{html_escape(status)}</span>\n</nav>\n'
        f'<main class="doc">\n{inner}\n</main>\n'
        f"</div>\n"
    )
    before_defaults = html
    html = inject_body_defaults(html, header=default_header_html,
                                footer=default_footer_html, notes=agent_notes,
                                doc_meta=doc_meta)
    anchor_missing = bool(
        (default_header_html or default_footer_html or agent_notes or doc_meta)
        and html == before_defaults)
    html, pruned = prune_faces(html, font=font)
    html = move_faces_last(html)

    warnings = list(asset_warnings)
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
            "render_bytes": len(html.encode("utf-8")),
            "svg_inlined": svg_inlined, "svg_bytes_saved": svg_bytes_saved}
