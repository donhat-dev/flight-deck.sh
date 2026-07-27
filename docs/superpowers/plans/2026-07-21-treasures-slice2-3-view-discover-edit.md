# Treasures Slice 2 + 3 — dashboard view, discovery, editing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the Treasures library visible and usable in FlightDeck — list, sandboxed preview, provenance link. Then harvest the scattered drafts already sitting in `~/.claude/projects`, and allow editing the markdown source so a new version re-renders.

**Architecture:** Slice 1 already owns wrap/store/index (`backend/flightdeck/treasures/`). This plan adds one HTTP router over the same service layer, one discovery scanner, one frontend view, and one write endpoint. Markdown stays the source of truth: editing writes markdown and re-renders through the existing pandoc pipeline, producing a new version rather than mutating HTML.

**Tech Stack:** FastAPI router (existing patterns in `backend/flightdeck/routers/`), React + Tailwind view (existing patterns in `frontend/src/systems/`), stdlib JSONL scanning, optional Milkdown npm packages.

## Global Constraints

- Everything from slice 1's plan still applies: package at `backend/flightdeck/treasures/`, run/test from `backend/` with `../.venv/bin/python`, the venv has **no pip** (`uv pip install --python .venv/bin/python …`), portable SQL (`?`, `ON CONFLICT`, TEXT ISO timestamps), tests run on SQLite while production runs on PostgreSQL.
- **Baseline to preserve: 122 passed, 1 skipped.** Any new test count is on top of that.
- **The rendered artifact is untrusted content.** It must only ever be displayed inside an iframe carrying a bare `sandbox=""` attribute, which forces an opaque origin and blocks scripts. Never inject artifact HTML into the dashboard DOM.
- **No silent caps.** The discovery scanner bounds its work (file count, age, size); every bound must be reported in its response, not applied invisibly.
- **Published artifacts are read-only from the dashboard** — claude.ai has no update API. The UI must say so rather than implying a sync.
- Frontend styling: zinc/emerald ramps + `fd-shell`/`fd-core` + `--fd-hair-2`, so Night and Day both read correctly. Match `frontend/src/systems/ManualsView.jsx` for atoms (Eyebrow, badges, chips, skeleton).
- All code comments and docs in English.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/flightdeck/routers/treasures.py` | HTTP surface: list, get, raw artifact bytes, source PUT, discover |
| `backend/flightdeck/server.py` (modify) | register the router + `treasures_store.init(write_conn)` at startup |
| `backend/flightdeck/treasures/discover.py` | scan `~/.claude/projects/**/*.jsonl` for artifact bodies the agent wrote to files |
| `backend/flightdeck/treasures/mcp_server.py` (modify) | add the `treasure_discover` tool |
| `frontend/src/treasures/TreasuresView.jsx` | the library view: list, filters, detail with preview/source/edit |
| `frontend/src/App.jsx` (modify) | nav entry + view wiring (+ pass `onOpenSession` for provenance links) |
| `backend/tests/test_treasures_routes.py` | router tests via FastAPI TestClient |
| `backend/tests/test_treasures_discover.py` | scanner tests against a synthetic JSONL tree |

---

## Task 1: HTTP router — list, get, raw

**Files:**
- Create: `backend/flightdeck/routers/treasures.py`
- Modify: `backend/flightdeck/server.py`
- Test: `backend/tests/test_treasures_routes.py`

**Interfaces:**
- Consumes: `flightdeck.treasures.service` (`list_rows`, `get`), `flightdeck.treasures.store` (`init`), `flightdeck.db` (`open_read`, `open_write`).
- Produces: `GET /api/treasures`, `GET /api/treasures/{ident}`, `GET /api/treasures/{ident}/raw`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_routes.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.routers import treasures as treasures_router
from flightdeck.treasures import service, store


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    cfg = {"db_path": str(tmp_path / "t.db"), "database_url": None}
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    # two artifacts: one EN draft, one VN draft
    service.wrap(conn, title="Helpdesk report", content="# Helpdesk\n\nBody.\n",
                 origin_kind="claude_session", origin_id="sess-en")
    service.wrap(conn, title="Báo cáo", content="# Báo cáo\n\nNội dung.\n",
                 language="vi", origin_kind="claude_session", origin_id="sess-vi")
    app = FastAPI()
    app.state.cfg = cfg
    app.include_router(treasures_router.router)
    return TestClient(app)


def test_list_returns_rows_newest_first(client):
    r = client.get("/api/treasures")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert {t["language"] for t in body["treasures"]} == {"en", "vi"}
    # every row carries the fields the UI renders
    row = body["treasures"][0]
    for key in ("id", "title", "slug", "kind", "language", "status", "version",
                "render_bytes", "origin_id", "updated_at"):
        assert key in row


def test_list_filters(client):
    assert client.get("/api/treasures?language=vi").json()["count"] == 1
    assert client.get("/api/treasures?status=published").json()["count"] == 0
    assert client.get("/api/treasures?query=helpdesk").json()["count"] == 1
    assert client.get("/api/treasures?origin_id=sess-vi").json()["count"] == 1


def test_get_one_with_and_without_source(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    plain = client.get(f"/api/treasures/{ident}").json()
    assert plain["title"] == "Báo cáo"
    assert "source" not in plain
    full = client.get(f"/api/treasures/{ident}?include_source=true").json()
    assert full["source"].startswith("# Báo cáo")
    assert client.get("/api/treasures/nope").status_code == 404


def test_raw_serves_the_artifact_html(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    r = client.get(f"/api/treasures/{ident}/raw")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.lstrip().startswith("<!doctype html>")
    assert "data:font/woff2;base64," in r.text     # self-contained
    assert client.get("/api/treasures/nope/raw").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_routes.py -q`
