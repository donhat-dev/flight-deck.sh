import re
import pytest

from flightdeck.treasures import render

# Vietnamese codepoints absent from `latin-ext` subsets (U+1EA0-1EF9 block).
VN_CODEPOINTS = {0x1EBF: "ế", 0x1ED9: "ộ", 0x1EEF: "ữ", 0x1EE3: "ợ", 0x1EA1: "ạ"}

MD = """# Báo cáo Helpdesk

Nội dung tiếng Việt: hiệu quả, cộng đồng, người dùng.

- một
- hai
"""


def test_fonts_cover_vietnamese():
    """The artifact fonts must carry the Vietnamese block; latin-ext does not."""
    ttlib = pytest.importorskip("fontTools.ttLib")
    for path in render.font_paths():
        cmap = set(ttlib.TTFont(path).getBestCmap().keys())
        missing = [c for cp, c in VN_CODEPOINTS.items() if cp not in cmap]
        assert not missing, f"{path} is missing Vietnamese glyphs: {missing}"


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
