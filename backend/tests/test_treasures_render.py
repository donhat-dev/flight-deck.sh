import re
import pytest

from flightdeck.treasures import lint, render

# Vietnamese codepoints absent from `latin-ext` subsets (U+1EA0-1EF9 block).
VN_CODEPOINTS = {0x1EBF: "ế", 0x1ED9: "ộ", 0x1EEF: "ữ", 0x1EE3: "ợ", 0x1EA1: "ạ"}

MD = """# Báo cáo Helpdesk

Nội dung tiếng Việt: hiệu quả, cộng đồng, người dùng.

- một
- hai
"""


ASCII_PROBE = {0x41: "A", 0x61: "a", 0x30: "0"}


def _family_cmaps():
    """Merged cmap per font family, keyed by the family's filename prefix.

    Coverage is a FAMILY property, not a file property: a family is split into a
    latin face and a vietnamese face, each carrying a unicode-range, so neither
    file alone covers everything.
    """
    ttlib = pytest.importorskip("fontTools.ttLib")
    fams: dict[str, set[int]] = {}
    for path in render.font_paths():
        name = path.rsplit("/", 1)[-1]
        fam = ("space-grotesk" if name.startswith("space-grotesk")
               else "jetbrains-mono" if name.startswith("jetbrains-mono")
               else "playfair-display" if name.startswith("playfair-display")
               else name)
        fams.setdefault(fam, set()).update(ttlib.TTFont(path).getBestCmap())
    return fams


def test_font_families_cover_vietnamese():
    """Every family must carry the Vietnamese block; latin-ext does not."""
    for fam, cmap in _family_cmaps().items():
        missing = [c for cp, c in VN_CODEPOINTS.items() if cp not in cmap]
        assert not missing, f"{fam} is missing Vietnamese glyphs: {missing}"


def test_font_families_cover_ascii():
    """REGRESSION: until 2026-07-28 every embedded face was Google's
    *vietnamese* subset — 113 glyphs, no 'A', no 'a', no '0' — declared without
    a unicode-range, so it claimed the whole family and all Latin text silently
    fell back to the system font. Space Grotesk was never actually rendered in
    any published artifact, and the missing '0' also made the CSS `ch` unit
    degrade to its 0.5em fallback, so the 68ch prose measure was wrong too."""
    for fam, cmap in _family_cmaps().items():
        missing = [c for cp, c in ASCII_PROBE.items() if cp not in cmap]
        assert not missing, f"{fam} cannot render basic Latin: {missing}"


def test_render_markdown_is_self_contained(tmp_path):
    out = render.render(MD, source_format="markdown", title="Báo cáo",
                        language="vi", workdir=str(tmp_path))
    html = out["html"]
    assert html.lstrip().startswith("<!doctype html>")
    assert "<title>Báo cáo</title>" in html
    assert 'lang="vi"' in html
    assert "data:font/woff2;base64," in html          # fonts embedded by pandoc
    assert 'rel="icon"' in html                       # favicon present
    assert render.EXTERNAL_REF_RE.findall(html) == []  # nothing external left
    assert out["bytes"] == len(html.encode("utf-8"))


def test_render_html_fragment_input(tmp_path):
    frag = '<section><h1>Từ HTML</h1><p>Khung do agent đưa.</p></section>'
    out = render.render(frag, source_format="html", title="Fragment",
                        language="vi", workdir=str(tmp_path))
    assert "Từ HTML" in out["html"]
    assert render.EXTERNAL_REF_RE.findall(out["html"]) == []