Expected: FAIL — `ImportError: cannot import name 'treasures' from 'flightdeck.routers'`

- [ ] **Step 3: Write the router**

Create `backend/flightdeck/routers/treasures.py`:

```python
"""Treasures HTTP surface for the dashboard.

Read paths only in this task: list, one row, and the rendered artifact bytes.
The artifact is untrusted content, so `/raw` exists to be loaded into an iframe
carrying a bare `sandbox=""` attribute (opaque origin, no scripts) — never to be
injected into the dashboard DOM.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from flightdeck import db
from flightdeck.treasures import filestore, service

router = APIRouter(tags=["treasures"])


def _db_path(request: Request) -> str:
    return request.app.state.cfg["db_path"]


@router.get("/api/treasures")
def list_treasures(request: Request, status: str | None = None,
                   language: str | None = None, kind: str | None = None,
                   origin_id: str | None = None, query: str | None = None,
                   limit: int = 200, offset: int = 0):
    with db.read_conn(_db_path(request)) as conn:
        rows = service.list_rows(conn, status=status, language=language,
                                 kind=kind, origin_id=origin_id, query=query,
                                 limit=limit, offset=offset)
    return {"treasures": rows, "count": len(rows)}


@router.get("/api/treasures/{ident}")
def get_treasure(request: Request, ident: str, include_source: bool = False):
    with db.read_conn(_db_path(request)) as conn:
        row = service.get(conn, ident, include_source=include_source)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    return row


@router.get("/api/treasures/{ident}/raw")
def raw_treasure(request: Request, ident: str):
    """The rendered artifact, for a sandboxed iframe."""
    with db.read_conn(_db_path(request)) as conn:
        row = service.get(conn, ident)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    path = Path(row["artifact_path"]).resolve()
    # The path comes from the index, not the request, but confirm it still sits
    # inside the filestore before reading anything off disk.
    root = filestore.root().resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file missing")
    return Response(content=path.read_text(encoding="utf-8"),
                    media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})
```

- [ ] **Step 4: Register the router and the table**

In `backend/flightdeck/server.py`:

1. Extend the routers import to include `treasures`:
```python
from flightdeck.routers import (charts, core, diff, hub, missions, sessions,
                               stream, treasures)
```
2. Import the store beside the hub credentials import:
```python
from flightdeck.treasures import store as treasures_store
```
3. Beside `credentials.init(write_conn)`, add:
```python
    # Treasures index: created here too, so the dashboard works even if the
    # Treasures MCP has never been run in this environment.
    treasures_store.init(write_conn)
```
4. Beside the other `include_router` calls, add:
```python
    app.include_router(treasures.router)
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_routes.py -q`
Expected: PASS (4 passed)

Run: `cd backend && ../.venv/bin/python -m pytest tests -q`
Expected: 126 passed, 1 skipped

