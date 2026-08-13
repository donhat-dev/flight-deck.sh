"""The fragment export mode — publishing without a hand-edit.

The standalone artifact and a claude.ai Artifact disagree about who owns the page
frame, and the disagreement costs three specific things. Each is a test here,
because each failed silently rather than loudly when a full document was handed
over:

  1. the `<body class="kind-… font-…">` every typography rule hangs off — the
     Artifact frame supplies <body>, so the classes vanish and the sheet matches
     nothing;
  2. the page background, painted on `html` — the host's own <body> sits on top
     of it, which left the margins light around text that stayed dark;
  3. the colour mode — with no `color-scheme` the viewer's dark mode repaints
     form controls and the canvas under type that did not change.

Plus one thing that is not a frame problem but breaks the same way: the fonts
are only inlined today because pandoc's `--embed-resources` processes the
stylesheet it links; a fragment has no <head> to link from, so it must inline
them itself or quietly become the one external reference in a page whose CSP
blocks external hosts.
"""
import base64
import json
import re

import pytest

from flightdeck.treasures import mcp_server, render


@pytest.fixture()
def frag(tmp_path):
    return render.render_fragment(
        "# Title\n\nBody paragraph with **bold** and `code`.\n\n"
        "## Heading two\n\n- one\n- two\n",
        source_format="markdown", title="Fragment Doc", kind="spec-review",
        status="final", font="jetbrains-mono", workdir=str(tmp_path / "w"))


def markup_of(html: str) -> str:
    """Everything after the embedded sheet.

    Scoped deliberately: the CSS legitimately contains the strings `<html>` and
    `<body>` in prose, and checking the whole output for them reported a frame
    that was not there.
    """
    return html[html.index("</style>"):]


# ---------------------------------------------------------------- 1. the frame

@pytest.mark.parametrize("tag", ["<!doctype", "<html", "<head", "<body"])
def test_fragment_emits_no_document_frame(frag, tag):
    assert tag not in markup_of(frag["html"]).lower()
    assert not frag["warnings"], frag["warnings"]


def test_the_kind_and_font_classes_move_to_a_wrapper(frag):
    # The classes are the whole reason a full document degrades: on <body> they
    # are simply dropped, and every rule that selects them stops matching.
    wrapper = re.search(r'</style>\s*<div class="([^"]+)"', frag["html"]).group(1)
    assert wrapper.split() == ["doc-root", "kind-spec-review", "font-jetbrains-mono"]
    assert ".doc-root.font-jetbrains-mono" in frag["html"]
    assert "body.font-" not in frag["html"]


def test_nav_and_main_survive_inside_the_wrapper(frag):
    markup = markup_of(frag["html"])
    assert '<main class="doc">' in markup
    assert "Fragment Doc" in markup and "tag-final" in markup
    # The content itself must actually be there — an empty wrapper would pass
    # every structural check above.
    assert "Heading two" in markup and "<strong>bold</strong>" in markup


# ------------------------------------------------------- 2. the page it paints

def test_the_wrapper_paints_its_own_background(frag):
    # `html { background: … }` cannot work in a fragment: the host's <body> has
    # its own background sitting above <html>. Only the wrapper is under our
    # content, so only the wrapper can paint it. The exact rule is asserted by
    # test_the_wrapper_is_its_own_stacking_context, which also covers what has to
    # ride along with the paint.
    assert ".doc-root { background: var(--paper);" in frag["html"]
    assert "html { background" not in frag["html"]


def test_body_pseudo_elements_follow_the_wrapper(frag):
    # The aurora and grain layers hang off ::before/::after. Left on `body` they
    # would decorate the host page instead of the document.
    assert ".doc-root::before" in frag["html"]
    assert ".doc-root::after" in frag["html"]
    assert "body::before" not in frag["html"]
    assert "body::after" not in frag["html"]


def test_the_scroll_progress_bar_is_dropped_not_repointed(frag):
    # `animation-timeline: scroll(root)` reads the ROOT scroller, which in a
    # fragment is the host page. There is no element that owns the document's
    # own scroll, so a repointed bar would confidently report someone else's
    # position. Removal is the honest answer.
    assert "html::before" not in frag["html"]
    assert "fd-scroll-progress" not in frag["html"]


# -------------------------------------------------------- 3. the colour scheme

def test_tokens_move_off_root_and_beat_a_theming_host(frag):
    css = frag["html"]
    assert ":root {" not in css
    # A bare `.doc-root` (0,1,0) loses to a host's `[data-theme="dark"] .doc-root`
    # (0,2,0), so the same block is repeated under both attributes. Without the
    # repeats a dark-mode viewer could still recolour the document.
    for sel in ('.doc-root,', '[data-theme="light"] .doc-root',
                '[data-theme="dark"] .doc-root'):
        assert sel in css, sel
    assert "color-scheme: light" in css