def test_render_warns_about_remote_assets(tmp_path):
    md = "# T\n\n![](https://example.com/chart.png)\n"
    out = render.render(md, source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    assert any("example.com" in w for w in out["warnings"])


def test_external_ref_regex_ignores_svg_namespace():
    """The favicon data URI contains the SVG namespace URL; it is not a fetch."""
    sample = ('<link rel="icon" href="data:image/svg+xml,'
              "<svg xmlns='http://www.w3.org/2000/svg'></svg>\">")
    assert render.EXTERNAL_REF_RE.findall(sample) == []
    assert render.EXTERNAL_REF_RE.findall('<img src="https://cdn.example/x.png">')


def test_external_ref_regex_ignores_plain_anchor_links():
    """A citation <a href> is navigation, never a passive fetch — a report
    linking another artifact must not be flagged 'not self-contained'."""
    assert render.EXTERNAL_REF_RE.findall(
        '<a href="https://claude.ai/code/artifact/abc">the report</a>') == []
    # A <link href> (stylesheet-like) is still a passive fetch — still flagged.
    assert render.EXTERNAL_REF_RE.findall(
        '<link rel="stylesheet" href="https://cdn.example/x.css">')


def test_render_does_not_warn_about_a_plain_citation_link(tmp_path):
    md = "# T\n\nSee the [CRM-11198 report](https://claude.ai/code/artifact/abc).\n"
    out = render.render(md, source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    assert not any("fetched remote asset" in w for w in out["warnings"])
    assert not any("NOT self-contained" in w for w in out["warnings"])


FRONTMATTER_MD = """---
name: design-system-flightdeck-night
description: Use when implementing FlightDeck UI: tokens, motion, a11y
---

# FlightDeck Night

Body text that must survive the frontmatter strip.
"""


def test_yaml_frontmatter_does_not_break_the_render(tmp_path):
    """Real docs (SKILL.md, specs) open with frontmatter whose unquoted
    `key: value with: colons` is invalid YAML. pandoc used to abort the whole
    render; the block is metadata, so it is stripped and never parsed."""
    out = render.render(FRONTMATTER_MD, source_format="markdown",
                        title="FlightDeck Night", language="en",
                        workdir=str(tmp_path))
    html = out["html"]
    assert "Body text that must survive" in html
    assert "design-system-flightdeck-night" not in html   # frontmatter dropped
    assert render.EXTERNAL_REF_RE.findall(html) == []


def test_font_selects_the_body_class(tmp_path):
    """Regression: tokens.css's own CSS text (`body.font-default{...}` etc.)
    is embedded in every render regardless of which font is picked, so
    `'font-{font}' in html` passes even when the <body> tag itself got a
    completely different (or broken) class — that false-positive is exactly
    what hid a real bug (a `for font in ...:` loop clobbering this same
    `font` parameter before it reached pandoc). Anchor on the <body> tag
    itself, and also assert no stray filesystem path leaked into it."""
    for font in ("default", "space-grotesk", "jetbrains-mono"):
        out = render.render("# T\n", source_format="markdown", title="T",
                            language="en", font=font, workdir=str(tmp_path))
        m = re.search(r'<body class="([^"]*)"', out["html"])
        assert m, "no <body class=...> tag found"
        assert m.group(1) == f"kind-report font-{font}"


def test_custom_head_is_spliced_before_head_close_tag(tmp_path):
    head = '<meta name="robots" content="noindex"><style>.x{color:red}</style>'
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", custom_head=head, workdir=str(tmp_path))
    html = out["html"]
    assert head in html
    assert html.index(head) < html.index("</head>")


def test_no_custom_head_means_no_extra_markup(tmp_path):
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    assert out["html"].count("</head>") == 1


def test_a_real_horizontal_rule_is_not_mistaken_for_frontmatter(tmp_path):
    md = "---\n\nJust a rule above this paragraph.\n\n---\n\nAnd another.\n"
    out = render.render(md, source_format="markdown", title="Rules",
                        language="en", workdir=str(tmp_path))
    assert "Just a rule above this paragraph." in out["html"]


# --------------------------------------------------------------- table breakout

TABLE_MD = "# T\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"


def test_a_top_level_table_is_wrapped_in_table_wrap(tmp_path):
    """The Lua filter wraps every top-level table so tokens.css can break it out
    of the prose column — a table has no wrapper of its own to carry negative
    margins otherwise."""
    out = render.render(TABLE_MD, source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    assert re.search(r'<div class="table-wrap">\s*<table', out["html"])


def test_a_table_nested_in_a_component_is_not_wrapped(tmp_path):
    """Top-level only, matching the sheet's own `.doc >` scoping: a table inside
    a component div belongs to that component's layout, not the page's."""
    md = ('<div data-component="card">\n\n| A | B |\n| --- | --- |\n'
          '| 1 | 2 |\n\n</div>\n')
    out = render.render(md, source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    html = out["html"]
    card_table = html.index('data-component="card"')
    table_idx = html.index("<table")
    assert table_idx > card_table
    # the table sits directly inside the card, no table-wrap div in between
    assert "table-wrap" not in html[card_table:table_idx]


def test_tokens_css_declares_the_table_breakout():
    """Cheap coverage guard, in the style of the font-family coverage tests
    above: the wrapper rule and the width token it consumes must both exist."""
    css = render.TOKENS_CSS.read_text(encoding="utf-8")
    assert ".table-wrap {" in css
    assert "--table-max" in css


# --------------------------------------------------------- font-face pruning

_FILLER_PARAGRAPH = (
    "This paragraph exists purely to give the document a realistic amount of "
    "body text, so the fixed per-render overhead — the embedded stylesheet, "
    "the nav bar, the favicon — is a small fraction of the whole file rather "
    "than most of it, matching the shape a real report has rather than a "
    "two-line smoke test.\n\n"
)

ENGLISH_MD = (
    "# A Plain English Report\n\n"
    "This document has no special characters, no hero, and no code that "
    "would need JetBrains Mono. It exists only to prove which webfonts a "
    "real reader's browser could possibly need for a document like this "
    "one.\n\n"
    "## Background\n\n"
    + _FILLER_PARAGRAPH * 40 +
    "- one point\n- a second point\n- and a third, so the list has body too\n\n"
    "## Conclusion\n\n"
    "Nothing here should need a Vietnamese glyph, a monospace body font, or "
    "a hero component, so pruning should leave exactly one embedded face: "
    "the Space Grotesk latin subset that the body text actually uses.\n"
)


def test_pruning_drops_what_cannot_be_used(tmp_path):
    out = render.render(ENGLISH_MD, source_format="markdown", title="Report",
                        language="en", font="space-grotesk", workdir=str(tmp_path))
    html = out["html"]
    assert html.count("@font-face") == 1
    face = re.search(r"@font-face\s*\{[^}]*\}", html, re.S).group(0)
    assert "font-family: 'Space Grotesk'" in face
    assert "space-grotesk-latin.woff2" not in html
    assert any("pruned" in w and "5 unused font face" in w for w in out["warnings"]), \
        out["warnings"]


def test_pruning_keeps_the_mono_face_when_it_is_the_body_font(tmp_path):
    out = render.render(ENGLISH_MD, source_format="markdown", title="Report",
                        language="en", font="jetbrains-mono", workdir=str(tmp_path))
    html = out["html"]
    faces = re.findall(r"@font-face\s*\{[^}]*\}", html, re.S)
    assert len(faces) == 1
    assert "font-family: 'JetBrains Mono'" in faces[0]
    # No @font-face block for Space Grotesk. The base `body { font-family:
    # 'Space Grotesk', ... }` fallback rule (tokens.css's default, overridden
    # at runtime by body.font-jetbrains-mono) still names the string in plain
    # CSS text, so the assertion is scoped to @font-face blocks specifically —
    # anywhere else it is dead-but-harmless CSS, not an embedded webfont.
    assert not any("Space Grotesk" in f for f in faces)


VIETNAMESE_NAME_MD = """# A Report

This body is otherwise plain English, but it names Nguyễn once, which is
enough to pull the Vietnamese subset back in alongside the Latin one.
"""


def test_the_vietnamese_subset_comes_back_when_the_text_needs_it(tmp_path):
    out = render.render(VIETNAMESE_NAME_MD, source_format="markdown", title="Report",
                        language="en", font="space-grotesk", workdir=str(tmp_path))
    html = out["html"]
    faces = re.findall(r"@font-face\s*\{[^}]*\}", html, re.S)
    sg_faces = [f for f in faces if "font-family: 'Space Grotesk'" in f]
    assert len(sg_faces) == 2, faces


HERO_MD = """<div data-component="hero">

CRM-11198 · eyebrow

# Discount Service *PoC plan.*

The deck sentence.

</div>

Body text after the hero.
"""


def test_the_hero_brings_playfair_back(tmp_path):
    out = render.render(HERO_MD, source_format="markdown", title="Report",
                        language="en", font="space-grotesk", workdir=str(tmp_path))
    html = out["html"]
    faces = re.findall(r"@font-face\s*\{[^}]*\}", html, re.S)
    assert any("font-family: 'Playfair Display'" in f for f in faces), faces


def test_faces_are_last(tmp_path):
    out = render.render(ENGLISH_MD, source_format="markdown", title="Report",
                        language="en", font="space-grotesk", workdir=str(tmp_path))
    html = out["html"]
    h1_idx = html.index("<h1")
    last_face_idx = html.rindex("@font-face")
    assert last_face_idx > h1_idx
    assert h1_idx < 0.25 * len(html), (h1_idx, len(html))


# --------------------------------------------------- the pandoc local-src trap

def test_the_local_src_guard_fires(tmp_path):
    md = ('# T\n\n'
          '<iframe src="/nakivo/embed/launch/crm.lead/973770"></iframe>\n')
    with pytest.raises(lint.ComponentError, match=re.escape(
            "/nakivo/embed/launch/crm.lead/973770")):
        render.render(md, source_format="markdown", title="T", language="en",
                      workdir=str(tmp_path))


def test_the_guard_does_not_over_refuse(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "x.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
    md = (
        "# T\n\n"
        '<img src="https://example.com/a.png">\n\n'
        '<img src="data:image/png;base64,iVBORw0KGgoAAAA=">\n\n'
        '<img src="assets/x.png">\n'
    )
    out = render.render(md, source_format="markdown", title="T", language="en",
                        workdir=str(tmp_path))
    assert out["html"]
