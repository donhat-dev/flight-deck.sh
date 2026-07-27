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