def test_print_rules_target_the_wrapper(frag):
    assert ".doc-root { background: #fff; }" in frag["html"]
    assert "html, body { background" not in frag["html"]


def test_the_wrapper_is_its_own_stacking_context(frag):
    """The bug the structural tests above all passed through.

    The sheet layers the paper on <html> and keeps <body> transparent so the
    `z-index:-1` aurora can sit between them. That works only because the ROOT
    element's background is painted as the canvas, before any negative-z
    descendant — a privilege no ordinary <div> has, since a div's background is
    painted with the in-flow blocks, i.e. AFTER negative-z children. Collapsed
    onto a plain wrapper the wash was buried under the wrapper's own paper.

    Measured in a host page that owns <body> and paints itself dark, which is the
    only condition that shows it: on a light host the missing paint is invisible.
    """
    assert ".doc-root { background: var(--paper); isolation: isolate; }" in frag["html"]


def test_the_body_transparency_does_not_survive_as_a_self_override(frag):
    # `body { background: transparent }` is deliberate in the document build, but
    # with html and body collapsed into one wrapper it lands in the SAME rule set
    # and simply erases the paper — which is what left the wrapper transparent on
    # the first attempt, invisible against a light host.
    assert "background: transparent" not in frag["html"]


# ------------------------------------------------------- self-containment

def test_fonts_are_inlined_as_data_uris(frag):
    html = frag["html"]
    assert "data:font/woff2;base64" in html
    assert "url('fonts/" not in html
    # One data URI per face that ships, and each must decode — a truncated
    # base64 payload renders as a silent fallback, not an error.
    faces = re.findall(r"url\('data:font/woff2;base64,([A-Za-z0-9+/=]+)'\)", html)
    assert len(faces) == len(render.font_paths()), (len(faces), len(render.font_paths()))
    for payload in faces:
        assert base64.b64decode(payload)[:4] == b"wOF2"


def test_no_external_reference_survives(frag):
    assert not render.EXTERNAL_REF_RE.findall(frag["html"])


def test_comments_are_stripped_from_the_embedded_sheet(frag):
    # Dead weight in a published page, and the sheet's own prose mentions
    # `<html>`/`<body>`, which made the frame check above cry wolf.
    sheet = frag["html"][:frag["html"].index("</style>")]
    assert sheet.count("/*") <= 1, "only the token block's own note may remain"


def test_an_unknown_font_is_refused(tmp_path):
    # A bogus id would produce a wrapper class no rule matches: the default face
    # with no warning anywhere.
    with pytest.raises(ValueError, match="unknown font"):
        render.render_fragment("# X", source_format="markdown", title="X",
                               font="comic-sans", workdir=str(tmp_path))


# ------------------------------------------------------- through the MCP tool

@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    mcp_server.configure({"db_path": str(tmp_path / "t.db"), "database_url": None})
    return mcp_server


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


def test_publish_prepare_hands_over_the_fragment(wired, tmp_path):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Publishable", "kind": "report",
                     "content": "# Publishable\n\nA body line for the description.\n"})
    prep = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})

    # The path handed to the Artifact tool must be the fragment, not the
    # standalone document — that swap is the whole point of the mode.
    assert prep["file_path"].endswith("fragment.html")
    assert prep["document_path"].endswith("artifact.html")
    assert prep["file_path"] != prep["document_path"]
    assert not prep["warnings"], prep["warnings"]

    html = open(prep["file_path"], encoding="utf-8").read()
    assert "<body" not in markup_of(html).lower()
    assert prep["render_bytes"] == len(html.encode("utf-8"))
    assert prep["size_ok"] is True
    assert "do not edit it" in prep["next_step"]


# --------------------------------------------------------------- table breakout

def test_fragment_wraps_top_level_tables_too(tmp_path):
    """The Lua filter runs on the fragment's own pandoc invocation as well as the
    standalone one — the fragment path builds its own argv and could silently
    have been left off the filter."""
    md = "# T\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    out = render.render_fragment(md, source_format="markdown", title="T",
                                 workdir=str(tmp_path))
    assert re.search(r'<div class="table-wrap">\s*<table', out["html"])


def test_fragment_css_still_carries_the_table_wrap_rule():
    """fragment_css() does string surgery on tokens.css (strips comments, moves
    selectors) — cheap guard that none of it accidentally eats the breakout
    rule along the way."""
    assert ".table-wrap {" in render.fragment_css()


def test_the_fragment_is_regenerated_rather_than_kept_in_step(wired):
    # Derived state: exporting twice must produce the same bytes, which is what
    # lets publish_prepare rebuild it instead of storing a sibling file that
    # could fall out of step with artifact.html.
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Twice", "content": "# Twice\n\nLine.\n"})
    first = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    body = open(first["file_path"], encoding="utf-8").read()
    second = _call(wired, "treasure_publish_prepare", {"ident": wrapped["id"]})
    assert open(second["file_path"], encoding="utf-8").read() == body
    assert second["render_bytes"] == first["render_bytes"]
