"""Artifact files on disk. The filestore is the system of record.

Layout (root = TREASURES_STORE, default ~/.flightdeck/treasures):

    <slug>-<id>/
      meta.json          index row mirrored to disk, so the DB is rebuildable
      assets/            images the source references
      v1/{source.md, artifact.html}
      v2/{source.md, artifact.html}

Kept outside the repo on purpose: artifacts are data, some of it internal.
"""
import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path

META_NAME = "meta.json"


def root() -> Path:
    return Path(os.environ.get("TREASURES_STORE")
                or Path.home() / ".flightdeck" / "treasures").expanduser()


def read_roots() -> list[Path]:
    """Directories the server may read a SOURCE document from.

    `treasure_wrap(source_path=…)` and `treasure_discover(roots=…)` let an agent
    name a path that THIS process then reads — which is a different trust
    boundary from passing `content`, where the agent had to read the file itself
    and therefore passed its own permission gate. Without a boundary an agent
    could aim the server at `~/.ssh/id_rsa` and the bytes would land in an
    artifact that is then publishable.

    Default roots are the workspace, the Claude transcript tree, and the
    filestore. Override with TREASURES_READ_ROOTS (os.pathsep-separated).
    """
    env = os.environ.get("TREASURES_READ_ROOTS")
    if env:
        raw = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        raw = [os.environ.get("FLIGHTDECK_WORKSPACE") or str(Path.cwd()),
               "~/.claude/projects",
               str(root())]
    out = []
    for p in raw:
        try:
            out.append(Path(p).expanduser().resolve())
        except OSError:
            continue
    return out


def read_source(path: str) -> str:
    """Read a source document, refusing anything outside `read_roots()`.

    Fail-closed: an unreadable or out-of-bounds path raises rather than
    returning empty content, so a wrap can never silently index nothing.
    """
    target = Path(path).expanduser()
    try:
        resolved = target.resolve(strict=True)
    except OSError as e:
        raise FileNotFoundError(f"cannot read source: {path}") from e
    if resolved.is_dir():
        raise IsADirectoryError(f"source is a directory: {path}")
    roots = read_roots()
    if not any(resolved == r or r in resolved.parents for r in roots):
        raise PermissionError(
            f"refusing to read {resolved}: outside the allowed source roots "
            f"({', '.join(str(r) for r in roots)}). Set TREASURES_READ_ROOTS to "
            f"widen it deliberately.")
    return resolved.read_text(encoding="utf-8")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(title: str) -> str:
    """ASCII kebab-case. Vietnamese diacritics are folded (NFD then drop the
    combining marks) so `Báo cáo` becomes `bao-cao`; đ/Đ have no combining form
    and are mapped explicitly."""
    text = (title or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60] or "untitled"


def artifact_dir_path(slug: str, art_id: str) -> Path:
    """Where the artifact WOULD live. Creates nothing, so a caller can render
    first and only materialize the directory once the render succeeded."""
    return root() / f"{slug}-{art_id}"


def artifact_dir(slug: str, art_id: str) -> Path:
    path = artifact_dir_path(slug, art_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "assets").mkdir(exist_ok=True)
    return path


def next_version(art_dir: Path) -> int:
    versions = [int(p.name[1:]) for p in Path(art_dir).glob("v*")
                if p.is_dir() and p.name[1:].isdigit()]
    return (max(versions) + 1) if versions else 1


def write_version(art_dir: Path, version: int, source_text: str,
                  source_ext: str, html: str) -> dict:
    vdir = Path(art_dir) / f"v{version}"
    vdir.mkdir(parents=True, exist_ok=True)
    source_path = vdir / f"source.{source_ext}"
    artifact_path = vdir / "artifact.html"
    source_path.write_text(source_text, encoding="utf-8")
    artifact_path.write_text(html, encoding="utf-8")
    return {"version_dir": str(vdir), "source_path": str(source_path),
            "artifact_path": str(artifact_path)}


def write_meta(art_dir: Path, meta: dict) -> str:
    path = Path(art_dir) / META_NAME
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)


def read_meta(art_dir: Path):
    path = Path(art_dir) / META_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
