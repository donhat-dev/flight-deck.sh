# Treasures Slice 1 — wrap + store + index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An MCP server that turns agent-written markdown (or an HTML fragment) into a publish-ready self-contained HTML artifact, stores it in a local filestore, and indexes it in FlightDeck's database — exposing `treasure_wrap`, `treasure_get`, `treasure_list`.

**Architecture:** Four small modules under `backend/flightdeck/treasures/`, layered so each is testable alone: `store.py` (DB index) → `filestore.py` (files on disk) → `render.py` (pandoc wrap) → `service.py` (orchestration) → `mcp_server.py` (stdio JSON-RPC surface). Markdown stays the source of truth; `artifact.html` is derived and re-derivable. The DB is an index over real files: every artifact dir carries a `meta.json` sidecar so the index can be rebuilt from disk.

**Tech Stack:** Python 3.12 stdlib + FastAPI-era deps already in `backend/requirements.txt` (no new runtime deps), `psycopg`/SQLite through the existing `flightdeck.db` seam, and the **pandoc** binary (pinned static download, no root).

## Global Constraints

- **Package location:** `backend/flightdeck/treasures/` — a subpackage beside the existing `hub/`, `systems/`, `agui/`, `routers/`. Imports are `from flightdeck import db`, `from flightdeck.treasures import store`.
- **Run/test cwd is `backend/`**; the venv is at the repo root, so commands are `cd backend && ../.venv/bin/python -m pytest tests -q`.
- **The venv has no `pip`** (created with `uv venv`). Install packages with `uv pip install --python .venv/bin/python <pkg>` from the repo root.
- **Dual engine, one DDL.** Tests run on SQLite (no `database_url`); production runs on PostgreSQL. All SQL must work on both: `?` placeholders (the PG wrapper in `db.py` translates to `%s`), `INSERT … ON CONFLICT(col) DO UPDATE SET …`, no `PRAGMA`, no `now()`, no `executescript`.
- **Timestamps are TEXT, ISO-8601 UTC, set in Python** — matching the existing `messages.ts` / `tool_calls.ts` columns. This is a deliberate amendment to the spec's `timestamptz`: it keeps one portable DDL and one code path for both engines. Format: `datetime.now(timezone.utc).isoformat(timespec="seconds")`.
- **Filestore root:** `TREASURES_STORE` env, default `~/.flightdeck/treasures`. Never inside the repo (artifacts are data, some internal).
- **pandoc:** resolve in this order — `TREASURES_PANDOC` env, `~/.flightdeck/bin/pandoc`, then `shutil.which("pandoc")`. Pinned version **3.10.1**.
- **Artifact fonts are Space Grotesk + Playfair Display with the `vietnamese`+`latin` subsets** (per `ARTIFACT_STYLE.md`). Measured empirically during planning: the app's Outfit/IBM-Plex `latin-ext` subsets are **missing** ế ộ ữ ợ ạ (U+1EA0–1EF9), so they must not be used for artifacts.
- **Self-contained output is the contract:** zero non-`data:` external references in the rendered HTML, except the `http://www.w3.org/2000/svg` XML *namespace* inside the favicon data URI (a namespace identifier, not a fetch).
- All code comments and docs in English.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/flightdeck/treasures/__init__.py` | empty package marker |
| `backend/flightdeck/treasures/store.py` | the `treasures` table: portable DDL + `init` / `upsert` / `get` / `list_rows` |
| `backend/flightdeck/treasures/filestore.py` | artifact dir layout, version dirs, `meta.json` sidecar, checksums, slugs |
| `backend/flightdeck/treasures/render.py` | pandoc invocation, template/CSS wiring, warnings (external fetches, size) |
| `backend/flightdeck/treasures/service.py` | the `wrap` use case: render → write files → index; plus read-throughs |
| `backend/flightdeck/treasures/mcp_server.py` | stdio JSON-RPC server exposing 3 tools; loads `.env` + config itself |
| `backend/flightdeck/treasures/templates/artifact.html` | pandoc template = the artifact shell (title, emoji favicon, theme, `$body$`) |
| `backend/flightdeck/treasures/templates/tokens.css` | design tokens + `@font-face` pointing at the local woff2 files |
| `backend/flightdeck/treasures/templates/fonts/*.woff2` | Vietnamese-capable subsets (committed; ~150 KB total) |
| `backend/tests/test_treasures_store.py` | store CRUD on SQLite |
| `backend/tests/test_treasures_filestore.py` | dir layout, versions, sidecar, slugs |
| `backend/tests/test_treasures_render.py` | pandoc output is self-contained; VN glyph coverage |
| `backend/tests/test_treasures_service.py` | wrap end-to-end (files + index row agree) |
| `backend/tests/test_treasures_mcp.py` | JSON-RPC handshake + the 3 tools |
| `scripts/fetch-pandoc.sh` | pinned static pandoc download into `~/.flightdeck/bin` |
| `Makefile` | `pandoc` + `fonts` targets |
| `.mcp.json` (repo root) | registers the `treasures` MCP server |

---

## Task 1: Store — the `treasures` table

**Files:**
- Create: `backend/flightdeck/treasures/__init__.py`
- Create: `backend/flightdeck/treasures/store.py`
- Test: `backend/tests/test_treasures_store.py`

**Interfaces:**
- Consumes: `flightdeck.db` (`connect`, `open_read`) — already exists.
- Produces:
  - `store.COLUMNS: tuple[str, ...]`
  - `store.init(conn) -> None`
  - `store.upsert(conn, row: dict) -> dict` (returns the stored row)
  - `store.get(conn, ident: str) -> dict | None` (matches `id` or `slug`)
  - `store.list_rows(conn, *, status=None, language=None, kind=None, origin_id=None, query=None, limit=100, offset=0) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_store.py`:

```python
from flightdeck import db
from flightdeck.treasures import store


def _row(**over):
    row = {
        "id": "abc123def456",
        "title": "Helpdesk OSS alternatives",
        "slug": "helpdesk-oss-alternatives",
        "dir_path": "/tmp/store/helpdesk-oss-alternatives-abc123def456",
        "kind": "report",
        "language": "en",
        "status": "draft",
        "version": 1,
        "source_format": "markdown",
        "source_checksum": "s" * 64,
        "render_checksum": "r" * 64,
        "render_bytes": 12345,
        "origin_kind": "claude_session",
        "origin_id": "560cf395-75cc-4ef7-bfb2-b1d56dd78a58",
        "origin_path": None,
        "published_url": None,
        "duplicate_of": None,
        "authored_at": "2026-07-21T10:00:00+00:00",
        "ingested_at": "2026-07-21T10:00:05+00:00",
        "updated_at": "2026-07-21T10:00:05+00:00",
    }
    row.update(over)
    return row


def test_upsert_then_get_by_id_and_slug(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    by_id = store.get(conn, "abc123def456")
    by_slug = store.get(conn, "helpdesk-oss-alternatives")
    assert by_id["title"] == "Helpdesk OSS alternatives"
    assert by_slug["id"] == "abc123def456"
    assert by_id["version"] == 1
    assert store.get(conn, "nope") is None


def test_upsert_is_idempotent_and_updates(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    store.upsert(conn, _row(version=2, status="published",
                            published_url="https://claude.ai/public/artifacts/x"))
    rows = store.list_rows(conn)
    assert len(rows) == 1                      # same id -> one row
    assert rows[0]["version"] == 2
    assert rows[0]["status"] == "published"


def test_list_filters_and_search(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.upsert(conn, _row())
    store.upsert(conn, _row(id="vn0000000001", slug="bao-cao-helpdesk",
                            title="Bao cao helpdesk", language="vi",
                            dir_path="/tmp/store/bao-cao-helpdesk-vn0000000001"))
    assert len(store.list_rows(conn, language="vi")) == 1
    assert len(store.list_rows(conn, status="draft")) == 2
    assert len(store.list_rows(conn, origin_id="560cf395-75cc-4ef7-bfb2-b1d56dd78a58")) == 2
    assert len(store.list_rows(conn, kind="report")) == 2
    assert store.list_rows(conn, query="bao cao")[0]["language"] == "vi"
    assert len(store.list_rows(conn, limit=1)) == 1


def test_init_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    store.init(conn)                            # must not raise
    assert store.list_rows(conn) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flightdeck.treasures'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/treasures/__init__.py` (empty file).

Create `backend/flightdeck/treasures/store.py`:

```python
"""The `treasures` index: one row per artifact, pointing at files on disk.

The filestore is the system of record for content (each artifact dir carries a
meta.json sidecar); this table is a rebuildable index that makes the library
queryable and — because `origin_id` holds the Claude session id — joinable
against FlightDeck's existing messages / tool_calls / session_meta rows.

Portable SQL only: `?` placeholders, ON CONFLICT upserts, TEXT ISO timestamps.
The same DDL runs on SQLite (tests) and PostgreSQL (production), where the table
is LOGGED because artifacts are user content, not derived ingest data.
"""

COLUMNS = (
    "id", "title", "slug", "dir_path", "kind", "language", "status", "version",
    "source_format", "source_checksum", "render_checksum", "render_bytes",
    "origin_kind", "origin_id", "origin_path", "published_url", "duplicate_of",
    "authored_at", "ingested_at", "updated_at",
)

_DDL = """
CREATE TABLE IF NOT EXISTS treasures (
  id              text PRIMARY KEY,
  title           text NOT NULL,
  slug            text NOT NULL,
  dir_path        text NOT NULL,
  kind            text NOT NULL,
  language        text NOT NULL,
  status          text NOT NULL,
  version         integer NOT NULL,
  source_format   text NOT NULL,
  source_checksum text,
  render_checksum text,
  render_bytes    bigint,
  origin_kind     text,
  origin_id       text,
  origin_path     text,
  published_url   text,
  duplicate_of    text,
  authored_at     text,
  ingested_at     text NOT NULL,
  updated_at      text NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_treasures_origin ON treasures(origin_id)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_status ON treasures(status)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_slug ON treasures(slug)",
    "CREATE INDEX IF NOT EXISTS idx_treasures_srcsum ON treasures(source_checksum)",
)


def init(conn) -> None:
    """Create the table + indexes if absent. Safe to call on every startup."""
    conn.execute(_DDL)
    for stmt in _INDEXES:
        conn.execute(stmt)
    conn.commit()


def upsert(conn, row: dict) -> dict:
    """Insert or update by primary key; returns the stored row."""
    cols = ",".join(COLUMNS)
    placeholders = ",".join("?" for _ in COLUMNS)
    updates = ",".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id")
    conn.execute(
        f"INSERT INTO treasures ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        tuple(row.get(c) for c in COLUMNS))
    conn.commit()
    return get(conn, row["id"])


def _as_dict(r) -> dict:
    return {c: r[c] for c in COLUMNS}


def get(conn, ident: str) -> dict | None:
    """Fetch by id, falling back to slug."""
    cols = ",".join(COLUMNS)
    r = conn.execute(
        f"SELECT {cols} FROM treasures WHERE id=?", (ident,)).fetchone()
    if r is None:
        r = conn.execute(
            f"SELECT {cols} FROM treasures WHERE slug=? "
            f"ORDER BY updated_at DESC", (ident,)).fetchone()
    return _as_dict(r) if r is not None else None


def list_rows(conn, *, status=None, language=None, kind=None, origin_id=None,
              query=None, limit=100, offset=0) -> list[dict]:
    """Filtered, newest-first listing. `query` matches title or slug."""
    where, params = [], []
    for col, val in (("status", status), ("language", language),
                     ("kind", kind), ("origin_id", origin_id)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    if query:
        where.append("(lower(title) LIKE ? OR lower(slug) LIKE ?)")
        like = f"%{query.lower()}%"
        params += [like, like]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ",".join(COLUMNS)
    params += [int(limit), int(offset)]
    rows = conn.execute(
        f"SELECT {cols} FROM treasures{clause} "
        f"ORDER BY updated_at DESC LIMIT ? OFFSET ?", tuple(params)).fetchall()
    return [_as_dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_store.py -q`
Expected: PASS (4 passed)

Then confirm nothing else broke: `cd backend && ../.venv/bin/python -m pytest tests -q`
Expected: 93 passed, 1 skipped (89 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/treasures/__init__.py backend/flightdeck/treasures/store.py backend/tests/test_treasures_store.py
git commit -m "feat(treasures): treasures index table + store CRUD

Portable DDL (SQLite for tests, PostgreSQL in production), TEXT ISO
timestamps like the existing ledger columns, ON CONFLICT upsert.
origin_id holds the Claude session id so artifacts join the session ledger."
```

---

## Task 2: Render — pandoc wrap, template, Vietnamese-capable fonts

**Files:**
- Create: `scripts/fetch-pandoc.sh`
- Create: `backend/flightdeck/treasures/templates/artifact.html`
- Create: `backend/flightdeck/treasures/templates/tokens.css`
- Create: `backend/flightdeck/treasures/templates/fonts/` (two woff2 files, fetched)
- Create: `backend/flightdeck/treasures/render.py`
- Modify: `Makefile` (add `pandoc` and `fonts` targets)
- Test: `backend/tests/test_treasures_render.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone).
- Produces:
  - `render.pandoc_path() -> str` (raises `RuntimeError` with install hint if absent)
  - `render.render(source_text: str, *, source_format: str, title: str, language: str = "en", kind: str = "report", workdir: str) -> dict` returning `{"html": str, "bytes": int, "warnings": list[str]}`
  - `render.EXTERNAL_REF_RE` (compiled regex used by both the module and its test)

- [ ] **Step 1: Fetch the pandoc binary and the fonts**

Create `scripts/fetch-pandoc.sh`:

```bash
#!/usr/bin/env bash
# Pinned static pandoc into ~/.flightdeck/bin (no root needed).
set -euo pipefail
VERSION=3.10.1
DEST="${HOME}/.flightdeck/bin"
mkdir -p "$DEST" "${TMPDIR:-/tmp}/pandoc-dl"
cd "${TMPDIR:-/tmp}/pandoc-dl"
URL="https://github.com/jgm/pandoc/releases/download/${VERSION}/pandoc-${VERSION}-linux-amd64.tar.gz"
curl -sSL "$URL" -o pandoc.tar.gz
tar xzf pandoc.tar.gz
install -m 0755 "pandoc-${VERSION}/bin/pandoc" "$DEST/pandoc"
"$DEST/pandoc" --version | head -1
```

Add to `Makefile`:

```make
FONT_DIR := backend/flightdeck/treasures/templates/fonts

pandoc:                     ## fetch the pinned static pandoc into ~/.flightdeck/bin
	bash scripts/fetch-pandoc.sh

fonts:                      ## fetch Vietnamese-capable artifact fonts (woff2)
	mkdir -p $(FONT_DIR)
	curl -sSL -A "Mozilla/5.0" -o $(FONT_DIR)/space-grotesk-vietnamese.woff2 \
	  "$$(curl -sSL -A 'Mozilla/5.0' 'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400..700&display=swap&subset=vietnamese' | grep -o 'https://[^)]*\.woff2' | head -1)"
	curl -sSL -A "Mozilla/5.0" -o $(FONT_DIR)/playfair-display-vietnamese.woff2 \
	  "$$(curl -sSL -A 'Mozilla/5.0' 'https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@1,600&display=swap&subset=vietnamese' | grep -o 'https://[^)]*\.woff2' | head -1)"
	ls -la $(FONT_DIR)
```

Run both:

```bash
make pandoc
make fonts
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_treasures_render.py`:

```python
import re
import pytest

from flightdeck.treasures import render

# Vietnamese codepoints absent from `latin-ext` subsets (U+1EA0-1EF9 block).
VN_CODEPOINTS = {0x1EBF: "ế", 0x1ED9: "ộ", 0x1EEF: "ữ", 0x1EE3: "ợ", 0x1EA1: "ạ"}

MD = """# Báo cáo Helpdesk

Nội dung tiếng Việt: hiệu quả, cộng đồng, người dùng.

- một
- hai
"""


def test_fonts_cover_vietnamese():
    """The artifact fonts must carry the Vietnamese block; latin-ext does not."""
    ttlib = pytest.importorskip("fontTools.ttLib")
    for path in render.font_paths():
        cmap = set(ttlib.TTFont(path).getBestCmap().keys())
        missing = [c for cp, c in VN_CODEPOINTS.items() if cp not in cmap]
        assert not missing, f"{path} is missing Vietnamese glyphs: {missing}"


def test_render_markdown_is_self_contained(tmp_path):
    out = render.render(MD, source_format="markdown", title="Báo cáo",
                        language="vi", workdir=str(tmp_path))
    html = out["html"]
    assert html.lstrip().startswith("<!doctype html>")
    assert "<title>Báo cáo</title>" in html
    assert 'lang="vi"' in html
    assert "data:font/woff2;base64," in html          # fonts embedded by pandoc
    assert 'rel="icon"' in html                       # favicon present
    assert render.EXTERNAL_REF_RE.findall(html) == []  # nothing external left
    assert out["bytes"] == len(html.encode("utf-8"))


def test_render_html_fragment_input(tmp_path):
    frag = '<section><h1>Từ HTML</h1><p>Khung do agent đưa.</p></section>'
    out = render.render(frag, source_format="html", title="Fragment",
                        language="vi", workdir=str(tmp_path))
    assert "Từ HTML" in out["html"]
    assert render.EXTERNAL_REF_RE.findall(out["html"]) == []


def test_render_warns_about_remote_assets(tmp_path):
    md = "# T\n\n![](https://example.com/chart.png)\n"
    out = render.render(md, source_format="markdown", title="T",
                        language="en", workdir=str(tmp_path))
    assert any("example.com" in w for w in out["warnings"])


def test_external_ref_regex_ignores_svg_namespace():
    """The favicon data URI contains the SVG namespace URL; it is not a fetch."""
    sample = ('<link rel="icon" href="data:image/svg+xml,'
              "<svg xmlns='http://www.w3.org/2000/svg'></svg>\">")
    assert render.EXTERNAL_REF_RE.findall(sample) == []
    assert render.EXTERNAL_REF_RE.findall('<img src="https://cdn.example/x.png">')
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'render' from 'flightdeck.treasures'`

- [ ] **Step 4: Write the template and tokens**

Create `backend/flightdeck/treasures/templates/artifact.html`:

```html
<!doctype html>
<html lang="$lang$" data-theme="night">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title$</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#128142;</text></svg>">
$for(css)$<link rel="stylesheet" href="$css$">$endfor$
</head>
<body class="kind-$kind$">
<main class="doc">
$if(title)$<h1 class="doc-title">$title$</h1>$endif$
$body$
</main>
</body>
</html>
```

Create `backend/flightdeck/treasures/templates/tokens.css`:

```css
/* Artifact tokens. Fonts are LOCAL woff2 files; pandoc --embed-resources
   base64-inlines them, so the published artifact makes zero network requests.
   Space Grotesk + Playfair Display carry the Vietnamese block (per
   ARTIFACT_STYLE.md); the app's Outfit/IBM-Plex latin-ext subsets do not. */
