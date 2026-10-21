"""Published identity: a real `<dl>` + JSON-LD travel INSIDE the artifact, and
everything context-bearing (tags, origin, source paths, provenance,
staleness) goes to a LOCAL agent-notes.md sidecar instead — see render.py's
`_json_ld_script`/`_agent_notes_details` and service.py's `_write_agent_notes`.

Test 8, the disclosure test, is the one that matters most: if it ever fails,
the split this feature exists to enforce has broken.
"""
import json
import re
import subprocess
from pathlib import Path

from flightdeck import db
from flightdeck.treasures import render, service, store

DOC_META = {
    "title": "Báo cáo Helpdesk", "kind": "report", "status": "published",
    "version": 3, "language": "en", "id": "abc123def456",
    "authored_at": "2026-08-01T00:00:00+00:00",
    "source_checksum": "deadbeef" * 8,
}


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


def _json_ld(html: str) -> dict:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                  html, re.S)
    assert m, "no JSON-LD script tag found"
    return json.loads(m.group(1))


# --------------------------------------------------------------------- 1, 3

def test_json_ld_parses_and_carries_every_published_field(tmp_path):
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", doc_meta=DOC_META, workdir=str(tmp_path))
    data = _json_ld(out["html"])
    assert data["@context"] == "https://schema.org"
    assert data["@type"] == "TechArticle"
    assert data["name"] == DOC_META["title"]
    assert data["genre"] == DOC_META["kind"]
    assert data["creativeWorkStatus"] == DOC_META["status"]
    assert data["version"] == DOC_META["version"]
    assert data["inLanguage"] == DOC_META["language"]
    assert data["identifier"] == DOC_META["id"]
    assert data["dateCreated"] == DOC_META["authored_at"]
    assert data["sha256"] == {"@type": "PropertyValue", "name": "source-sha256",
                             "value": DOC_META["source_checksum"]}
    # 3. render_checksum is the checksum of the file THIS block sits inside,
    # so it can never be correct — it must never appear here at all.
    assert "render_checksum" not in json.dumps(data)
    assert "renderChecksum" not in json.dumps(data)


def test_missing_fields_are_omitted_not_null(tmp_path):
    meta = dict(DOC_META)
    del meta["authored_at"]
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", doc_meta=meta, workdir=str(tmp_path))
    data = _json_ld(out["html"])
    assert "dateCreated" not in data
    assert None not in data.values()


# ------------------------------------------------------------------------ 2

def test_title_with_script_close_tag_cannot_break_out(tmp_path):
    meta = dict(DOC_META,
               title='Report</script><script>alert(1)</script> and <a<b')
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", doc_meta=meta, workdir=str(tmp_path))
    html = out["html"]
    # Exactly one </script> in the whole document — the JSON-LD block's own
    # closing tag. The attacker-controlled ones must never appear literally.
    assert html.count("</script>") == 1
    data = _json_ld(html)
    assert data["name"] == meta["title"]


# ------------------------------------------------------------------------ 4

def test_dl_survives_pandoc_markdown_extraction(tmp_path):
    out = render.render("# T\n\nBody.\n", source_format="markdown", title="T",
                        language="en", doc_meta=DOC_META, workdir=str(tmp_path))
    html = out["html"]
    assert "<dl>" in html
    assert f"<dt>kind</dt><dd>{DOC_META['kind']}</dd>" in html
    proc = subprocess.run([render.pandoc_path(), "-f", "html", "-t", "markdown"],
                          input=html, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    md = proc.stdout
    assert DOC_META["kind"] in md
    assert DOC_META["status"] in md
    assert str(DOC_META["version"]) in md


# ------------------------------------------------------------------------ 5

def test_kind_appears_as_text_not_only_in_class(tmp_path):
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", kind="spec-review",
                        doc_meta=dict(DOC_META, kind="spec-review"),
                        workdir=str(tmp_path))
    html = out["html"]
    assert 'class="kind-spec-review' in html            # unchanged: the class
    assert "<dt>kind</dt><dd>spec-review</dd>" in html   # NEW: also real text


# ------------------------------------------------------------------------ 6

TABLE_ONLY_MD = "# T\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"


def test_reading_guide_names_only_components_present(tmp_path):
    out = render.render(TABLE_ONLY_MD, source_format="markdown", title="T",
                        language="en", doc_meta=DOC_META, workdir=str(tmp_path))
    html = out["html"]
    notes_body = html[html.index('<div class="agent-notes-body">'):
                      html.index("</details>")]
    assert "table-wrap" in notes_body
    assert "hero" not in notes_body
    assert "card" not in notes_body
    assert "grid" not in notes_body
    assert "diagram" not in notes_body


