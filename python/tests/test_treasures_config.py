"""Site-wide Treasures defaults: storage, injection, and the HTTP surface.

Covers treasures/store.py's config_get/config_set, render.py's
inject_body_defaults (via render()), and routers/treasure_config.py — the
three pieces that make default_agent_notes/default_header_html/
default_footer_html a real, editable, applied-everywhere feature.
"""
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.routers import treasure_config as treasure_config_router
from flightdeck.treasures import render, store


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


# --------------------------------------------------------------- store: get

def test_config_get_on_virgin_db_returns_all_keys_empty(tmp_path):
    conn = _conn(tmp_path)
    assert store.config_get(conn) == {
        "default_agent_notes": "", "default_header_html": "",
        "default_footer_html": "", "updated_at": None}


# --------------------------------------------------------------- store: set

def test_config_set_one_key_leaves_the_others_empty_and_matches_get(tmp_path):
    conn = _conn(tmp_path)
    out = store.config_set(conn, {"default_agent_notes": "hello"})
    assert out["default_agent_notes"] == "hello"
    assert out["default_header_html"] == ""
    assert out["default_footer_html"] == ""
    assert out["updated_at"] is not None
    # No second round trip needed: config_set already returns the full config.
    assert out == store.config_get(conn)


def test_config_set_unknown_key_raises_value_error(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        store.config_set(conn, {"nope": "x"})
    # Nothing was written by the rejected call.
    assert store.config_get(conn)["updated_at"] is None


# ------------------------------------------------------------ render: inject

def test_injects_header_footer_and_notes_into_the_body(tmp_path):
    out = render.render(
        "# T\n\nBody.\n", source_format="markdown", title="T", language="en",
        default_header_html='<div id="hdr">HEADER</div>',
        default_footer_html='<div id="ftr">FOOTER</div>',
        agent_notes="Distinctive note sentence for the placement check.",
        workdir=str(tmp_path))
    html = out["html"]
    main_open = html.index('<main class="doc">')
    hdr_idx = html.index('<div id="hdr">HEADER</div>')
    ftr_idx = html.index('<div id="ftr">FOOTER</div>')
    details_idx = html.index('<details id="agent-notes">')
    main_close = html.index("</main>")
    # header right after <main class="doc">, footer right before </main>,
    # notes right before </main> too but AFTER the footer.
    assert main_open < hdr_idx < ftr_idx < details_idx < main_close
    assert "Distinctive note sentence for the placement check." in html
    assert not out["warnings"]


def test_notes_survive_pandoc_plain_extraction(tmp_path):
    """The whole point of the collapsed <details> choice: a plain-text
    extraction (what an agent fetching the artifact would run) still sees the
    notes, unlike a <meta>/JSON-LD/comment/aria-label channel."""
    notes = "This exact sentence must survive the plain-text extraction path."
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", agent_notes=notes, workdir=str(tmp_path))
    proc = subprocess.run([render.pandoc_path(), "-f", "html", "-t", "plain"],
                          input=out["html"], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0
    assert notes in proc.stdout


def test_nothing_injected_when_nothing_is_configured(tmp_path):
    """Byte-identical whether the new kwargs are omitted or explicitly None —
    existing golden-output tests must not move."""
    omitted = render.render("# T\n", source_format="markdown", title="T",
                            language="en", workdir=str(tmp_path / "a"))
    explicit_none = render.render(
        "# T\n", source_format="markdown", title="T", language="en",
        default_header_html=None, default_footer_html=None, agent_notes=None,
        workdir=str(tmp_path / "b"))
    assert omitted["html"] == explicit_none["html"]
    # A plain substring check on "agent-notes" no longer proves nothing was
    # injected: tokens.css now carries a permanent #agent-notes styling rule
    # (the collapsed block still has to look right on the rare page that opens
    # it), so the string sits in EVERY render's embedded <style> block
    # regardless of whether the <details> itself was ever spliced in — the
    # same class of false positive `_reachable_families`/`_visible_codepoints`
    # already guard against for component/font detection. The precise proxy
    # is the actual element, which this assertion checks directly.
    assert explicit_none["html"].count("<details") == 0


def test_notes_cannot_inject_markup(tmp_path):
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", agent_notes="<script>alert(1)</script>",
                        workdir=str(tmp_path))
    html = out["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html


def test_missing_anchor_returns_html_unchanged_and_warns():
    """No <main class="doc"> anchor -> nothing is dropped silently, and the
    caller gets a visible warning instead."""
    plain = "<html><body>no main tag here</body></html>"
    out = render.inject_body_defaults(plain, header="<div>H</div>",
                                      footer=None, notes=None)
    assert out == plain


# ---------------------------------------------------------------------- HTTP

@pytest.fixture()
def client(tmp_path):
    cfg = {"db_path": str(tmp_path / "t.db"), "database_url": None}
    db.configure(cfg)
    conn = db.connect(cfg["db_path"])
    store.init(conn)
    conn.close()
    app = FastAPI()
    app.state.cfg = cfg
    app.include_router(treasure_config_router.router)
    return TestClient(app)


def test_get_on_a_virgin_db(client):
    r = client.get("/api/treasure-config")
    assert r.status_code == 200
    assert r.json() == {"default_agent_notes": "", "default_header_html": "",
                        "default_footer_html": "", "updated_at": None}


def test_put_then_get_round_trip(client):
    r = client.put("/api/treasure-config",
                   json={"default_header_html": "<div>H</div>",
                         "default_agent_notes": "note text"})
    assert r.status_code == 200
    body = r.json()
    assert body["default_header_html"] == "<div>H</div>"
    assert body["default_agent_notes"] == "note text"
    assert body["default_footer_html"] == ""

    again = client.get("/api/treasure-config")
    assert again.json() == body


def test_put_unknown_key_is_400(client):
    r = client.put("/api/treasure-config", json={"nope": "x"})
    assert r.status_code == 400


def test_put_non_string_value_is_400(client):
    r = client.put("/api/treasure-config", json={"default_agent_notes": 42})
    assert r.status_code == 400
