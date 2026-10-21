"""Component contract tests (docs/treasures-components.md).

The four negative tests §3 requires are `test_negative_*` below; the rest cover
the happy path and the syntaxes the contract bans.
"""
import re
import tempfile
from pathlib import Path

import pytest

from flightdeck import db
from flightdeck.treasures import filestore, lint, render, service, store


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


def _render(md, **kw):
    with tempfile.TemporaryDirectory() as wd:
        return render.render(md, source_format="markdown", title="T",
                             language="en", workdir=wd, **kw)


def _body(html):
    """Just the rendered document. Counting component markup across the whole
    file would also hit tokens.css's own `[data-component=...]` selectors, which
    ship in every artifact."""
    return html[html.index("<main"):html.index("</main>")]


def _css():
    """tokens.css with comments stripped, so a selector assertion cannot pick up
    the comment block sitting above the rule."""
    css = render.TOKENS_CSS.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


HERO = """<div data-component="hero">

CRM-11198 · eyebrow

# Discount Service *PoC plan.*

The deck sentence.

</div>
"""


# --- lint ------------------------------------------------------------------
def test_lint_inserts_the_blank_lines_commonmark_needs():
    fixed, notes = lint.lint('<div data-component="hero">\n# Title\n</div>\n')
    assert fixed == '<div data-component="hero">\n\n# Title\n\n</div>\n'
    assert len(notes) == 2


def test_lint_leaves_correct_markup_untouched():
    fixed, notes = lint.lint(HERO)
    assert fixed == HERO
    assert notes == []


def test_lint_ignores_inline_components():
    """A badge lives inside a paragraph, so the blank-line rule must not touch
    it — padding around an inline span would split the sentence into two."""
    md = 'Status: <span data-component="badge">PASS</span> today.\n'
    fixed, notes = lint.lint(md)
    assert fixed == md and notes == []


def test_lint_leaves_a_stray_closing_div_alone():
    """Only a tag that actually closes a component gets padded."""
    md = "text\n</div>\n"
    fixed, notes = lint.lint(md)
    assert fixed == md and notes == []


# --- render ----------------------------------------------------------------
def test_components_render_as_real_elements_with_attributes():
    md = HERO + (
        '\nStatus <span data-component="badge" data-tone="good">OK</span>.\n'
        '\n<div data-component="card" data-tone="mid">\n\nBody.\n\n</div>\n')
    html = _render(md)["html"]
    assert 'data-component="hero"' in html
    assert '<span data-component="badge" data-tone="good">OK</span>' in html
    assert 'data-component="card" data-tone="mid"' in html
    # the hero accent line must survive as <em> so Playfair can target it
    assert "<em>PoC plan.</em>" in html
    assert lint.validate(md, html) == []


def test_a_heading_led_component_becomes_a_section_not_a_div():
    """pandoc rewrites a div whose first child is a heading into <section>,
    which is why every CSS rule matches the attribute and never `div[...]`."""
    md = '<div data-component="card">\n\n### Lead heading\n\nBody.\n\n</div>\n'
    html = _render(md)["html"]
    assert '<section id="lead-heading" data-component="card">' in html
    assert '<div data-component="card">' not in html


def test_inner_markdown_is_parsed_not_passed_through():
    md = ('<div data-component="card">\n\n**bold** and a list:\n\n'
          '- one\n- two\n\n</div>\n')
    html = _render(md)["html"]
    assert "<strong>bold</strong>" in html
    assert "<li>one</li>" in html


def test_tokens_css_declares_every_component_and_never_matches_on_div():
    css = _css()
    for name in lint.COMPONENTS:
        assert f'[data-component="{name}"]' in css, f"{name} has no CSS"
    assert "div[data-component" not in css


def test_grid_nests_cards_through_the_whole_pipeline():
    """`grid` is the only nesting component. CommonMark and pandoc both handle
    the nesting, and the lint must not mangle either level's blank lines."""
    md = ('<div data-component="grid">\n\n'
          '<div data-component="card" data-tone="good">\n\n### A\n\nBody A.\n\n</div>\n\n'
          '<div data-component="card">\n\n### B\n\nBody B.\n\n</div>\n\n'
          '</div>\n')
    fixed, notes = lint.lint(md)
    assert fixed == md and notes == []
    html = _render(md)["html"]
    body = _body(html)
    assert '<div data-component="grid">' in body
    assert body.count('data-component="card"') == 2
    assert lint.validate(md, html) == []


