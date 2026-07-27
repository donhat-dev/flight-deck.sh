import json
from pathlib import Path

from flightdeck.treasures import filestore


def test_slugify_folds_vietnamese_diacritics():
    assert filestore.slugify("Báo cáo Helpdesk 2026!") == "bao-cao-helpdesk-2026"
    assert filestore.slugify("Đánh giá — hiệu quả") == "danh-gia-hieu-qua"
    assert filestore.slugify("   ") == "untitled"


def test_new_id_and_checksum_are_stable_shapes():
    art_id = filestore.new_id()
    assert len(art_id) == 12 and art_id == art_id.lower()
    assert art_id.isalnum()
    assert filestore.checksum("abc") == filestore.checksum("abc")
    assert len(filestore.checksum("abc")) == 64
    assert filestore.checksum("abc") != filestore.checksum("abd")


def test_root_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    assert filestore.root() == tmp_path / "store"


def test_write_version_then_next_version(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    art_dir = filestore.artifact_dir("bao-cao", "abc123def456")
    assert art_dir.name == "bao-cao-abc123def456"
    assert filestore.next_version(art_dir) == 1

    paths = filestore.write_version(art_dir, 1, "# Tiêu đề\n", "md",
                                    "<!doctype html><html></html>")
    assert Path(paths["source_path"]).read_text(encoding="utf-8") == "# Tiêu đề\n"
    assert Path(paths["artifact_path"]).name == "artifact.html"
    assert Path(paths["version_dir"]).name == "v1"
    assert filestore.next_version(art_dir) == 2

    filestore.write_version(art_dir, 2, "# Tiêu đề 2\n", "md", "<html>2</html>")
    assert filestore.next_version(art_dir) == 3
    assert (art_dir / "v1" / "artifact.html").exists()   # history kept


def test_meta_sidecar_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    art_dir = filestore.artifact_dir("x", "aaaaaaaaaaaa")
    assert filestore.read_meta(art_dir) is None
    meta = {"id": "aaaaaaaaaaaa", "title": "X", "version": 1}
    path = filestore.write_meta(art_dir, meta)
    assert json.loads(Path(path).read_text(encoding="utf-8"))["title"] == "X"
    assert filestore.read_meta(art_dir)["version"] == 1