@font-face {
  font-family: 'Space Grotesk';
  src: url('fonts/space-grotesk-vietnamese.woff2') format('woff2');
  font-weight: 400 700;
  font-display: swap;
}
@font-face {
  font-family: 'Playfair Display';
  src: url('fonts/playfair-display-vietnamese.woff2') format('woff2');
  font-weight: 600;
  font-style: italic;
  font-display: swap;
}

:root {
  --ink: #12131a;
  --paper: #fbfaf7;
  --muted: #5d6070;
  --hair: rgba(18, 19, 26, 0.12);
  --accent: #0f766e;
}
html[data-theme='night'] {
  --ink: #e7e7ea;
  --paper: #0b0b0d;
  --muted: #9a9aa5;
  --hair: rgba(231, 231, 234, 0.14);
  --accent: #2dd4bf;
}

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: 'Space Grotesk', system-ui, sans-serif;
  line-height: 1.6;
}
.doc { max-width: 46rem; margin: 0 auto; padding: 3rem 1.5rem 6rem; }
.doc-title { font-size: 2rem; line-height: 1.2; margin: 0 0 1.5rem; }
h2 { margin-top: 2.5rem; border-bottom: 1px solid var(--hair); padding-bottom: .3rem; }
a { color: var(--accent); }
code, pre { font-family: ui-monospace, monospace; font-size: .9em; }
pre { overflow-x: auto; padding: 1rem; border: 1px solid var(--hair); border-radius: .5rem; }
table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
th, td { border-bottom: 1px solid var(--hair); padding: .5rem .6rem; text-align: left; }
blockquote { margin: 1.5rem 0; padding-left: 1rem; border-left: 2px solid var(--accent);
             color: var(--muted); font-family: 'Playfair Display', serif; font-style: italic; }
