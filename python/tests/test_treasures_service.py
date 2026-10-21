import pytest
import json
import re
from pathlib import Path

from flightdeck import db
from flightdeck.treasures import filestore, service, store

MD = "# Báo cáo\n\nNội dung tiếng Việt.\n"


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


def _body_class(html: str) -> str:
    """The actual class on <body>, never the tokens.css selector text — every
    font's `body.font-*{...}` rule is embedded in every render regardless of
    which one is selected, so a plain substring check on the whole HTML
    passes no matter what <body> actually got (this hid the `for font in
    ...:` loop-variable-clobber bug for a full round)."""
    m = re.search(r'<body class="([^"]*)"', html)
    assert m, "no <body class=...> tag found"
    return m.group(1)


def test_wrap_writes_files_and_indexes_row(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Báo cáo", content=MD, language="vi",
                       origin_kind="claude_session", origin_id="sess-1")

    # files on disk
    art_dir = Path(out["dir_path"])
    assert art_dir.name.startswith("bao-cao-")
    assert (art_dir / "v1" / "artifact.html").is_file()
    assert (art_dir / "v1" / "source.md").read_text(encoding="utf-8") == MD
    meta = json.loads((art_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["id"] == out["id"]

    # index row agrees with disk
    row = store.get(conn, out["id"])
    assert row["dir_path"] == str(art_dir)
    assert row["language"] == "vi"
    assert row["status"] == "draft"
    assert row["version"] == 1
    assert row["origin_id"] == "sess-1"
    assert row["source_checksum"] == filestore.checksum(MD)
    assert row["render_bytes"] == out["render_bytes"] > 0
    assert out["warnings"] == []


def test_wrap_same_id_bumps_version_and_keeps_history(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    first = service.wrap(conn, title="Doc", content="# One\n")
    second = service.wrap(conn, title="Doc", content="# Two\n",
                          artifact_id=first["id"])
    assert second["id"] == first["id"]
    assert second["version"] == 2
    art_dir = Path(first["dir_path"])
    assert (art_dir / "v1" / "source.md").read_text(encoding="utf-8") == "# One\n"
    assert (art_dir / "v2" / "source.md").read_text(encoding="utf-8") == "# Two\n"
    assert len(store.list_rows(conn)) == 1
    assert store.get(conn, first["id"])["version"] == 2


def test_wrap_defaults_font_and_inherits_it_across_versions(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    first = service.wrap(conn, title="Doc", content="# One\n")
    assert first["font"] == "space-grotesk"           # house default, unset
    assert _body_class(Path(first["artifact_path"]).read_text(encoding="utf-8")) \
        == "kind-report font-space-grotesk"

    picked = service.wrap(conn, title="Doc", content="# Two\n",
                          artifact_id=first["id"], font="jetbrains-mono")
    assert picked["font"] == "jetbrains-mono"

    # omitted on the next wrap -> inherits the artifact's current font, not the house default
    again = service.wrap(conn, title="Doc", content="# Three\n",
                         artifact_id=first["id"])
    assert again["font"] == "jetbrains-mono"
    assert _body_class(Path(again["artifact_path"]).read_text(encoding="utf-8")) \
        == "kind-report font-jetbrains-mono"


def test_wrap_rejects_unknown_font(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        service.wrap(conn, title="Doc", content="# One\n", font="comic-sans")


def test_custom_head_is_spliced_verbatim_and_survives_rerender(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    head = '<meta name="robots" content="noindex">'
    out = service.wrap(conn, title="Doc", content="# One\n", custom_head=head)
    assert head in Path(out["artifact_path"]).read_text(encoding="utf-8")

    again = service.rerender(conn, out["id"])
    assert head in Path(again["artifact_path"]).read_text(encoding="utf-8")


def test_update_meta_rejects_unknown_font(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Doc", content="# One\n")
    with pytest.raises(ValueError):
        service.update_meta(conn, out["id"], font="comic-sans")


def test_update_meta_font_change_needs_rerender_to_reach_disk(monkeypatch, tmp_path):
    """update_meta is metadata-only — the point of the split is that a stale
    artifact.html survives until an explicit rerender, same as kind/status."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Doc", content="# One\n")  # space-grotesk
    before_html = Path(out["artifact_path"]).read_text(encoding="utf-8")

    service.update_meta(conn, out["id"], font="jetbrains-mono")
    assert Path(out["artifact_path"]).read_text(encoding="utf-8") == before_html

    service.rerender(conn, out["id"])
    assert _body_class(Path(out["artifact_path"]).read_text(encoding="utf-8")) \
        == "kind-report font-jetbrains-mono"


def test_get_can_include_source_and_html(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Doc", content="# Hello\n")
    plain = service.get(conn, out["id"])
    assert "source" not in plain and "html" not in plain
    full = service.get(conn, out["id"], include_source=True, include_html=True)
    assert full["source"] == "# Hello\n"
    assert full["html"].lstrip().startswith("<!doctype html>")
    assert service.get(conn, "missing") is None


def test_wrap_accepts_html_fragment(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Frag", content="<p>Xin chào</p>",
                       source_format="html")
    assert store.get(conn, out["id"])["source_format"] == "html"
    assert (Path(out["dir_path"]) / "v1" / "source.html").is_file()


def test_failed_render_leaves_no_orphan_dir(monkeypatch, tmp_path):
    """A render failure must not leave an empty artifact dir behind — the
    filestore and the index would then disagree about what exists."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    monkeypatch.setattr(service.render, "render",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pandoc failed")))
    try:
        service.wrap(conn, title="Doomed doc", content="# Doomed\n")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the render failure to propagate")
    root = filestore.root()
    assert not list(root.glob("doomed-doc-*")), "orphan artifact dir left behind"
    assert store.list_rows(conn) == []


def test_rerender_updates_output_in_place_without_bumping_version(monkeypatch, tmp_path):
    """A template change must refresh the rendered HTML without adding an empty
    version to the history — the source did not change."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Doc", content="# Doc\n\nBody.\n")
    art = Path(out["artifact_path"])
    before_html = art.read_text(encoding="utf-8")
    before = store.get(conn, out["id"])

    again = service.rerender(conn, out["id"])
    after = store.get(conn, out["id"])
    assert after["version"] == before["version"] == 1      # no version bump
    assert not (Path(out["dir_path"]) / "v2").exists()
    assert art.read_text(encoding="utf-8") == before_html   # same template -> same bytes
    assert after["render_checksum"] == before["render_checksum"]
    assert again["warnings"] == []
    # source untouched
    assert (Path(out["dir_path"]) / "v1" / "source.md").read_text(encoding="utf-8") == "# Doc\n\nBody.\n"


def test_rerender_all_reports_failures_and_keeps_going(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    a = service.wrap(conn, title="Good one", content="# Good\n\nBody.\n")
    service.wrap(conn, title="Second", content="# Second\n\nBody.\n")

    calls = {"n": 0}
    real = service.render.render

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pandoc failed: synthetic")
        return real(*args, **kwargs)

    monkeypatch.setattr(service.render, "render", flaky)
    out = service.rerender_all(conn)
    assert out["rerendered"] == 1
    assert len(out["failed"]) == 1
    assert "synthetic" in out["failed"][0]["error"]
    assert service.get(conn, a["id"]) is not None          # nothing was destroyed


def test_delete_removes_files_and_row(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Throwaway", content="# Throwaway\n\nBody.\n")
    art_dir = Path(out["dir_path"])
    assert art_dir.is_dir()

    res = service.delete(conn, out["id"])
    assert res["deleted"] == out["id"]
    assert res["removed_files"] >= 3          # source, artifact, meta.json
    assert not art_dir.exists()
    assert store.get(conn, out["id"]) is None
    assert store.list_rows(conn) == []


def test_delete_unknown_ident_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    with pytest.raises(LookupError):
        service.delete(conn, "nope")


def test_delete_refuses_a_path_outside_the_filestore(monkeypatch, tmp_path):
    """NEGATIVE TEST — attempt the blocked action and prove nothing was lost.

    A corrupted or hand-edited row must not be able to aim delete at somewhere
    else on disk. The victim file has to survive AND the row must stay, so a
    half-done delete cannot orphan the index.
    """
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    out = service.wrap(conn, title="Hijack", content="# Hijack\n\nBody.\n")

    outside = tmp_path / "precious"
    outside.mkdir()
    victim = outside / "do-not-touch.txt"
    victim.write_text("keep me", encoding="utf-8")

    row = store.get(conn, out["id"])
    row["dir_path"] = str(outside)            # point the row off the filestore
    store.upsert(conn, row)

    with pytest.raises(PermissionError):
        service.delete(conn, out["id"])
    assert victim.read_text(encoding="utf-8") == "keep me"   # untouched
    assert outside.is_dir()
    assert store.get(conn, out["id"]) is not None            # row kept


def test_delete_refuses_the_filestore_root_itself(monkeypatch, tmp_path):
    """A row pointing at the root would wipe the whole library."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = _conn(tmp_path)
    keeper = service.wrap(conn, title="Keeper", content="# Keeper\n\nBody.\n")
    row = store.get(conn, keeper["id"])
    row["dir_path"] = str(filestore.root())
    store.upsert(conn, row)

    with pytest.raises(PermissionError):
        service.delete(conn, keeper["id"])
    assert filestore.root().is_dir()
    assert list(filestore.root().iterdir())                  # library intact
