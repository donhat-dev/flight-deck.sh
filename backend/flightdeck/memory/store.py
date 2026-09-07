"""Reading the memory store: one file's frontmatter, body links, and the index.

This module knows the on-disk format and nothing else. Lint, graph and search all sit
on top of it, so a format change lands in exactly one place.

Two link shapes are parsed apart on purpose. `[[name]]` is a plain link whose target is
a memory's `name`. `[[some target|label]]` is an alias link, and in this store its
targets do not follow the naming rule at all — they point at things that do not exist
yet. Reporting them as broken links would be wrong; dropping them would silently turn
their source file into an isolated node. They get their own list, and lint gives them
their own finding kind.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

INDEX_NAME = "MEMORY.md"

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
_WIKILINK = re.compile(r"\[\[([^\]\[]+)\]\]")
_INDEX_LINE = re.compile(r"^-\s*\[([^\]]+)\]\(([^)]+)\)\s*(?:—|--|-)?\s*(.*)$")


@dataclass
class Memory:
    name: str
    path: Path
    description: str = ""
    type: str | None = None
    origin_session: str | None = None
    source: str | None = None
    tier: str | None = None
    mtime: float = 0.0
    links: list[str] = field(default_factory=list)
    alias_links: list[str] = field(default_factory=list)
    body: str = ""

    @property
    def filename(self) -> str:
        return self.path.name


def _scalar(raw: str, key: str) -> str | None:
    """One `key: value` out of a frontmatter block, at any indent. Quotes stripped.

    A hand-rolled reader rather than a YAML dependency: the frontmatter this store
    writes is a fixed shallow shape, and the backend has no YAML requirement today.
    """
    m = re.search(rf"^\s*{re.escape(key)}:\s*(.*?)\s*$", raw, re.M)
    if not m:
        return None
    value = m.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value or None


def _parse(path: Path) -> Memory:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER.match(text)
    front, body = (m.group(1), text[m.end():]) if m else ("", text)

    plain: list[str] = []
    alias: list[str] = []
    seen: set[str] = set()
    for target in _WIKILINK.findall(body):
        if "|" in target:
            head = target.split("|", 1)[0].strip()
            if head and head not in seen:
                seen.add(head)
                alias.append(head)
        else:
            head = target.strip()
            if head and head not in seen:
                seen.add(head)
                plain.append(head)

    return Memory(
        name=_scalar(front, "name") or path.stem,
        path=path,
        description=_scalar(front, "description") or "",
        type=_scalar(front, "type"),
        origin_session=_scalar(front, "originSessionId"),
        source=_scalar(front, "source"),
        tier=_scalar(front, "tier"),
        mtime=path.stat().st_mtime,
        links=plain,
        alias_links=alias,
        body=body,
    )


def load_all(memory_dir: Path) -> list[Memory]:
    """Every memory in the directory, index file excluded, sorted by name.

    Non-recursive on purpose: the harness scans recursively, but a nested .md is a
    defect this domain should surface rather than silently adopt as a memory.
    """
    memory_dir = Path(memory_dir)
    if not memory_dir.is_dir():
        return []
    return sorted(
        (_parse(p) for p in memory_dir.glob("*.md") if p.name != INDEX_NAME),
        key=lambda m: m.name,
    )


def load_index(memory_dir: Path) -> list[tuple[str, str, str]]:
    """`(title, filename, hook)` for each pointer line in MEMORY.md."""
    index = Path(memory_dir) / INDEX_NAME
    if not index.is_file():
        return []
    out = []
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INDEX_LINE.match(line.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return out