img { max-width: 100%; height: auto; }
```

- [ ] **Step 5: Write the renderer**

Create `backend/flightdeck/treasures/render.py`:

```python
"""Wrap content into a self-contained artifact with pandoc.

One transform, not a chain: `content (markdown | html fragment) + template +
tokens.css` -> a single HTML file with every asset inlined as a data URI.
Verified during design: pandoc's --embed-resources recurses into the linked
stylesheet, so `@font-face url(x.woff2)` becomes data:font/woff2;base64 and no
external reference survives.

pandoc resolves relative asset paths against its working directory, so the
template dir is copied into the caller's workdir before invoking it.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEMPLATE_FILE = TEMPLATES / "artifact.html"
TOKENS_CSS = TEMPLATES / "tokens.css"
FONTS_DIR = TEMPLATES / "fonts"

# claude.ai caps a rendered artifact at 16 MiB; warn from 80% up.
SIZE_WARN_BYTES = int(0.8 * 16 * 1024 * 1024)

# Any src=/href=/url() pointing at a real host. The favicon's data URI embeds
# the SVG *namespace* (http://www.w3.org/2000/svg), which is an identifier
# rather than a fetch, so w3.org is excluded.
EXTERNAL_REF_RE = re.compile(
    r"""(?:src|href)\s*=\s*["'](?!data:)https?://(?!www\.w3\.org/)[^"']+"""
    r"""|url\(\s*["']?(?!data:)https?://(?!www\.w3\.org/)[^)"']+""",
    re.IGNORECASE)