def test_stats_shape_yields_strong_numbers_in_a_list():
    md = ('<div data-component="stats">\n\n'
          '- **7** manday estimate\n- **353** modules installed\n\n</div>\n')
    html = _body(_render(md)["html"])
    assert '<li><strong>7</strong> manday estimate</li>' in html
    assert '<li><strong>353</strong> modules installed</li>' in html


def test_every_component_and_tone_has_css():
    css = _css()
    for name in lint.COMPONENTS:
        assert f'[data-component="{name}"]' in css, f"{name} has no CSS"
    for tone in ("good", "mid", "weak", "assume", "deferred"):
        assert f'[data-tone="{tone}"]' in css, f"card tone {tone} has no CSS"


# --- adopted shell styles ---------------------------------------------------
def test_the_aurora_scrolls_away_instead_of_following_the_reader():
    """REGRESSION: the wash belongs to the opening screen. `position: fixed`
    would tint every table in a 60-screen document on the way down."""
    css = _css()
    block = re.search(r"body::before\s*\{(.*?)\}", css, re.S).group(1)
    assert "position: absolute" in block
    assert "position: fixed" not in block


def test_the_aurora_never_makes_the_page_scrollable():
    """REGRESSION, measured in a browser twice: a bleed past the viewport is the
    nicer way to hide the blur's outer edge, but nothing can clip it. `overflow`
    on the root propagates to the viewport, and `overflow` on body propagates too
    whenever html is `visible` — so the declaration lands on the viewport and
    body itself never clips. Both placements left the page horizontally
    scrollable by exactly the bleed width. The wash must therefore be bounded to
    the viewport with no negative offsets at all."""
    css = _css()
    block = re.search(r"body::before\s*\{(.*?)\}", css, re.S).group(1)
    assert "inset: 0 0 auto 0" in block
    assert "height: 100vh" in block
    offsets = re.findall(r"\b(?:inset|top|right|bottom|left)\s*:([^;]+);", block)
    assert offsets and not any("-" in v for v in offsets), \
        f"a negative offset would reintroduce the bleed: {offsets}"
    for sel in (r"\nhtml\s*\{", r"\nbody\s*\{"):
        b = re.search(sel + r"(.*?)\}", css, re.S).group(1)
        assert "overflow" not in b, "overflow here propagates to the viewport; it cannot clip the wash"


def test_scroll_progress_is_pure_css_and_needs_no_markup():
    """It hangs off html::before, so a generated document authors no furniture,
    and it is driven by a scroll timeline rather than JS (artifacts run none)."""
    css = _css()
    assert "html::before" in css
    assert "animation-timeline: scroll(root)" in css
    assert "@keyframes fd-scroll-progress" in css
    # invisible, not wrong-length, where scroll timelines are unsupported
    block = re.search(r"html::before\s*\{(.*?)\}", css, re.S).group(1)
    assert "transform: scaleX(0)" in block


def test_the_one_allowed_shadow_is_on_the_table_only():
    """§3 is borders-over-shadows with exactly one accent-tinted exception."""
    css = _css()
    shadows = re.findall(r"([^{}]+)\{[^{}]*box-shadow:\s*([^;]+);", css)
    real = [(s.strip(), v) for s, v in shadows if "none" not in v]
    assert len(real) == 1, f"expected one shadow, found {real}"
    assert real[0][0] == "table"
    assert "rgba(22, 104, 227, .18)" in real[0][1]


def test_section_deck_and_step_chips_need_no_new_syntax():
    """Both attach to shapes markdown already emits: `h2` then `p`, and `ol`."""
    css = _css()
    assert ".doc > h2 + p" in css
    assert ".doc > ol > li::before" in css
    html = _render("## Section\n\nDeck line.\n\n1. one\n2. two\n")["html"]
    assert "<h2" in html and "<ol" in html