def test_reading_guide_mentions_hero_when_present(tmp_path):
    hero_md = ('<div data-component="hero">\n\nEyebrow\n\n# Title\n\n</div>\n\n'
              "Body after the hero.\n")
    out = render.render(hero_md, source_format="markdown", title="T",
                        language="en", doc_meta=DOC_META, workdir=str(tmp_path))
    html = out["html"]
    notes_body = html[html.index('<div class="agent-notes-body">'):
                      html.index("</details>")]
    assert "hero" in notes_body
    assert "table-wrap" not in notes_body


# ------------------------------------------------------------------------ 7

def test_details_emitted_with_identity_when_no_operator_note(tmp_path):
    out = render.render("# T\n", source_format="markdown", title="T",
                        language="en", doc_meta=DOC_META, workdir=str(tmp_path))
    html = out["html"]
    assert '<details id="agent-notes">' in html
    body = html[html.index('<div class="agent-notes-body">'):
                html.index("</details>")]
    assert "<dl>" in body
    assert "<pre>" not in body       # no operator note configured


# ------------------------------------------------------------------------ 8

def test_disclosure_fragment_never_leaks_local_tier_fields(monkeypatch, tmp_path):
    """THE test that matters: nothing context-bearing may ever reach a
    published fragment. A branch-name-shaped and a session-id-shaped string
    are included deliberately, on top of the real origin_path/tags — if any
    of these ever show up in the fragment, the split this feature exists to
    enforce has broken."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)

    origin_path = "/home/nathando/Documents/Projects/tickets/crm-event/spec.md"
    session_id = "90fb37ee-63a1-4f2e-9c3d-1a2b3c4d5e6f"
    branch_name = "CRM-11475-presale-embed"

    out = service.wrap(
        conn, title="Internal Report", content="# Internal Report\n\nBody.\n",
        origin_kind="doc_file", origin_id=session_id, origin_path=origin_path)
    service.set_tags(conn, out["id"], ["CRM-11372", "CRM-11475", branch_name])

    row = service.get(conn, out["id"])
    source_path = row["source_path"]
    # Tags are stored lowercased (store._clean_tags) — check what actually
    # ends up in the tags table, not the case the caller wrote.
    assert set(row["tags"]) == {"crm-11372", "crm-11475", branch_name.lower()}

    frag = service.export_fragment(conn, out["id"])
    html = Path(frag["fragment_path"]).read_text(encoding="utf-8")

    forbidden_strings = (origin_path, source_path, session_id,
                        branch_name.lower(), "crm-11372", "crm-11475")
    for forbidden in forbidden_strings:
        assert forbidden not in html, (
            f"{forbidden!r} leaked into the published fragment")
    # Same guarantee for the standalone artifact.html, not just the fragment.
    artifact_html = Path(row["artifact_path"]).read_text(encoding="utf-8")
    for forbidden in forbidden_strings:
        assert forbidden not in artifact_html


# ------------------------------------------------------------------------ 9

def test_agent_notes_md_written_with_both_tiers(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    origin_path = "/home/nathando/Documents/Projects/tickets/crm-event/spec.md"

    out = service.wrap(conn, title="Doc", content="# Doc\n\nBody.\n",
                       origin_kind="doc_file", origin_id="sess-42",
                       origin_path=origin_path)
    assert Path(out["agent_notes_path"]).is_file()   # written on every render
    service.set_tags(conn, out["id"], ["CRM-11372"])
    again = service.rerender(conn, out["id"])        # refresh with tags now set

    notes_path = Path(again["agent_notes_path"])
    assert notes_path.is_file()
    text = notes_path.read_text(encoding="utf-8")
    assert text.startswith("# Doc")
    assert "local companion" in text.lower()

    # published tier — repeated here for a self-standing local record
    assert out["kind"] in text
    assert out["status"] in text
    assert str(out["version"]) in text

    # local-only tier — must be HERE, and must NOT be in the artifact itself.
    # Tags are stored lowercased (store._clean_tags), so "CRM-11372" in ->
    # "crm-11372" out.
    assert origin_path in text
    assert "crm-11372" in text
    assert "sess-42" in text

    artifact_html = Path(again["artifact_path"]).read_text(encoding="utf-8")
    assert origin_path not in artifact_html
    assert "crm-11372" not in artifact_html
    assert "sess-42" not in artifact_html