# Remote assets pandoc will fetch during the build (convenient, but never silent).
_REMOTE_IN_SOURCE_RE = re.compile(r"https?://[^\s)\"'<>]+", re.IGNORECASE)


def font_paths() -> list[str]:
    """The woff2 files tokens.css references. Used by the coverage test."""
    return sorted(str(p) for p in FONTS_DIR.glob("*.woff2"))


def pandoc_path() -> str:
    """Resolve the pandoc binary: env override, ~/.flightdeck/bin, then PATH."""
    env = os.environ.get("TREASURES_PANDOC")
    candidates = [env] if env else []
    candidates.append(str(Path.home() / ".flightdeck" / "bin" / "pandoc"))
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    found = shutil.which("pandoc")
    if found:
        return found
    raise RuntimeError(
        "pandoc not found. Run `make pandoc` (installs the pinned static "
        "binary into ~/.flightdeck/bin, no root needed) or set TREASURES_PANDOC.")


def render(source_text: str, *, source_format: str, title: str,
           language: str = "en", kind: str = "report", workdir: str) -> dict:
    """Render `source_text` into a self-contained HTML string.

    source_format: "markdown" or "html" (an HTML fragment, not a document).
    workdir: a real directory; the template + fonts are copied in so pandoc can
             resolve the relative asset paths, and any `assets/` the caller has
             already placed there is picked up too.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TOKENS_CSS, work / "tokens.css")
    dest_fonts = work / "fonts"
    dest_fonts.mkdir(exist_ok=True)
    for font in FONTS_DIR.glob("*.woff2"):
        shutil.copy2(font, dest_fonts / font.name)

    ext = "md" if source_format == "markdown" else "html"
    src = work / f"source.{ext}"
    src.write_text(source_text, encoding="utf-8")

    argv = [pandoc_path(), src.name]
    if source_format == "html":
        argv += ["-f", "html"]
    argv += [
        "--standalone", "--embed-resources",
        "--template", str(TEMPLATE_FILE),
        "-c", "tokens.css",
        "-M", f"title={title}",
        "-M", f"lang={language}",
        "-M", f"kind={kind}",
    ]
    proc = subprocess.run(argv, cwd=str(work), capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed: {proc.stderr.strip()[:500]}")

    html = proc.stdout
    warnings = []
    if proc.stderr.strip():
        warnings.append(f"pandoc: {proc.stderr.strip()[:300]}")
    for url in sorted(set(_REMOTE_IN_SOURCE_RE.findall(source_text))):
        warnings.append(f"fetched remote asset during wrap: {url}")
    leftovers = EXTERNAL_REF_RE.findall(html)
    if leftovers:
        warnings.append(
            f"{len(leftovers)} external reference(s) survived — the artifact is "
            f"NOT self-contained: {leftovers[:3]}")
    size = len(html.encode("utf-8"))
    if size > SIZE_WARN_BYTES:
        warnings.append(
            f"rendered size {size / 1048576:.1f} MiB approaches the 16 MiB cap")
    return {"html": html, "bytes": size, "warnings": warnings}
```

- [ ] **Step 6: Install the test-only font dependency**

Run from the repo root:

```bash
uv pip install --python .venv/bin/python fonttools brotli
```

Then append to `backend/requirements.txt`:

```
# test-only: verifies the artifact fonts carry the Vietnamese block
fonttools>=4.53
brotli>=1.1
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_render.py -q`
Expected: PASS (5 passed)

If `test_fonts_cover_vietnamese` fails, the `make fonts` download picked a
non-Vietnamese subset: re-run `make fonts`, then verify by hand with
`../.venv/bin/python -c "from fontTools.ttLib import TTFont; print(0x1EBF in TTFont('flightdeck/treasures/templates/fonts/space-grotesk-vietnamese.woff2').getBestCmap())"`
which must print `True`.

- [ ] **Step 8: Commit**

```bash
git add scripts/fetch-pandoc.sh Makefile backend/requirements.txt \
        backend/flightdeck/treasures/render.py \
        backend/flightdeck/treasures/templates backend/tests/test_treasures_render.py
git commit -m "feat(treasures): pandoc wrap engine, artifact template, VN fonts

pandoc --embed-resources inlines the local woff2 via tokens.css, so the output
has zero external refs (asserted). Fonts are Space Grotesk + Playfair Display
with the vietnamese subset: the app's latin-ext subsets are missing U+1EA0-1EF9
(measured), which would silently break Vietnamese drafts."
```

---

## Task 3: Filestore — artifact dirs, versions, sidecar

**Files:**
- Create: `backend/flightdeck/treasures/filestore.py`
- Test: `backend/tests/test_treasures_filestore.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `filestore.root() -> Path`
  - `filestore.new_id() -> str` (12 lowercase hex chars)
  - `filestore.slugify(title: str) -> str` (ASCII kebab, Vietnamese diacritics folded)
  - `filestore.checksum(text: str) -> str` (sha256 hex)
  - `filestore.artifact_dir(slug: str, art_id: str) -> Path`
  - `filestore.write_version(art_dir: Path, version: int, source_text: str, source_ext: str, html: str) -> dict` → `{"version_dir", "source_path", "artifact_path"}`
  - `filestore.write_meta(art_dir: Path, meta: dict) -> Path`
  - `filestore.read_meta(art_dir: Path) -> dict | None`
  - `filestore.next_version(art_dir: Path) -> int`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_filestore.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_filestore.py -q`
Expected: FAIL — `ImportError: cannot import name 'filestore'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/treasures/filestore.py`:

```python
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


def artifact_dir(slug: str, art_id: str) -> Path:
    path = root() / f"{slug}-{art_id}"
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_filestore.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/treasures/filestore.py backend/tests/test_treasures_filestore.py
git commit -m "feat(treasures): filestore layout, version dirs, meta.json sidecar

Artifacts live outside the repo (TREASURES_STORE, default ~/.flightdeck/
treasures) with one dir per artifact and a version subdir per render. The
sidecar mirrors the index row so the DB can be rebuilt from disk. Slugs fold
Vietnamese diacritics."
```

---

## Task 4: Service — the wrap use case end to end

**Files:**
- Create: `backend/flightdeck/treasures/service.py`
- Test: `backend/tests/test_treasures_service.py`

**Interfaces:**
- Consumes: `store.init/upsert/get/list_rows`, `filestore.*`, `render.render` (Tasks 1-3).
- Produces:
  - `service.wrap(conn, *, title, content, source_format="markdown", kind="report", language="en", origin_kind=None, origin_id=None, origin_path=None, authored_at=None) -> dict`
  - `service.get(conn, ident, *, include_source=False, include_html=False) -> dict | None`
  - `service.list_rows(conn, **filters) -> list[dict]` (thin pass-through)
  - `service.now_iso() -> str`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_service.py`:

```python
import json
from pathlib import Path

from flightdeck import db
from flightdeck.treasures import filestore, service, store

MD = "# Báo cáo\n\nNội dung tiếng Việt.\n"


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    store.init(conn)
    return conn


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'service'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/treasures/service.py`:

```python
"""The wrap use case: render -> write files -> index.