- [ ] **Step 6: Verify against the running service**

The durable service is systemd, serving `:8010` from PostgreSQL. Restart it so the
new router loads, then check the endpoints answer (an empty library is a valid
answer — `count: 0`):

```bash
systemctl --user restart flightdeck && sleep 5
curl -s http://127.0.0.1:8010/api/treasures | head -c 200; echo
```
Expected: `{"treasures":[],"count":0}` (or rows, if any exist).

---

## Task 2: Discovery — harvest the drafts already on disk

**Files:**
- Create: `backend/flightdeck/treasures/discover.py`
- Modify: `backend/flightdeck/routers/treasures.py` (add the discover endpoint)
- Modify: `backend/flightdeck/treasures/mcp_server.py` (add the `treasure_discover` tool)
- Test: `backend/tests/test_treasures_discover.py`

**What counts as a discoverable artifact — the conservative rule.** A candidate is
a `Write` tool call in an assistant turn whose `input.file_path` ends in `.md` or
`.html` and whose `input.content` is at least `MIN_BYTES` long. That is precisely
"a document the agent explicitly wrote to a file", which avoids the false
positives that scraping every fenced code block would produce. Read the JSONL
directly rather than through `transcript.py`, because that module truncates long
tool inputs and would cut document bodies short.

**Interfaces:**
- Consumes: `flightdeck.treasures.store` (`list_rows` for the already-imported set), `service.wrap` (for `import`), `filestore.checksum`.
- Produces:
  - `discover.scan(projects_dir, *, max_files=400, max_age_days=None, min_bytes=400) -> dict` with keys `candidates`, `scanned`, `skipped`, `bounds`
  - `discover.run(conn, projects_dir, *, do_import=False, **bounds) -> dict` (adds `imported` when importing)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_discover.py`:

```python
import json

from flightdeck import db
from flightdeck.treasures import discover, filestore, store

MD_BODY = "# Báo cáo Helpdesk\n\n" + ("Nội dung tiếng Việt đầy đủ. " * 30)
HTML_BODY = "<h1>Report</h1>" + ("<p>Long enough body.</p>" * 30)


def _jsonl(path, session_id, blocks):
    """One assistant line per block set, in the shape Claude Code writes."""
    with open(path, "w", encoding="utf-8") as fh:
        for content in blocks:
            fh.write(json.dumps({
                "type": "assistant", "uuid": f"u-{session_id}",
                "sessionId": session_id, "timestamp": "2026-07-20T10:00:00Z",
                "cwd": "/home/x/proj",
                "message": {"content": content},
            }) + "\n")


def _tree(tmp_path):
    proj = tmp_path / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    _jsonl(proj / "sess-a.jsonl", "sess-a", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/docs/bao-cao.md", "content": MD_BODY}},
    ], [
        {"type": "text", "text": "some prose, not a document"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/notes.txt", "content": MD_BODY}},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/tiny.md", "content": "# hi"}},
    ]])
    _jsonl(proj / "sess-b.jsonl", "sess-b", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/home/x/proj/report.html", "content": HTML_BODY}},
    ]])
    return tmp_path / "projects"


def test_scan_finds_only_document_writes(tmp_path):
    out = discover.scan(str(_tree(tmp_path)))
    paths = sorted(c["file_path"] for c in out["candidates"])
    assert paths == ["/home/x/proj/docs/bao-cao.md", "/home/x/proj/report.html"]
    assert out["scanned"] == 2                      # two jsonl files
    assert out["skipped"]["not_a_document"] >= 2    # notes.txt + Bash
    assert out["skipped"]["too_small"] == 1         # tiny.md
    assert out["bounds"]["min_bytes"] > 0           # bounds are reported


def test_candidate_metadata_is_useful(tmp_path):
    out = discover.scan(str(_tree(tmp_path)))
    md = next(c for c in out["candidates"] if c["file_path"].endswith(".md"))
    assert md["title"] == "Báo cáo Helpdesk"        # from the first heading
    assert md["language"] == "vi"                    # Vietnamese glyphs present
    assert md["source_format"] == "markdown"
    assert md["origin_id"] == "sess-a"               # closes the origin_id gap
    assert md["origin_path"].endswith("sess-a.jsonl")
    assert md["authored_at"] == "2026-07-20T10:00:00Z"
    assert md["checksum"] == filestore.checksum(MD_BODY)
    assert md["bytes"] == len(MD_BODY.encode("utf-8"))

    html = next(c for c in out["candidates"] if c["file_path"].endswith(".html"))
    assert html["source_format"] == "html"
    assert html["language"] == "en"