def test_playfair_is_confined_to_the_hero_accent():
    """canon §1: Playfair Display is allowed on a hero accent line and nowhere
    else, so every rule naming it must be scoped to the hero."""
    css = _css()
    for block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = block[0].strip(), block[1]
        if "Playfair" in body and "@font-face" not in sel:
            assert '[data-component="hero"]' in sel, \
                f"Playfair leaked outside the hero: {sel}"


# --- the four required negative tests --------------------------------------
def test_negative_1_missing_blank_line_still_yields_a_real_element(monkeypatch, tmp_path):
    """Missing blank line: lint reports the fix and the HTML has an element."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Doc",
                       content='<div data-component="card">\n# T\n</div>\n')
    html = Path(out["artifact_path"]).read_text(encoding="utf-8")
    assert any("blank line" in w for w in out["warnings"])
    assert "data-component=\"card\"" in html
    assert "&lt;div data-component" not in html
    # the stored source carries the fix, so a later rerender is already clean
    assert service.get(conn, out["id"], include_source=True)["source"] == \
        '<div data-component="card">\n\n# T\n\n</div>\n'


def test_negative_2_unknown_component_refuses_the_wrap(monkeypatch, tmp_path):
    """Fail-closed: nothing reaches disk or the index."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    with pytest.raises(lint.ComponentError):
        service.wrap(conn, title="Doomed",
                     content='<div data-component="nope">\n\nx\n\n</div>\n')
    assert store.list_rows(conn) == []
    assert not list(filestore.root().glob("doomed-*"))


def test_negative_3_forced_degradation_ships_plain_markdown(monkeypatch, tmp_path):
    """When validation fails the artifact must not print raw tags as text; it
    degrades to plain markdown, warns, and KEEPS the source intact."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    real = service.render.render

    def sabotage(text, **kw):
        # Simulate a renderer that escapes the component instead of emitting it.
        out = real(text, **kw)
        if 'data-component="card"' in out["html"]:
            out["html"] = out["html"].replace(
                '<section id="t" data-component="card">',
                '&lt;div data-component="card"&gt;')
            out["html"] = out["html"].replace(
                '<div data-component="card">', '&lt;div data-component="card"&gt;')
        return out

    monkeypatch.setattr(service.render, "render", sabotage)
    md = '<div data-component="card">\n\nBody.\n\n</div>\n'
    out = service.wrap(conn, title="Degraded", content=md)
    html = Path(out["artifact_path"]).read_text(encoding="utf-8")
    assert "&lt;div data-component" not in html
    assert any("stripped" in w for w in out["warnings"])
    assert "Body." in html
    # source keeps the author's components — the strip is render-only
    assert service.get(conn, out["id"], include_source=True)["source"] == md


def test_negative_4_a_plain_document_gets_no_component_markup(monkeypatch, tmp_path):
    """A document using no component must come out with no component markup in
    its BODY, so nothing about the existing library's rendering changes.

    The component RULES do ship in every artifact's embedded CSS — one shared
    tokens.css is the whole point — so the check is scoped to the body rather
    than the whole file."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    md = "# Plain\n\nBody text with **bold**.\n"
    out = service.wrap(conn, title="Plain", content=md)
    html = Path(out["artifact_path"]).read_text(encoding="utf-8")
    body = html[html.index("<main"):html.index("</main>")]
    assert "data-component" not in body
    assert out["warnings"] == []
    assert service.get(conn, out["id"], include_source=True)["source"] == md


# --- the banned syntaxes ---------------------------------------------------
def test_banned_bracketed_span_is_not_the_recommended_form():
    """`[PASS]{.badge}` renders, but Milkdown escapes the bracket on save, so
    one click of Save destroys it. The contract bans it; this records that the
    span form is what the CSS actually targets."""
    html = _render("A [PASS]{.badge} b.\n")["html"]
    assert '<span class="badge">PASS</span>' in html
    css = _css()
    assert ".badge" not in css.replace('[data-component="badge"]', "")


def test_strip_removes_markers_but_keeps_content():
    md = ('<div data-component="hero">\n\n# T\n\n</div>\n\n'
          'x <span data-component="badge">P</span> y\n')
    out = lint.strip(md)
    assert "data-component" not in out
    assert "# T" in out and "P" in out