Order matters: the artifact is rendered and written to disk first, and only a
successful render produces an index row. The DB therefore never advertises an
artifact that is not on disk.
"""
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.treasures import filestore, render, store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wrap(conn, *, title, content, source_format="markdown", kind="report",
         language="en", origin_kind=None, origin_id=None, origin_path=None,
         authored_at=None, artifact_id=None) -> dict:
    """Render `content` into a new artifact version and index it.

    Pass `artifact_id` to add a version to an existing artifact; omit it to
    create a new one.
    """
    existing = store.get(conn, artifact_id) if artifact_id else None
    if existing:
        art_id = existing["id"]
        slug = existing["slug"]
        art_dir = Path(existing["dir_path"])
        art_dir.mkdir(parents=True, exist_ok=True)
    else:
        art_id = filestore.new_id()
        slug = filestore.slugify(title)
        art_dir = filestore.artifact_dir(slug, art_id)

    version = filestore.next_version(art_dir)
    ext = "md" if source_format == "markdown" else "html"
    rendered = render.render(content, source_format=source_format, title=title,
                             language=language, kind=kind,
                             workdir=str(art_dir / f"v{version}"))
    paths = filestore.write_version(art_dir, version, content, ext,
                                    rendered["html"])

    stamp = now_iso()
    row = {
        "id": art_id,
        "title": title,
        "slug": slug,
        "dir_path": str(art_dir),
        "kind": kind,
        "language": language,
        "status": existing["status"] if existing else "draft",
        "version": version,
        "source_format": source_format,
        "source_checksum": filestore.checksum(content),
        "render_checksum": filestore.checksum(rendered["html"]),
        "render_bytes": rendered["bytes"],
        "origin_kind": origin_kind or (existing or {}).get("origin_kind"),
        "origin_id": origin_id or (existing or {}).get("origin_id"),
        "origin_path": origin_path or (existing or {}).get("origin_path"),
        "published_url": (existing or {}).get("published_url"),
        "duplicate_of": (existing or {}).get("duplicate_of"),
        "authored_at": authored_at or (existing or {}).get("authored_at") or stamp,
        "ingested_at": (existing or {}).get("ingested_at") or stamp,
        "updated_at": stamp,
    }
    stored = store.upsert(conn, row)
    filestore.write_meta(art_dir, stored)
    return {**stored,
            "artifact_path": paths["artifact_path"],
            "source_path": paths["source_path"],
            "warnings": rendered["warnings"]}


def _version_paths(row: dict) -> dict:
    vdir = Path(row["dir_path"]) / f"v{row['version']}"
    ext = "md" if row["source_format"] == "markdown" else "html"
    return {"source_path": str(vdir / f"source.{ext}"),
            "artifact_path": str(vdir / "artifact.html")}


def get(conn, ident, *, include_source=False, include_html=False):
    row = store.get(conn, ident)
    if row is None:
        return None
    paths = _version_paths(row)
    out = {**row, **paths}
    if include_source:
        out["source"] = Path(paths["source_path"]).read_text(encoding="utf-8")
    if include_html:
        out["html"] = Path(paths["artifact_path"]).read_text(encoding="utf-8")
    return out


def list_rows(conn, **filters):
    return store.list_rows(conn, **filters)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_service.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/treasures/service.py backend/tests/test_treasures_service.py
git commit -m "feat(treasures): wrap use case (render -> files -> index)

Render and write happen before the index row, so the DB never advertises an
artifact that is not on disk. Re-wrapping with artifact_id adds a version and
keeps the previous one."
```

---

## Task 5: MCP server — `treasure_wrap` / `treasure_get` / `treasure_list`

**Files:**
- Create: `backend/flightdeck/treasures/mcp_server.py`
- Modify: `.mcp.json` (repo root)
- Modify: `docs/treasures-design.md` (record the two amendments)
- Test: `backend/tests/test_treasures_mcp.py`

**Interfaces:**
- Consumes: `service.wrap/get/list_rows`, `store.init`, `flightdeck.config.load`, `flightdeck.db.configure/open_write`.
- Produces: a stdio JSON-RPC server; `mcp_server.TOOLS` dict and `mcp_server.handle(req: dict) -> dict | None` (pure function, so the test needs no subprocess).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_treasures_mcp.py`:

```python
import json

import pytest

from flightdeck.treasures import mcp_server


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Point the server at a scratch SQLite DB + filestore."""
    monkeypatch.setenv("TREASURES_STORE", str(tmp_path / "store"))
    mcp_server.configure({"db_path": str(tmp_path / "t.db"),
                          "database_url": None})
    return mcp_server


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


