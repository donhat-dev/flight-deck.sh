"""Find artifact bodies the agent already wrote into the transcript tree.

The conservative rule: a candidate is a `Write` tool call whose file_path ends
in .md or .html and whose content is at least MIN_BYTES. That is exactly "a
document the agent wrote to a file", which keeps prose and code fences out.

The JSONL is read directly rather than through transcript.py, because that
module truncates long tool inputs and would cut document bodies short.

Every bound (file count, age, size) is reported in the result — a scan must
never silently under-report.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.treasures import filestore, service, store

DOC_SUFFIXES = (".md", ".html", ".htm")
MIN_BYTES = 400
MAX_FILES = 400
PACKAGE_DIR = Path(__file__).resolve().parent

# Codepoints unique to Vietnamese (Latin Extended Additional) plus đ/ơ/ư.
_VN_RE = re.compile(r"[Ạ-ỹĐđƠ-ư]")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r"<title>(.*?)</title>|<h1[^>]*>(.*?)</h1>",
                       re.IGNORECASE | re.DOTALL)


def _language_of(text: str) -> str:
    return "vi" if _VN_RE.search(text) else "en"


def _title_of(text: str, file_path: str, source_format: str) -> str:
    if source_format == "markdown":
        m = _H1_RE.search(text)
        if m:
            return m.group(1).strip()
    else:
        m = _TITLE_RE.search(text)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1) or m.group(2) or "").strip()
    return Path(file_path).stem.replace("-", " ").replace("_", " ").strip() or "untitled"


DOC_SUFFIXES = (".md", ".html", ".htm")


def scan_docs(roots, *, max_files: int = MAX_FILES,
              max_age_days: int | None = None,
              min_bytes: int = MIN_BYTES) -> dict:
    """Find real document FILES under `roots`, newest first.

    Complements `scan`, which mines documents out of transcripts and so can only
    ever see `~/.claude/projects`. A document written straight to the workspace
    (`Subscription_Service/18-….md`) is invisible to that scanner, which is why
    this one exists.

    Every root goes through `filestore.read_source`, so a root outside the
    allowed set is refused rather than silently skipped.
    """
    store_root = filestore.root().resolve()
    cutoff = (time.time() - max_age_days * 86400) if max_age_days else None
    by_checksum: dict[str, dict] = {}
    skipped = {"too_small": 0, "in_filestore": 0, "unreadable": 0,
               "files_dropped_by_cap": 0}
    refused_roots = []

    files: list[Path] = []
    for raw in roots:
        base = Path(raw).expanduser()
        if not base.is_dir():
            refused_roots.append(f"{raw}: not a directory")
            continue
        try:
            resolved = base.resolve(strict=True)
        except OSError as e:
            refused_roots.append(f"{raw}: {e}")
            continue
        allowed = filestore.read_roots()
        if not any(resolved == a or a in resolved.parents for a in allowed):
            refused_roots.append(
                f"{resolved}: outside the allowed source roots")
            continue
        for suffix in DOC_SUFFIXES:
            files += [p for p in resolved.glob(f"**/*{suffix}") if p.is_file()]

    files = [f for f in files if store_root not in f.resolve().parents]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if cutoff:
        files = [f for f in files if f.stat().st_mtime >= cutoff]
    skipped["files_dropped_by_cap"] = max(0, len(files) - max_files)
    files = files[:max_files]

    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped["unreadable"] += 1
            continue
        if len(content.encode("utf-8")) < min_bytes:
            skipped["too_small"] += 1
            continue
        source_format = "markdown" if path.suffix.lower() == ".md" else "html"
        checksum = filestore.checksum(content)
        by_checksum.setdefault(checksum, {
            "checksum": checksum,
            "content": content,
            "source_format": source_format,
            "title": _title_of(content, str(path), source_format),
            "language": _language_of(content),
            "origin_kind": "doc_file",
            "origin_id": None,
            "origin_path": str(path.resolve()),
            "authored_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "bytes": len(content.encode("utf-8")),
        })

    return {
        "candidates": list(by_checksum.values()),
        "skipped": skipped,
        "refused_roots": refused_roots,
        "bounds": {"roots": [str(r) for r in roots], "max_files": max_files,
                   "max_age_days": max_age_days, "min_bytes": min_bytes},
    }


def _candidate(block_input, obj, jsonl_path, store_root):
    path = block_input.get("file_path") or ""
    content = block_input.get("content")
    if not path or not isinstance(content, str):
        return None, "not_a_document"
    if not path.lower().endswith(DOC_SUFFIXES):
        return None, "not_a_document"
    try:
        parents = Path(path).resolve().parents
        # Our own output (the filestore) and our own source (the treasures
        # package, whose pandoc template is an .html file written with Write)
        # must never be discovered — that would loop the tool back on itself.
        if store_root in parents:
            return None, "in_filestore"
        if PACKAGE_DIR in parents:
            return None, "own_source"
    except (OSError, RuntimeError):
        pass
    size = len(content.encode("utf-8"))
    if size < MIN_BYTES:
        return None, "too_small"
    source_format = "markdown" if path.lower().endswith(".md") else "html"
    return {
        "file_path": path,
        "source_format": source_format,
        "title": _title_of(content, path, source_format),
        "language": _language_of(content),
        "bytes": size,
        "checksum": filestore.checksum(content),
        "content": content,
        "origin_kind": "claude_session",
        "origin_id": obj.get("sessionId"),
        "origin_path": str(jsonl_path),
        "authored_at": obj.get("timestamp"),
    }, None


def scan(projects_dir: str, *, max_files: int = MAX_FILES,
         max_age_days: int | None = None, min_bytes: int = MIN_BYTES) -> dict:
    """Walk the transcript tree and return artifact candidates, newest files
    first. Later duplicates of the same checksum collapse into one candidate."""
    root = Path(projects_dir).expanduser()
    store_root = filestore.root().resolve()
    files = sorted(root.glob("**/*.jsonl"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True)
    cutoff = (time.time() - max_age_days * 86400) if max_age_days else None
    if cutoff:
        files = [f for f in files if f.stat().st_mtime >= cutoff]
    dropped_by_cap = max(0, len(files) - max_files)
    files = files[:max_files]

    by_checksum: dict[str, dict] = {}
    skipped = {"not_a_document": 0, "too_small": 0, "in_filestore": 0,
               "own_source": 0,
               "unparseable_lines": 0, "files_dropped_by_cap": dropped_by_cap}
    for jsonl_path in files:
        try:
            handle = jsonl_path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line or '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    skipped["unparseable_lines"] += 1
                    continue
                if obj.get("type") != "assistant":
                    continue
                blocks = (obj.get("message") or {}).get("content")
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Write":
                        # A tool call, but not one that can produce a document
                        # (e.g. Bash) — still worth reporting as skipped, not
                        # silently dropped.
                        skipped["not_a_document"] += 1
                        continue
                    cand, reason = _candidate(block.get("input") or {}, obj,
                                              jsonl_path, store_root)
                    if cand is None:
                        if reason:
                            skipped[reason] += 1
                        continue
                    if cand["bytes"] < min_bytes:
                        skipped["too_small"] += 1
                        continue
                    by_checksum.setdefault(cand["checksum"], cand)

    return {
        "candidates": list(by_checksum.values()),
        "scanned": len(files),
        "skipped": skipped,
        "bounds": {"max_files": max_files, "max_age_days": max_age_days,
                   "min_bytes": min_bytes},
    }


def run(conn, projects_dir: str, *, do_import: bool = False,
        roots=None, **bounds) -> dict:
    """Scan, mark which candidates the store already holds (by source checksum),
    and optionally wrap+index the new ones.

    Without `roots` this mines the transcript tree, as before. With `roots` it
    ALSO walks those directories for real .md/.html files, which is the only way
    a document written straight to the workspace can be found — the transcript
    scanner cannot see outside `~/.claude/projects` by construction.
    """
    result = scan(projects_dir, **bounds)
    if roots:
        docs = scan_docs(roots, **bounds)
        seen = {c["checksum"] for c in result["candidates"]}
        result["candidates"] += [c for c in docs["candidates"]
                                if c["checksum"] not in seen]
        result["skipped"] = {**result["skipped"],
                             **{f"docs_{k}": v for k, v in docs["skipped"].items()}}
        result["refused_roots"] = docs["refused_roots"]
        result["bounds"]["roots"] = docs["bounds"]["roots"]
    known = {r["source_checksum"] for r in store.list_rows(conn, limit=100000)
             if r["source_checksum"]}
    imported = 0
    failed = []
    for cand in result["candidates"]:
        cand["already_imported"] = cand["checksum"] in known
        if do_import and not cand["already_imported"]:
            try:
                service.wrap(conn, title=cand["title"], content=cand["content"],
                             source_format=cand["source_format"],
                             language=cand["language"],
                             origin_kind=cand["origin_kind"],
                             origin_id=cand["origin_id"],
                             origin_path=cand["origin_path"],
                             authored_at=cand["authored_at"])
            except Exception as e:
                # One unrenderable document must not abort the batch. Record it
                # and carry on — a partial import that says what it dropped
                # beats an import that stops at document 29 in silence.
                cand["import_error"] = f"{type(e).__name__}: {e}"
                failed.append({"title": cand["title"],
                               "file_path": cand["file_path"],
                               "error": cand["import_error"][:300]})
                continue
            cand["already_imported"] = True
            known.add(cand["checksum"])
            imported += 1
    # `content` is bulky; the caller wants metadata, not every document body.
    for cand in result["candidates"]:
        cand.pop("content", None)
    result["imported"] = imported
    result["failed"] = failed
    return result