def test_run_marks_already_imported_and_can_import(monkeypatch, tmp_path):
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    projects = str(_tree(tmp_path))

    first = discover.run(conn, projects)
    assert first["imported"] == 0
    assert all(c["already_imported"] is False for c in first["candidates"])

    imported = discover.run(conn, projects, do_import=True)
    assert imported["imported"] == 2
    rows = store.list_rows(conn)
    assert len(rows) == 2
    assert {r["origin_kind"] for r in rows} == {"claude_session"}
    assert {r["origin_id"] for r in rows} == {"sess-a", "sess-b"}

    # idempotent: a second import adds nothing, and candidates are flagged
    again = discover.run(conn, projects, do_import=True)
    assert again["imported"] == 0
    assert all(c["already_imported"] for c in again["candidates"])
    assert len(store.list_rows(conn)) == 2


def test_scan_skips_the_filestore_itself(monkeypatch, tmp_path):
    """Discovering our own wrapped output would loop artifacts back in."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    proj = tmp_path / "projects" / "-p"
    proj.mkdir(parents=True)
    inside = str(filestore.root() / "x-123" / "v1" / "artifact.html")
    _jsonl(proj / "s.jsonl", "s", [[
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": inside, "content": HTML_BODY}}]])
    out = discover.scan(str(tmp_path / "projects"))
    assert out["candidates"] == []
    assert out["skipped"]["in_filestore"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_discover.py -q`
Expected: FAIL — `ImportError: cannot import name 'discover'`

- [ ] **Step 3: Write the scanner**

Create `backend/flightdeck/treasures/discover.py`:

```python
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
from pathlib import Path

from flightdeck.treasures import filestore, service, store

DOC_SUFFIXES = (".md", ".html", ".htm")
MIN_BYTES = 400
MAX_FILES = 400

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


def _candidate(block_input, obj, jsonl_path, store_root):
    path = block_input.get("file_path") or ""
    content = block_input.get("content")
    if not path or not isinstance(content, str):
        return None, "not_a_document"
    if not path.lower().endswith(DOC_SUFFIXES):
        return None, "not_a_document"
    try:
        if store_root in Path(path).resolve().parents:
            return None, "in_filestore"
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
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use" or block.get("name") != "Write":
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


def run(conn, projects_dir: str, *, do_import: bool = False, **bounds) -> dict:
    """Scan, mark which candidates the store already holds (by source checksum),
    and optionally wrap+index the new ones."""
    result = scan(projects_dir, **bounds)
    known = {r["source_checksum"] for r in store.list_rows(conn, limit=100000)
             if r["source_checksum"]}
    imported = 0
    for cand in result["candidates"]:
        cand["already_imported"] = cand["checksum"] in known
        if do_import and not cand["already_imported"]:
            service.wrap(conn, title=cand["title"], content=cand["content"],
                         source_format=cand["source_format"],
                         language=cand["language"],
                         origin_kind=cand["origin_kind"],
                         origin_id=cand["origin_id"],
                         origin_path=cand["origin_path"],
                         authored_at=cand["authored_at"])
            cand["already_imported"] = True
            known.add(cand["checksum"])
            imported += 1
    # `content` is bulky; the caller wants metadata, not every document body.
    for cand in result["candidates"]:
        cand.pop("content", None)
    result["imported"] = imported
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_discover.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Expose discovery on both surfaces**

Append to `backend/flightdeck/routers/treasures.py`:

```python
@router.post("/api/treasures/discover")
def discover_treasures(request: Request, do_import: bool = False,
                       max_files: int = 400, max_age_days: int | None = None):
    """Scan the transcript tree for drafts. POST because `do_import=true`
    writes; the default is a dry run that only reports what it found."""
    from flightdeck.treasures import discover
    cfg = request.app.state.cfg
    conn = db.open_write(cfg["db_path"]) if do_import else db.open_read(cfg["db_path"])
    try:
        return discover.run(conn, cfg["projects_dir"], do_import=do_import,
                            max_files=max_files, max_age_days=max_age_days)
    finally:
        if not do_import:
            conn.close()
```

In `backend/flightdeck/treasures/mcp_server.py`, add the tool function beside
the others:

```python
def t_discover(do_import=False, max_files=400, max_age_days=None):
    from flightdeck.treasures import discover
    return discover.run(_conn(), _state["cfg"]["projects_dir"],
                        do_import=do_import, max_files=max_files,
                        max_age_days=max_age_days)
```

and register it in `TOOLS`:

```python
    "treasure_discover": (
        t_discover,
        "Scan ~/.claude/projects for markdown/HTML documents the agent wrote to "
        "files but that are not in the library yet. Dry run by default; pass "
        "do_import=true to wrap and index the new ones. Reports its bounds and "
        "what it skipped.",
        {"do_import": {"type": "boolean"},
         "max_files": {"type": "integer"},
         "max_age_days": {"type": "integer"}},
        []),
```

Update `test_treasures_mcp.py::test_initialize_and_tools_list` so the expected
tool set becomes `{"treasure_wrap", "treasure_get", "treasure_list", "treasure_discover"}`.

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests -q`
Expected: 131 passed, 1 skipped

- [ ] **Step 7: Dry-run discovery against the REAL transcript tree**

Step 7 is the honest test of the conservative rule. The scan must find real
documents without flooding the result with false positives:

```bash
cd /home/nathando/Documents/Projects/flight-deck.sh
set -a; . .env; set +a
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"treasure_discover","arguments":{"max_files":120}}}' \
  | .venv/bin/python backend/flightdeck/treasures/mcp_server.py 2>/dev/null \
  | python3 -c "
import json,sys
d=json.loads(json.loads(sys.stdin.readline())['result']['content'][0]['text'])
print('scanned files:', d['scanned'], '| candidates:', len(d['candidates']))
print('skipped:', d['skipped'])
print('bounds:', d['bounds'])
for c in d['candidates'][:10]:
    print(f\"  [{c['language']}] {c['title'][:48]:50} {c['bytes']:7}B  {c['file_path'][-52:]}\")
"
```

Report the counts and eyeball the titles: they should look like real documents
(reports, specs, notes), not code files. **Do not import yet** — importing is the
user's call once the candidate list looks right.

---

## Task 3: Frontend — the Treasures view

**Files:**
- Create: `frontend/src/treasures/TreasuresView.jsx`
- Modify: `frontend/src/App.jsx`
- Verify: browser

**Interfaces:**
- Consumes: `GET /api/treasures`, `GET /api/treasures/{id}?include_source=true`, `GET /api/treasures/{id}/raw`, `POST /api/treasures/discover`.
- Produces: default-exported `TreasuresView` taking one prop, `onOpenSession(sessionId)`.

- [ ] **Step 1: Build the view**

Create `frontend/src/treasures/TreasuresView.jsx`. Requirements, all of which
must be visible in the final screenshot:

1. **Summary strip** (`fd-shell` > `fd-core`, the `ManualsView` StatCell pattern): total artifacts, drafts, published, and a language split (`en` / `vi`).
2. **Controls row**: filter chips (All / draft / published / en / vi), a search box bound to the `query` param, a **Refresh** button, and a **Discover drafts** button that POSTs `discover` as a **dry run** and shows `candidates` / `already_imported` counts plus the reported bounds. Importing is a second, explicit click labelled **Import N new** — never automatic.
3. **List** (one row per artifact): title, kind, status badge (draft = zinc, published = emerald, archived = dim), language badge (`vi` = amber to stand out), version, size (KB), relative `updated_at`, and — when `origin_id` is set — a **provenance link** rendered as a button calling `onOpenSession(origin_id)`; artifacts without an origin show a muted "no source".
4. **Detail pane** when a row is selected, with two tabs:
   - **Preview**: `<iframe src={`/api/treasures/${id}/raw`} sandbox="" className="h-[70vh] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-white" title={row.title} />`. The bare `sandbox=""` is mandatory. Include a one-line note that the artifact renders in an isolated sandbox.
   - **Source**: the markdown/HTML fetched with `include_source=true`, in a `<pre>` with `overflow-x: auto`.
   - When `status === "published"`, show `published_url` as a link and a muted note that the published copy cannot be updated from here (claude.ai has no update API).
5. Loading skeleton and an error panel with a retry, matching `ManualsView`.
6. Poll `GET /api/treasures` every 15s while mounted (clear the interval on unmount) and label it "auto 15s" beside Refresh, so the freshness claim is accurate.

- [ ] **Step 2: Wire it into App.jsx**

Three edits:

1. Import beside the other view imports:
```jsx
import TreasuresView from "./treasures/TreasuresView.jsx";
```
2. Add to `NAV` (top-level — Treasures is a content library, not a system board), after the `hub` entry:
```jsx
  { k: "treasures", label: "Treasures", icon: "◈" },
```
3. Add a branch beside the other `view === …` branches (before the final `: (`):
```jsx
      ) : view === "treasures" ? (
        <Shell variant="contained" header={<Header title="Treasures" subtitle="Artifact library — wrap, preview, provenance" />}>
          <TreasuresView onOpenSession={(id) => { setView("sessions"); goSession(id); }} />
        </Shell>
```

- [ ] **Step 3: Build and verify in the browser**

```bash
cd /home/nathando/Documents/Projects/flight-deck.sh
npm --prefix frontend run build
systemctl --user restart flightdeck && sleep 5
```

Then, so the view has something to show, wrap two artifacts (one EN, one VN)
through the MCP:

```bash
set -a; . .env; set +a
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"treasure_wrap","arguments":{"title":"Helpdesk OSS alternatives","content":"# Helpdesk OSS alternatives\n\n## Candidates\n\n| Tool | License |\n|---|---|\n| Zammad | AGPL |\n| FreeScout | AGPL |\n\nBody text for the report.\n","kind":"report"}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"treasure_wrap","arguments":{"title":"Báo cáo đánh giá Helpdesk","content":"# Báo cáo đánh giá Helpdesk\n\nNội dung tiếng Việt: hiệu quả, cộng đồng, người dùng, đặc biệt.\n","language":"vi","kind":"report","origin_kind":"claude_session","origin_id":"560cf395-75cc-4ef7-bfb2-b1d56dd78a58"}}}' \
 | .venv/bin/python backend/flightdeck/treasures/mcp_server.py >/dev/null 2>&1
curl -s http://127.0.0.1:8010/api/treasures | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['count'],[t['slug'] for t in d['treasures']])"
```

Open `http://127.0.0.1:8010/` → **Treasures**, then confirm by screenshot in both
themes:
- both artifacts listed with correct badges (one `vi`, one `en`);
- selecting the VN one and opening **Preview** renders the artifact **with
  Vietnamese diacritics intact** (proof the embedded subset font is being used);
- **Source** tab shows the markdown;
- the provenance button on the VN row opens the Logbook session.

---

## Task 4: Editing the source (tier 1)

**Files:**
- Modify: `backend/flightdeck/routers/treasures.py` (add the PUT)
- Modify: `backend/tests/test_treasures_routes.py` (add the PUT test)
- Modify: `frontend/src/treasures/TreasuresView.jsx` (add the Edit tab)
- Modify: `frontend/package.json` (only if Milkdown is adopted)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_treasures_routes.py`:

```python
def test_put_source_creates_a_new_version(client):
    ident = client.get("/api/treasures?language=vi").json()["treasures"][0]["id"]
    before = client.get(f"/api/treasures/{ident}").json()
    assert before["version"] == 1

    r = client.put(f"/api/treasures/{ident}/source",
                   json={"content": "# Báo cáo v2\n\nĐã sửa nội dung.\n"})
    assert r.status_code == 200
    after = r.json()
    assert after["version"] == 2
    assert after["id"] == ident
    assert after["source_checksum"] != before["source_checksum"]

    # the new source is what /raw and include_source now serve
    assert "Báo cáo v2" in client.get(f"/api/treasures/{ident}/raw").text
    assert client.get(f"/api/treasures/{ident}?include_source=true"
                      ).json()["source"].startswith("# Báo cáo v2")
    assert client.put("/api/treasures/nope/source",
                      json={"content": "x" * 10}).status_code == 404
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_routes.py -q`
Expected: FAIL — 405 Method Not Allowed (the route does not exist yet)

- [ ] **Step 3: Add the endpoint**

Append to `backend/flightdeck/routers/treasures.py` (and add `from pydantic import
BaseModel` to the imports):

```python
class SourceIn(BaseModel):
    content: str


@router.put("/api/treasures/{ident}/source")
def put_source(request: Request, ident: str, body: SourceIn):
    """Save edited markdown/HTML and re-render, producing a NEW version.

    Markdown stays the source of truth: this writes the source and re-runs the
    pandoc pipeline, so the artifact can never drift from its source.
    """
    cfg = request.app.state.cfg
    conn = db.open_write(cfg["db_path"])
    row = service.get(conn, ident)
    if row is None:
        raise HTTPException(status_code=404, detail="treasure not found")
    return service.wrap(conn, title=row["title"], content=body.content,
                        source_format=row["source_format"], kind=row["kind"],
                        language=row["language"], artifact_id=row["id"])
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_routes.py -q`
Expected: PASS (5 passed). Then the whole suite: 132 passed, 1 skipped.

- [ ] **Step 5: Add the Edit tab — plain editor first, Milkdown as an upgrade**

The deliverable is *editing markdown and seeing a new version render*, not a
specific editor widget. Build it in that order so it is never blocked:

1. **Guaranteed path (do this first, commit-worthy on its own):** a third tab
   **Edit** containing a monospace `<textarea>` pre-filled from
   `include_source=true`, a **Save** button that PUTs the content, and on success
   a version-bump indicator plus a forced reload of the preview iframe (change
   its `key` so the browser refetches `/raw`). Disable the tab entirely when
   `status === "published"`, with the read-only note.
2. **Upgrade (only if it lands cleanly):** replace the textarea with **Milkdown**
   (`@milkdown/core`, `@milkdown/react`, `@milkdown/preset-commonmark`,
   `@milkdown/preset-gfm`, `@milkdown/theme-nord` or headless styling), which
   edits WYSIWYG while still emitting markdown. Install with
   `npm --prefix frontend install <pkgs>` and keep the same PUT contract. **If it
   does not integrate cleanly within a bounded attempt, keep the textarea** and
   say so — do not leave a half-wired editor. Note in the report which path
   shipped.

- [ ] **Step 6: Verify editing end to end in the browser**

```bash
npm --prefix frontend run build && systemctl --user restart flightdeck && sleep 5
```

In the UI: select the VN artifact → **Edit** → append a line containing
Vietnamese text → **Save** → confirm the version indicator goes to 2, the
**Preview** re-renders with the new text and diacritics intact, and
`ls ~/.flightdeck/treasures/*/` shows both `v1/` and `v2/`. Screenshot it.

---

## Self-Review

**1. Spec coverage.** Slice 2 (`docs/treasures-design.md` §7): list with badges →
Task 3; provenance link into the Logbook → Task 3; sandboxed-iframe preview →
Task 3 (bare `sandbox=""`); source pane → Task 3; published-is-read-only notice →
Tasks 3-4. Slice 3: `treasure_discover` → Task 2 (both MCP tool and HTTP
endpoint); tier-1 editing (§8) → Task 4. The design's "realtime via the watchdog"
is **downgraded to a 15s poll plus a Refresh button, labelled as such** — wiring
the filestore into the existing watcher is deferred rather than silently claimed.
The `origin_id` open question (§10) is now answered for discovered artifacts: the
scanner reads the session id straight from the JSONL line.

**2. Placeholder scan.** Every backend step carries runnable code and real
assertions. Task 3 is specified as numbered requirements rather than a full JSX
listing — deliberate, because the view must match the existing `ManualsView`
atoms which the implementer will read; every element that must appear (including
the exact iframe attributes and the exact `App.jsx` edits) is named concretely.
Task 4 Step 5 names both the guaranteed path and the upgrade, with the decision
rule for falling back.

**3. Type consistency.** `discover.scan` returns `candidates`/`scanned`/`skipped`/
`bounds`; `discover.run` adds `imported` and `already_imported` per candidate —
matching every test and both callers. The router uses `service.get`/`list_rows`/
`wrap` with the signatures slice 1 produced, and `service.wrap(artifact_id=…)` is
the same version-bumping path the MCP uses. `filestore.root()`/`checksum()` are
used as defined. The frontend prop is `onOpenSession`, matching `RouteLoom`'s
existing convention.