def test_initialize_and_tools_list(wired):
    init = wired.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "treasures"
    listed = wired.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"treasure_wrap", "treasure_get", "treasure_list"}
    for tool in listed["result"]["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_notifications_get_no_response(wired):
    assert wired.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_wrap_get_list_round_trip(wired):
    wrapped = _call(wired, "treasure_wrap",
                    {"title": "Báo cáo", "content": "# Báo cáo\n\nNội dung.\n",
                     "language": "vi", "origin_id": "sess-9"})
    assert wrapped["language"] == "vi"
    assert wrapped["version"] == 1
    assert wrapped["artifact_path"].endswith("v1/artifact.html")

    got = _call(wired, "treasure_get", {"ident": wrapped["id"],
                                        "include_source": True})
    assert got["source"].startswith("# Báo cáo")

    listed = _call(wired, "treasure_list", {"language": "vi"})
    assert [r["id"] for r in listed["treasures"]] == [wrapped["id"]]
    assert _call(wired, "treasure_list", {"language": "en"})["treasures"] == []


def test_tool_errors_come_back_as_data_not_crashes(wired):
    out = _call(wired, "treasure_get", {"ident": "does-not-exist"})
    assert out["error"].startswith("not found")
    unknown = _call(wired, "nope", {})
    assert "unknown tool" in unknown["error"]


def test_unknown_method_returns_jsonrpc_error(wired):
    resp = wired.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    assert resp["error"]["code"] == -32601
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_mcp.py -q`
Expected: FAIL — `ImportError: cannot import name 'mcp_server'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/treasures/mcp_server.py`:

```python
#!/usr/bin/env python3
"""Treasures MCP server — stdio, newline-delimited JSON-RPC.

Three tools in slice 1: wrap content into a self-contained artifact, read one
back, and list the library. The agent supplies content only; this server owns
format, style, and render.

It runs outside FlightDeck's process (any session can call it), so it resolves
its own configuration: the repo-root `.env` for TOKEN_AUDIT_DATABASE_URL, then
config.toml through flightdeck.config. `handle()` is a pure function of a
request dict, which is what the tests exercise.
"""
import json
import os
import sys
from pathlib import Path

# Allow `python .../treasures/mcp_server.py` from any cwd: backend/ is two
# levels up from this file, and that is what makes `flightdeck` importable.
BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flightdeck import config, db                       # noqa: E402
from flightdeck.treasures import service, store         # noqa: E402

_state = {"cfg": None, "conn": None}


def _load_dotenv() -> None:
    """systemd injects .env for the web app; an MCP server gets no such help,
    so read the repo-root .env ourselves (KEY=VALUE lines, no export/quotes)."""
    env_path = BACKEND.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def configure(cfg: dict | None = None) -> None:
    """Wire the engine + schema once. Tests pass an explicit cfg."""
    if cfg is None:
        _load_dotenv()
        cfg = config.load(os.environ.get(
            "TOKEN_AUDIT_CONFIG", str(BACKEND / "config.toml")))
    db.configure(cfg)
    conn = db.open_write(cfg["db_path"])
    store.init(conn)
    _state["cfg"] = cfg
    _state["conn"] = conn


def _conn():
    if _state["conn"] is None:
        configure()
    return _state["conn"]


def t_wrap(title, content, source_format="markdown", kind="report",
           language="en", origin_kind=None, origin_id=None, origin_path=None,
           artifact_id=None):
    return service.wrap(_conn(), title=title, content=content,
                        source_format=source_format, kind=kind,
                        language=language, origin_kind=origin_kind,
                        origin_id=origin_id, origin_path=origin_path,
                        artifact_id=artifact_id)


def t_get(ident, include_source=False, include_html=False):
    row = service.get(_conn(), ident, include_source=include_source,
                      include_html=include_html)
    return row if row is not None else {"error": f"not found: {ident}"}


def t_list(status=None, language=None, kind=None, origin_id=None, query=None,
           limit=100, offset=0):
    rows = service.list_rows(_conn(), status=status, language=language,
                             kind=kind, origin_id=origin_id, query=query,
                             limit=limit, offset=offset)
    return {"treasures": rows, "count": len(rows)}


TOOLS = {
    "treasure_wrap": (
        t_wrap,
        "Wrap markdown (or an HTML fragment) into a self-contained, "
        "publish-ready HTML artifact, store it, and index it. Returns the "
        "artifact row plus artifact_path and any warnings. Pass artifact_id to "
        "add a version to an existing artifact.",
        {"title": {"type": "string"},
         "content": {"type": "string"},
         "source_format": {"type": "string", "enum": ["markdown", "html"]},
         "kind": {"type": "string"},
         "language": {"type": "string", "enum": ["en", "vi"]},
         "origin_kind": {"type": "string"},
         "origin_id": {"type": "string",
                       "description": "Claude session id, when known"},
         "origin_path": {"type": "string"},
         "artifact_id": {"type": "string"}},
        ["title", "content"]),
    "treasure_get": (
        t_get,
        "Read one artifact by id or slug, optionally with its markdown source "
        "and/or rendered HTML.",
        {"ident": {"type": "string"},
         "include_source": {"type": "boolean"},
         "include_html": {"type": "boolean"}},
        ["ident"]),
    "treasure_list": (
        t_list,
        "List stored artifacts, newest first, with optional filters.",
        {"status": {"type": "string"},
         "language": {"type": "string"},
         "kind": {"type": "string"},
         "origin_id": {"type": "string"},
         "query": {"type": "string"},
         "limit": {"type": "integer"},
         "offset": {"type": "integer"}},
        []),
}


def handle(req: dict):
    """Map one JSON-RPC request to a response dict (None for notifications)."""
    mid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "treasures", "version": "0.1"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r}}
            for n, (_f, d, p, r) in TOOLS.items()]}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        try:
            out = entry[0](**args) if entry else {"error": f"unknown tool {name}"}
        except Exception as e:                      # surface as data, never crash
            out = {"error": f"{type(e).__name__}: {e}"}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_treasures_mcp.py -q`
Expected: PASS (5 passed)

Then the whole suite: `cd backend && ../.venv/bin/python -m pytest tests -q`
Expected: 107 passed, 1 skipped (89 existing + 18 new)

- [ ] **Step 5: Register the MCP server**

Create/modify `.mcp.json` at the repo root of `flight-deck.sh`:

```json
{
  "mcpServers": {
    "treasures": {
      "command": "/home/nathando/Documents/Projects/flight-deck.sh/.venv/bin/python",
      "args": ["/home/nathando/Documents/Projects/flight-deck.sh/backend/flightdeck/treasures/mcp_server.py"]
    }
  }
}
```

- [ ] **Step 6: Verify against the live PostgreSQL, end to end**

This step is the acceptance gate: the tests all ran on SQLite, so prove the same
path works on the production engine and produces a real artifact.

```bash
cd /home/nathando/Documents/Projects/flight-deck.sh
set -a; . .env; set +a
printf '%s\n' \
  '{"jsonrpc":"2.0","id":0,"method":"initialize"}' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"treasure_wrap","arguments":{"title":"Báo cáo thử","content":"# Báo cáo thử\n\nNội dung tiếng Việt: hiệu quả, cộng đồng.\n","language":"vi","kind":"report"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"treasure_list","arguments":{}}}' \
  | .venv/bin/python backend/flightdeck/treasures/mcp_server.py
