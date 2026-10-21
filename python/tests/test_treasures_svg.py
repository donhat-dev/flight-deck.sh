"""Source-asset resolution + SVG inlining (render.py change 1 & 2).

Bug this closes: `render()`/`render_fragment()` ran pandoc in a throwaway
workdir that never received the source's own sibling files, so a diagram
referenced by relative path (`![alt](diagram.svg)`) resolved to nothing and
`--embed-resources` left the tag untouched — a broken image, silently. The
workaround an earlier session used was pasting base64 into the STORED
SOURCE, which is why a treasure wrapped that way permanently drifts against
its origin file.

These tests cover: copying the referenced asset into the workdir (with the
escape guard refusing anything outside `asset_dir`), and `inline_svg_images`
turning an SVG `<img>` into real markup (namespaced ids, stripped script/
event-handler content, a synthesised viewBox, and a `<figcaption>`).
"""
import base64
import re

from flightdeck.treasures import render

SIMPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">'
    '<rect width="100" height="40" fill="#eee"/>'
    '<text x="10" y="20">Diagram</text></svg>'
)

# A 1x1 transparent PNG — real bytes, not a placeholder.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def _data_uri(svg_text: str) -> str:
    b64 = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


# --------------------------------------------------------------------- 1, 9


def test_sibling_svg_inlines_with_no_leftover_img(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "diagram.svg").write_text(SIMPLE_SVG, encoding="utf-8")
    md = "# Report\n\n![The diagram](diagram.svg)\n"

    out = render.render(md, source_format="markdown", title="T",
                        asset_dir=str(docs), workdir=str(tmp_path / "work"))

    assert "<svg" in out["html"]
    assert "<img" not in out["html"]
    assert out["svg_inlined"] == 1
    assert not any("diagram.svg" in w for w in out["warnings"])


def test_render_is_reproducible_across_two_renders(tmp_path):
    """The deterministic `d1-`, `d2-` prefix is what this protects: a random
    or time-based prefix would make byte-identical reruns impossible."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "diagram.svg").write_text(SIMPLE_SVG, encoding="utf-8")
    md = "# Report\n\n![The diagram](diagram.svg)\n"

    out1 = render.render(md, source_format="markdown", title="T",
                         asset_dir=str(docs), workdir=str(tmp_path / "work1"))
    out2 = render.render(md, source_format="markdown", title="T",
                         asset_dir=str(docs), workdir=str(tmp_path / "work2"))

    assert out1["html"] == out2["html"]


# ------------------------------------------------------------------------ 2


def test_missing_asset_warns_and_does_not_crash(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    md = "# Report\n\n![Ghost](missing.svg)\n"

    out = render.render(md, source_format="markdown", title="T",
                        asset_dir=str(docs), workdir=str(tmp_path / "work"))

    assert any("missing.svg" in w for w in out["warnings"])


# ------------------------------------------------------------------------ 3


def test_escaping_path_is_refused_and_not_copied(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    # Placed one level above `docs`, i.e. genuinely outside asset_dir.
    (tmp_path / "secret.svg").write_text(SIMPLE_SVG, encoding="utf-8")
    md = "# Report\n\n![Secret](../secret.svg)\n"

    out = render.render(md, source_format="markdown", title="T",
                        asset_dir=str(docs), workdir=str(tmp_path / "nested" / "work"))

    assert any("secret.svg" in w and "outside" in w for w in out["warnings"])
    work = tmp_path / "nested" / "work"
    assert not list(work.rglob("secret.svg"))


# ------------------------------------------------------------------------ 4


def test_id_collision_across_two_figures_gets_distinct_prefixes():
    """The real hazard: id="ref" (an arrowhead marker) appears in four of the
    five diagrams this fix was built for. Inlined naively, the second
    <marker id="ref"> would never render — every marker-end="url(#ref)"
    binds to whichever the browser saw first."""
    svg_a = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
             '<marker id="ref"><path d="M0 0"/></marker>'
             '<path marker-end="url(#ref)" d="M0 0L1 1"/></svg>')
    svg_b = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
             '<marker id="ref"><path d="M1 1"/></marker>'
             '<path marker-end="url(#ref)" d="M1 1L2 2"/></svg>')
    html_in = (f'<img src="{_data_uri(svg_a)}" alt="A">'
               f'<img src="{_data_uri(svg_b)}" alt="B">')

    out, count, _ = render.inline_svg_images(html_in)

    assert count == 2
    ids = re.findall(r'id="([^"]+)"', out)
    assert len(ids) == len(set(ids)), f"duplicate id(s) in {ids!r}"
    assert 'id="d1-ref"' in out and 'url(#d1-ref)' in out
    assert 'id="d2-ref"' in out and 'url(#d2-ref)' in out


# ------------------------------------------------------------------------ 5


def test_script_and_event_handler_do_not_survive():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
           'onload="evil()"><script>alert(1)</script>'
           '<rect width="10" height="10"/></svg>')
    html_in = f'<img src="{_data_uri(svg)}" alt="x">'

    out, count, _ = render.inline_svg_images(html_in)

    assert count == 1
    assert "<script" not in out
    assert "alert(1)" not in out
    assert "onload" not in out
    assert "evil()" not in out


# ------------------------------------------------------------------------ 6


def test_alt_text_becomes_figcaption_empty_alt_omits_it():
    html_in = (f'<img src="{_data_uri(SIMPLE_SVG)}" alt="A caption">'
               f'<img src="{_data_uri(SIMPLE_SVG)}" alt="">')

    out, count, _ = render.inline_svg_images(html_in)

    assert count == 2
    assert "<figcaption>A caption</figcaption>" in out
    figures = re.findall(r"<figure class=\"diagram\">.*?</figure>", out, re.S)
    assert len(figures) == 2
    assert "<figcaption>" not in figures[1]


# ------------------------------------------------------------------------ 7


def test_missing_viewbox_is_synthesised_and_fixed_size_dropped():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
           '<rect width="200" height="100"/></svg>')
    html_in = f'<img src="{_data_uri(svg)}" alt="x">'

    out, count, _ = render.inline_svg_images(html_in)

    assert count == 1
    m = re.search(r"<svg[^>]*>", out)
    assert m is not None
    root = m.group(0)
    assert 'viewBox="0 0 200 100"' in root
    assert "width=" not in root
    assert "height=" not in root


# ------------------------------------------------------------------------ 8


def test_png_reference_still_becomes_base64_img(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "photo.png").write_bytes(TINY_PNG)
    md = "# Report\n\n![A photo](photo.png)\n"

    out = render.render(md, source_format="markdown", title="T",
                        asset_dir=str(docs), workdir=str(tmp_path / "work"))

    assert out["svg_inlined"] == 0
    assert "<img" in out["html"]
    assert "data:image/png;base64," in out["html"]
    assert "<figure class=\"diagram\">" not in out["html"]