```

Expected: three JSON lines; the wrap result carries an `artifact_path` under
`~/.flightdeck/treasures/bao-cao-thu-*/v1/artifact.html`, `warnings: []`, and
the list shows one row.

Then confirm the row landed in PostgreSQL and the file is genuinely
self-contained:

```bash
.venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ['TOKEN_AUDIT_DATABASE_URL']) as c:
    print(c.execute('select id, slug, language, version, render_bytes from treasures').fetchall())
    print('relpersistence(p=LOGGED):', c.execute(\"select relpersistence from pg_class where relname='treasures'\").fetchone())
"
ART=$(ls ~/.flightdeck/treasures/bao-cao-thu-*/v1/artifact.html | head -1)
grep -c "data:font/woff2;base64," "$ART"          # expect >= 1
grep -oE '(src|href)="https?://[^"]+' "$ART" | grep -v w3.org | wc -l   # expect 0
```

Open the file in a browser and confirm the Vietnamese diacritics render with
the embedded font rather than a fallback.

- [ ] **Step 7: Record the amendments in the design doc**

In `docs/treasures-design.md`, under §2, append two rows to the decisions table:

```markdown
| Package path (amendment) | `backend/flightdeck/treasures/` | The spec said `treasures/` at the repo root, but the package must import `flightdeck.db`; making it a subpackage beside `hub/`, `systems/`, `agui/` keeps imports plain and matches the existing layout. |
| Timestamp type (amendment) | `text` ISO-8601 UTC, set in Python | The spec said `timestamptz`. TEXT keeps one portable DDL for SQLite (tests) and PostgreSQL (production) — no `now()` vs `CURRENT_TIMESTAMP` split — and matches the existing `messages.ts` / `tool_calls.ts` columns. |
```

- [ ] **Step 8: Commit**

```bash
git add backend/flightdeck/treasures/mcp_server.py backend/tests/test_treasures_mcp.py \
        .mcp.json docs/treasures-design.md
git commit -m "feat(treasures): MCP server with wrap/get/list

Stdio JSON-RPC, three tools, handle() kept pure so tests need no subprocess.
The server resolves its own config (.env + config.toml) because it runs outside
FlightDeck's process. Verified on the live PostgreSQL: a Vietnamese artifact
wraps with fonts embedded and zero external refs."
```

---

## Self-Review

**1. Spec coverage (slice 1 scope only).** `treasure_wrap` / `treasure_get` /
`treasure_list` → Task 5; the `treasures` table with the spec's field set →
Task 1; filestore layout + `meta.json` + versions → Task 3; pandoc pipeline with
template + tokens + embedded fonts and the 16 MiB guard → Task 2; wrap
orchestration → Task 4. Two spec verification items are covered as tests:
"zero external refs" and "VN diacritics render with the subset font" (Task 2),
"re-wrap bumps version, history kept" (Task 4). **Deferred to later slices by
design:** `treasure_update`, `treasure_link_source`, `treasure_discover`,
`treasure_publish_prepare`, the FlightDeck view, and Milkdown editing. The
`origin_id` open question is handled the safe way the spec recommended — option
(b): the caller passes it when known, NULL otherwise; option (c) backfill
belongs to the discovery slice.

**2. Placeholder scan.** No TBD/TODO; every code step carries runnable code;
every test step carries real assertions; the one conditional instruction
(Task 2 Step 7's font re-fetch) names the exact command and the exact expected
output rather than describing an intent.

**3. Type consistency.** `store.COLUMNS` is the single column list used by
`upsert`/`get`/`list_rows`; `service.wrap` builds a dict with exactly those keys
plus the three extras it returns (`artifact_path`, `source_path`, `warnings`);
`filestore.write_version` returns the three keys `service` reads
(`version_dir`, `source_path`, `artifact_path`); `render.render` returns
`html`/`bytes`/`warnings`, matching every caller and test. `list_rows` (not
`list`) is used consistently in `store`, `service`, and `mcp_server`.
`mcp_server.configure(cfg)` accepts the same `cfg` shape `flightdeck.config`
produces (`db_path`, `database_url`).
