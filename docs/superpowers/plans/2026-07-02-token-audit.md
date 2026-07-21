# Claude Code Token Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local realtime dashboard that reads Claude Code session logs and reports token usage, cache in/out, API-equivalent cost, and savings — modeled on the "Claude Code — Token Audit" reference.

**Architecture:** FastAPI + SQLite backend ingests `~/.claude/projects/**/*.jsonl` incrementally into a raw-token `messages` table; cost is computed in the API layer from a code-side pricing table (rate change ⇒ no re-ingest). A `watchdog` observer re-ingests on file change and pushes SSE updates. A React/Vite/Tailwind frontend renders stat cards, a daily-cost chart, and a per-session table, auto-refreshing on SSE.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, watchdog, SQLite (stdlib `sqlite3`), pytest; React 18 + Vite + Tailwind + Recharts.

## Global Constraints

- All artifacts (code, comments, docs) in **English**.
- Backend is **read-only** on `~/.claude/`; never write there.
- **No network egress** of usage data — 100% local.
- Cost stored **nowhere**; only raw token counts persist. Cost computed on read.
- Package layout: Python package `token_audit/` at `token-audit/`; tests in `token-audit/tests/`.
- Pricing rates (per 1M tokens): opus-4-x $5/$25, sonnet-5/4-6 $3/$15, haiku-4-5 $1/$5, fable-5/mythos-5 $10/$50. Cache multipliers on input rate: read 0.1, 5m-write 1.25, 1h-write 2.0.
- Config file `token-audit/config.toml`: `subscription_monthly_usd`, `projects_dir` (default `~/.claude/projects`), `db_path` (default `token-audit/audit.db`).

---

### Task 1: Project scaffold + pricing module

**Files:**
- Create: `token-audit/token_audit/__init__.py`
- Create: `token-audit/token_audit/pricing.py`
- Create: `token-audit/requirements.txt`
- Create: `token-audit/config.toml`
- Test: `token-audit/tests/test_pricing.py`

**Interfaces:**
- Produces:
  - `RATES: dict[str, tuple[float, float]]` — model-ID prefix → `(input_per_mtok, output_per_mtok)`.
  - `CACHE_READ_MULT=0.1`, `WRITE_5M_MULT=1.25`, `WRITE_1H_MULT=2.0` (float constants).
  - `rate_for(model: str) -> tuple[float, float] | None` — longest-prefix match; `None` if unknown.
  - `message_cost(model, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens) -> float | None` — USD; `None` if model unknown.
  - `cache_savings(model, cache_read) -> float` — USD saved vs full-price input (`0.0` if unknown).

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_pricing.py
from token_audit import pricing


def test_rate_for_opus_prefix():
    assert pricing.rate_for("claude-opus-4-8") == (5.0, 25.0)
    assert pricing.rate_for("claude-opus-4-6") == (5.0, 25.0)


def test_rate_for_unknown_is_none():
    assert pricing.rate_for("gpt-4o") is None


def test_message_cost_full_formula():
    # 1M input, 1M output, 1M cache_read, 1M 5m-write, 1M 1h-write on opus (5/25)
    cost = pricing.message_cost(
        "claude-opus-4-8",
        input_tokens=1_000_000,
        cache_read=1_000_000,
        cache_create_5m=1_000_000,
        cache_create_1h=1_000_000,
        output_tokens=1_000_000,
    )
    # 5 + 25 + 0.1*5 + 1.25*5 + 2.0*5 = 5+25+0.5+6.25+10 = 46.75
    assert round(cost, 4) == 46.75


def test_message_cost_unknown_model_none():
    assert pricing.message_cost("gpt-4o", 1000, 0, 0, 0, 1000) is None


def test_cache_savings():
    # 1M cache_read on opus: 1M * 0.9 * 5/1e6 = 4.5
    assert round(pricing.cache_savings("claude-opus-4-8", 1_000_000), 4) == 4.5
    assert pricing.cache_savings("gpt-4o", 1_000_000) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit'` (module not yet created).

- [ ] **Step 3: Write scaffold + implementation**

```python
# token-audit/token_audit/__init__.py
```
(empty file — marks the package)

```python
# token-audit/token_audit/pricing.py
"""Model pricing and cost math. Rates are per 1,000,000 tokens (list API prices)."""

# model-ID prefix -> (input_per_mtok, output_per_mtok)
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
}

CACHE_READ_MULT = 0.1
WRITE_5M_MULT = 1.25
WRITE_1H_MULT = 2.0


def rate_for(model: str) -> tuple[float, float] | None:
    """Longest-prefix match of a model ID against RATES."""
    best: tuple[str, tuple[float, float]] | None = None
    for prefix, rate in RATES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rate)
    return best[1] if best else None


def message_cost(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
) -> float | None:
    """API-equivalent USD cost for one message, or None if the model is unpriced."""
    rate = rate_for(model)
    if rate is None:
        return None
    r_in, r_out = rate
    return (
        input_tokens * r_in
        + output_tokens * r_out
        + cache_read * CACHE_READ_MULT * r_in
        + cache_create_5m * WRITE_5M_MULT * r_in
        + cache_create_1h * WRITE_1H_MULT * r_in
    ) / 1_000_000


def cache_savings(model: str, cache_read: int) -> float:
    """USD saved by cache reads vs paying full input price (0.0 if unpriced)."""
    rate = rate_for(model)
    if rate is None:
        return 0.0
    r_in, _ = rate
    return cache_read * (1 - CACHE_READ_MULT) * r_in / 1_000_000
```

```
# token-audit/requirements.txt
fastapi>=0.110
uvicorn[standard]>=0.29
watchdog>=4.0
pytest>=8.0
```

```toml
# token-audit/config.toml
subscription_monthly_usd = 200.0
projects_dir = "~/.claude/projects"
db_path = "audit.db"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_pricing.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git init -q 2>/dev/null; git add token_audit/ tests/test_pricing.py requirements.txt config.toml
git commit -m "feat: token-audit scaffold + pricing module"
```

---

### Task 2: SQLite store + schema

**Files:**
- Create: `token-audit/token_audit/db.py`
- Test: `token-audit/tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `connect(db_path: str) -> sqlite3.Connection` — opens with `row_factory = sqlite3.Row`, runs schema (idempotent `CREATE TABLE IF NOT EXISTS` + indexes).
  - Table `messages(uuid PK, session_id, project, model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens, service_tier)`.
  - Table `files(path PK, mtime, size, bytes_ingested, lines_ingested)`.

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_db.py
from token_audit import db


def test_connect_creates_tables(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"messages", "files"} <= names


def test_messages_upsert_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    row = ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-02T00:00:00Z",
           10, 20, 30, 0, 5, "standard")
    sql = ("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?) "
           "ON CONFLICT(uuid) DO NOTHING")
    conn.execute(sql, row)
    conn.execute(sql, row)  # duplicate
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit.db'`.

- [ ] **Step 3: Write implementation**

```python
# token-audit/token_audit/db.py
"""SQLite store for Claude Code token-usage messages (raw counts only)."""
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  uuid TEXT PRIMARY KEY,
  session_id TEXT,
  project TEXT,
  model TEXT,
  ts TEXT,
  input_tokens INTEGER,
  cache_read INTEGER,
  cache_create_5m INTEGER,
  cache_create_1h INTEGER,
  output_tokens INTEGER,
  service_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model);

CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime REAL,
  size INTEGER,
  bytes_ingested INTEGER,
  lines_ingested INTEGER
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_db.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add token_audit/db.py tests/test_db.py
git commit -m "feat: sqlite store + schema"
```

---

### Task 3: Incremental ingest

**Files:**
- Create: `token-audit/token_audit/ingest.py`
- Test: `token-audit/tests/test_ingest.py`

**Interfaces:**
- Consumes: `db.connect` (Task 2).
- Produces:
  - `parse_line(obj: dict) -> dict | None` — maps one parsed JSONL object to a `messages` row-dict (keys: uuid, session_id, project, model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens, service_tier). Returns `None` unless `obj["type"]=="assistant"` and `obj["message"]["usage"]` present.
  - `ingest_file(conn, path: str) -> int` — resumes from `files.bytes_ingested`; re-ingests from 0 if size shrank; upserts rows; returns count of new rows.
  - `ingest_all(conn, projects_dir: str) -> int` — walks `**/*.jsonl`, sums `ingest_file`.

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_ingest.py
import json
from token_audit import db, ingest


def _line(uuid, model="claude-opus-4-8", usage=True):
    o = {"type": "assistant", "uuid": uuid, "sessionId": "s1",
         "cwd": "/p", "timestamp": "2026-07-02T00:00:00Z",
         "message": {"model": model}}
    if usage:
        o["message"]["usage"] = {
            "input_tokens": 10, "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30, "output_tokens": 5,
            "service_tier": "standard",
            "cache_creation": {"ephemeral_5m_input_tokens": 30,
                               "ephemeral_1h_input_tokens": 0}}
    return json.dumps(o)


def test_parse_line_extracts_row():
    row = ingest.parse_line(json.loads(_line("u1")))
    assert row["uuid"] == "u1"
    assert row["cache_create_5m"] == 30
    assert row["cache_create_1h"] == 0
    assert row["cache_read"] == 20


def test_parse_line_skips_non_usage():
    assert ingest.parse_line({"type": "user"}) is None
    assert ingest.parse_line(json.loads(_line("u2", usage=False))) is None


def test_incremental_ingest(tmp_path):
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(_line("u1") + "\n")
    conn = db.connect(str(tmp_path / "t.db"))
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    # append one line; only the new one is ingested
    with f.open("a") as fh:
        fh.write(_line("u2") + "\n")
    assert ingest.ingest_all(conn, str(tmp_path)) == 1
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit.ingest'`.

- [ ] **Step 3: Write implementation**

```python
# token-audit/token_audit/ingest.py
"""Incremental parser/loader for Claude Code session JSONL files."""
import glob
import json
import os

_COLS = ("uuid", "session_id", "project", "model", "ts", "input_tokens",
         "cache_read", "cache_create_5m", "cache_create_1h",
         "output_tokens", "service_tier")


def parse_line(obj: dict) -> dict | None:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    cc = usage.get("cache_creation") or {}
    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": obj.get("cwd"),
        "model": msg.get("model"),
        "ts": obj.get("timestamp"),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_create_5m": cc.get("ephemeral_5m_input_tokens", 0),
        "cache_create_1h": cc.get("ephemeral_1h_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "service_tier": usage.get("service_tier"),
    }


def _upsert(conn, row: dict) -> bool:
    if not row.get("uuid"):
        return False
    placeholders = ",".join("?" for _ in _COLS)
    sql = (f"INSERT INTO messages ({','.join(_COLS)}) VALUES ({placeholders}) "
           "ON CONFLICT(uuid) DO NOTHING")
    cur = conn.execute(sql, tuple(row[c] for c in _COLS))
    return cur.rowcount > 0


def ingest_file(conn, path: str) -> int:
    st = os.stat(path)
    rec = conn.execute("SELECT bytes_ingested, size FROM files WHERE path=?",
                       (path,)).fetchone()
    start = 0
    if rec and st.st_size >= rec["size"]:
        start = rec["bytes_ingested"]
    # else: new file or truncated/rotated -> re-ingest from 0
    new_rows = 0
    lines = 0
    with open(path, "rb") as fh:
        fh.seek(start)
        for raw in fh:
            lines += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            row = parse_line(obj)
            if row and _upsert(conn, row):
                new_rows += 1
    conn.execute(
        "INSERT INTO files (path, mtime, size, bytes_ingested, lines_ingested) "
        "VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
        "mtime=excluded.mtime, size=excluded.size, "
        "bytes_ingested=excluded.bytes_ingested, "
        "lines_ingested=files.lines_ingested+excluded.lines_ingested",
        (path, st.st_mtime, st.st_size, st.st_size, lines))
    conn.commit()
    return new_rows


def ingest_all(conn, projects_dir: str) -> int:
    projects_dir = os.path.expanduser(projects_dir)
    total = 0
    for path in glob.glob(os.path.join(projects_dir, "**", "*.jsonl"),
                          recursive=True):
        total += ingest_file(conn, path)
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_ingest.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add token_audit/ingest.py tests/test_ingest.py
git commit -m "feat: incremental JSONL ingest"
```

---

### Task 4: Aggregation queries (metrics layer)

**Files:**
- Create: `token-audit/token_audit/metrics.py`
- Create: `token-audit/token_audit/config.py`
- Test: `token-audit/tests/test_metrics.py`

**Interfaces:**
- Consumes: `db.connect` (Task 2); `pricing.message_cost`, `pricing.cache_savings` (Task 1).
- Produces:
  - `config.load(path="config.toml") -> dict` — reads TOML via stdlib `tomllib`; keys `subscription_monthly_usd`, `projects_dir`, `db_path`.
  - `summary(conn, subscription_monthly_usd, rtk_savings_usd=0.0) -> dict` — keys: `total_cost`, `cache_savings`, `saved_vs_subscription`, `rtk_savings`, `cache_hit_rate`, `input_tokens`, `output_tokens`, `session_count`, `unknown_model_tokens`.
  - `daily(conn) -> list[dict]` — `[{date, cost, input, output, cache_read}]` ordered by date.
  - `sessions(conn, limit, offset) -> list[dict]` — `[{session_id, project, models, first_ts, last_ts, input, output, cache_read, cost}]`.
  - `by_model(conn) -> list[dict]` — `[{model, input, output, cache_read, cost, priced}]`.

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_metrics.py
from token_audit import db, metrics


def _seed(conn):
    rows = [
        ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-01T00:00:00Z",
         1_000_000, 1_000_000, 0, 0, 0, "standard"),
        ("u2", "s2", "/p", "gpt-4o", "2026-07-02T00:00:00Z",
         500, 0, 0, 0, 500, "standard"),
    ]
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def test_summary_metrics(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    _seed(conn)
    s = metrics.summary(conn, subscription_monthly_usd=200.0, rtk_savings_usd=1.5)
    # u1 cost: 1M input*5/1e6 + 1M read*0.1*5/1e6 = 5 + 0.5 = 5.5 ; u2 unknown -> excluded
    assert round(s["total_cost"], 4) == 5.5
    assert round(s["cache_savings"], 4) == 4.5   # 1M*0.9*5/1e6
    assert s["session_count"] == 2
    assert s["unknown_model_tokens"] == 1000      # u2 input+output
    assert s["rtk_savings"] == 1.5
    # cache hit rate = read / (read + create + input) = 1M / (1M + 0 + 1_000_500)
    assert 0.49 < s["cache_hit_rate"] < 0.5


def test_daily_ordered(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    _seed(conn)
    d = metrics.daily(conn)
    assert [x["date"] for x in d] == ["2026-07-01", "2026-07-02"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit.metrics'`.

- [ ] **Step 3: Write implementation**

```python
# token-audit/token_audit/config.py
"""Load config.toml with expanduser applied to paths."""
import os
import tomllib


def load(path: str = "config.toml") -> dict:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    cfg.setdefault("subscription_monthly_usd", 0.0)
    cfg.setdefault("projects_dir", "~/.claude/projects")
    cfg.setdefault("db_path", "audit.db")
    cfg["projects_dir"] = os.path.expanduser(cfg["projects_dir"])
    cfg["db_path"] = os.path.expanduser(cfg["db_path"])
    return cfg
```

```python
# token-audit/token_audit/metrics.py
"""Derived metrics computed from the raw messages table + pricing."""
from token_audit import pricing


def _rows(conn):
    return conn.execute(
        "SELECT model, ts, input_tokens, cache_read, cache_create_5m, "
        "cache_create_1h, output_tokens, session_id FROM messages").fetchall()


def summary(conn, subscription_monthly_usd: float, rtk_savings_usd: float = 0.0) -> dict:
    total_cost = 0.0
    cache_sav = 0.0
    tot_read = tot_create = tot_input = tot_output = 0
    unknown_tokens = 0
    sessions = set()
    for r in _rows(conn):
        sessions.add(r["session_id"])
        tot_read += r["cache_read"]
        tot_create += r["cache_create_5m"] + r["cache_create_1h"]
        tot_input += r["input_tokens"]
        tot_output += r["output_tokens"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        if c is None:
            unknown_tokens += r["input_tokens"] + r["output_tokens"]
        else:
            total_cost += c
            cache_sav += pricing.cache_savings(r["model"], r["cache_read"])
    denom = tot_read + tot_create + tot_input
    return {
        "total_cost": total_cost,
        "cache_savings": cache_sav,
        "saved_vs_subscription": total_cost - subscription_monthly_usd,
        "rtk_savings": rtk_savings_usd,
        "cache_hit_rate": (tot_read / denom) if denom else 0.0,
        "input_tokens": tot_input,
        "output_tokens": tot_output,
        "session_count": len(sessions),
        "unknown_model_tokens": unknown_tokens,
    }


def daily(conn) -> list[dict]:
    out = {}
    for r in _rows(conn):
        day = (r["ts"] or "")[:10]
        d = out.setdefault(day, {"date": day, "cost": 0.0, "input": 0,
                                 "output": 0, "cache_read": 0})
        d["input"] += r["input_tokens"]
        d["output"] += r["output_tokens"]
        d["cache_read"] += r["cache_read"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        d["cost"] += c or 0.0
    return [out[k] for k in sorted(out)]


def sessions(conn, limit: int = 100, offset: int = 0) -> list[dict]:
    agg = {}
    for r in _rows(conn):
        s = agg.setdefault(r["session_id"], {
            "session_id": r["session_id"], "project": None, "models": set(),
            "first_ts": r["ts"], "last_ts": r["ts"], "input": 0, "output": 0,
            "cache_read": 0, "cost": 0.0})
        s["models"].add(r["model"])
        if r["ts"] and (s["first_ts"] is None or r["ts"] < s["first_ts"]):
            s["first_ts"] = r["ts"]
        if r["ts"] and (s["last_ts"] is None or r["ts"] > s["last_ts"]):
            s["last_ts"] = r["ts"]
        s["input"] += r["input_tokens"]
        s["output"] += r["output_tokens"]
        s["cache_read"] += r["cache_read"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        s["cost"] += c or 0.0
    # project per session: fetch one representative cwd
    for row in conn.execute(
            "SELECT session_id, project FROM messages GROUP BY session_id"):
        if row["session_id"] in agg:
            agg[row["session_id"]]["project"] = row["project"]
    ordered = sorted(agg.values(), key=lambda x: x["last_ts"] or "", reverse=True)
    sliced = ordered[offset:offset + limit]
    for s in sliced:
        s["models"] = sorted(m for m in s["models"] if m)
    return sliced


def by_model(conn) -> list[dict]:
    agg = {}
    for r in _rows(conn):
        m = agg.setdefault(r["model"], {
            "model": r["model"], "input": 0, "output": 0, "cache_read": 0,
            "cost": 0.0, "priced": pricing.rate_for(r["model"] or "") is not None})
        m["input"] += r["input_tokens"]
        m["output"] += r["output_tokens"]
        m["cache_read"] += r["cache_read"]
        c = pricing.message_cost(
            r["model"], r["input_tokens"], r["cache_read"],
            r["cache_create_5m"], r["cache_create_1h"], r["output_tokens"])
        m["cost"] += c or 0.0
    return sorted(agg.values(), key=lambda x: x["cost"], reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_metrics.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add token_audit/metrics.py token_audit/config.py tests/test_metrics.py
git commit -m "feat: metrics aggregation layer"
```

---

### Task 5: RTK savings parser

**Files:**
- Create: `token-audit/token_audit/rtk.py`
- Test: `token-audit/tests/test_rtk.py`

**Interfaces:**
- Consumes: nothing (shells out to `rtk`).
- Produces:
  - `parse_gain(text: str) -> dict` — parses `rtk gain` plain-text output into `{"tokens_saved": int, "commands": int}`; missing fields default to 0.
  - `rtk_savings_usd(rate_per_mtok: float = 5.0) -> float` — runs `rtk gain` (best-effort; returns 0.0 if `rtk` absent or errors), converts tokens_saved to USD at the given input rate.

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_rtk.py
from token_audit import rtk

SAMPLE = """RTK Token Savings (Global Scope)
Total commands:    1155
Input tokens:      809.2K
Tokens saved:      562.1K (69.5%)
"""


def test_parse_gain_extracts_tokens_saved():
    g = rtk.parse_gain(SAMPLE)
    assert g["tokens_saved"] == 562_100
    assert g["commands"] == 1155


def test_parse_gain_missing_defaults_zero():
    g = rtk.parse_gain("nothing here")
    assert g == {"tokens_saved": 0, "commands": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_rtk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit.rtk'`.

- [ ] **Step 3: Write implementation**

```python
# token-audit/token_audit/rtk.py
"""Best-effort parser for `rtk gain` CLI output (token savings at the CLI layer)."""
import re
import shutil
import subprocess


def _num(s: str) -> int:
    s = s.strip().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    return int(round(float(s) * mult))


def parse_gain(text: str) -> dict:
    out = {"tokens_saved": 0, "commands": 0}
    m = re.search(r"Tokens saved:\s*([\d.,]+[KM]?)", text)
    if m:
        out["tokens_saved"] = _num(m.group(1))
    m = re.search(r"Total commands:\s*([\d.,]+[KM]?)", text)
    if m:
        out["commands"] = _num(m.group(1))
    return out


def rtk_savings_usd(rate_per_mtok: float = 5.0) -> float:
    if not shutil.which("rtk"):
        return 0.0
    try:
        out = subprocess.run(["rtk", "gain"], capture_output=True, text=True,
                             timeout=10)
    except Exception:
        return 0.0
    if out.returncode != 0:
        return 0.0
    return parse_gain(out.stdout)["tokens_saved"] * rate_per_mtok / 1_000_000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_rtk.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add token_audit/rtk.py tests/test_rtk.py
git commit -m "feat: rtk gain savings parser"
```

---

### Task 6: FastAPI server + endpoints + SSE + watcher

**Files:**
- Create: `token-audit/token_audit/server.py`
- Test: `token-audit/tests/test_server.py`

**Interfaces:**
- Consumes: `config.load`, `db.connect`, `ingest.ingest_all`, `metrics.*`, `rtk.rtk_savings_usd`.
- Produces: an ASGI `app` (FastAPI). Endpoints: `GET /api/summary`, `/api/daily`, `/api/sessions`, `/api/by-model`, `/api/rtk`, `/api/stream` (SSE), static frontend at `/`. A module-level `asyncio.Event` (`updated`) is set after each ingest cycle; SSE waits on it.
- Note: the server opens SQLite with `check_same_thread=False`; the watcher runs `ingest_all` in a background thread and sets `updated`.

- [ ] **Step 1: Write the failing test**

```python
# token-audit/tests/test_server.py
import os
from fastapi.testclient import TestClient


def test_summary_endpoint(tmp_path, monkeypatch):
    # point the app at a temp config + seeded db
    cfg = tmp_path / "config.toml"
    db_path = tmp_path / "audit.db"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        f'subscription_monthly_usd = 200.0\n'
        f'projects_dir = "{proj}"\n'
        f'db_path = "{db_path}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))

    from token_audit import server
    app = server.create_app()
    with TestClient(app) as client:
        r = client.get("/api/summary")
        assert r.status_code == 200
        body = r.json()
        assert "total_cost" in body and "cache_hit_rate" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd token-audit && python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_audit.server'`.

- [ ] **Step 3: Write implementation**

```python
# token-audit/token_audit/server.py
"""FastAPI backend: metrics endpoints, SSE stream, filesystem watcher."""
import asyncio
import json
import os
import threading

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from token_audit import config, db, ingest, metrics, rtk

_updated = asyncio.Event()


def create_app() -> FastAPI:
    cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
    conn = db.connect(cfg["db_path"])  # check_same_thread handled below
    conn.close()
    conn = __import__("sqlite3").connect(cfg["db_path"], check_same_thread=False)
    conn.row_factory = __import__("sqlite3").Row

    app = FastAPI(title="Claude Code Token Audit")
    app.state.conn = conn
    app.state.cfg = cfg
    lock = threading.Lock()

    def reingest():
        with lock:
            ingest.ingest_all(conn, cfg["projects_dir"])

    @app.on_event("startup")
    async def _startup():
        reingest()
        loop = asyncio.get_event_loop()
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class H(FileSystemEventHandler):
                def on_any_event(self, event):
                    if str(event.src_path).endswith(".jsonl"):
                        reingest()
                        loop.call_soon_threadsafe(_updated.set)

            obs = Observer()
            obs.schedule(H(), cfg["projects_dir"], recursive=True)
            obs.daemon = True
            obs.start()
            app.state.observer = obs
        except Exception:
            app.state.observer = None

    @app.get("/api/summary")
    def summary():
        rate = rtk.rtk_savings_usd()
        return metrics.summary(conn, cfg["subscription_monthly_usd"], rate)

    @app.get("/api/daily")
    def daily():
        return metrics.daily(conn)

    @app.get("/api/sessions")
    def sessions(limit: int = 100, offset: int = 0):
        return metrics.sessions(conn, limit, offset)

    @app.get("/api/by-model")
    def by_model():
        return metrics.by_model(conn)

    @app.get("/api/rtk")
    def rtk_endpoint():
        return {"savings_usd": rtk.rtk_savings_usd()}

    @app.get("/api/stream")
    async def stream():
        async def gen():
            while True:
                await _updated.wait()
                _updated.clear()
                yield f"event: summary-updated\ndata: {json.dumps({'ts': True})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="static")

    return app


app = None
if os.environ.get("TOKEN_AUDIT_CONFIG") or os.path.exists("config.toml"):
    try:
        app = create_app()
    except Exception:
        app = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd token-audit && python -m pytest tests/test_server.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add token_audit/server.py tests/test_server.py
git commit -m "feat: fastapi server, sse, watcher"
```

---

### Task 7: React/Vite/Tailwind frontend

**Files:**
- Create: `token-audit/frontend/package.json`
- Create: `token-audit/frontend/vite.config.js`
- Create: `token-audit/frontend/tailwind.config.js`
- Create: `token-audit/frontend/postcss.config.js`
- Create: `token-audit/frontend/index.html`
- Create: `token-audit/frontend/src/main.jsx`
- Create: `token-audit/frontend/src/index.css`
- Create: `token-audit/frontend/src/App.jsx`
- Create: `token-audit/frontend/src/api.js`

**Interfaces:**
- Consumes: backend endpoints from Task 6.
- Produces: a built SPA in `frontend/dist/` served by the backend at `/`. Vite dev proxy forwards `/api` to the backend.

- [ ] **Step 1: Scaffold config files**

```json
// token-audit/frontend/package.json
{
  "name": "token-audit-frontend",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite --port 5190", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.3.0", "react-dom": "^18.3.0", "recharts": "^2.12.0" },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.0", "vite": "^5.4.0",
    "tailwindcss": "^3.4.0", "postcss": "^8.4.0", "autoprefixer": "^10.4.0"
  }
}
```

```js
// token-audit/frontend/vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8010" } },
});
```

```js
// token-audit/frontend/tailwind.config.js
export default { content: ["./index.html", "./src/**/*.{js,jsx}"], theme: { extend: {} }, plugins: [] };
```

```js
// token-audit/frontend/postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

```html
<!-- token-audit/frontend/index.html -->
<!doctype html>
<html lang="en" class="dark">
  <head><meta charset="UTF-8" /><title>Claude Code — Token Audit</title></head>
  <body class="bg-neutral-950 text-neutral-100">
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

```css
/* token-audit/frontend/src/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 2: API client + entrypoint**

```js
// token-audit/frontend/src/api.js
export async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
export function subscribe(onUpdate) {
  const es = new EventSource("/api/stream");
  es.addEventListener("summary-updated", onUpdate);
  return () => es.close();
}
```

```jsx
// token-audit/frontend/src/main.jsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
createRoot(document.getElementById("root")).render(<App />);
```

- [ ] **Step 3: App with stat cards, chart, table**

```jsx
// token-audit/frontend/src/App.jsx
import React, { useEffect, useState, useCallback } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { get, subscribe } from "./api.js";

const usd = (n) => `$${(n ?? 0).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const tok = (n) => (n ?? 0).toLocaleString("en-US");

function Card({ label, value, sub }) {
  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-4">
      <div className="text-xs uppercase tracking-wide text-neutral-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-neutral-500 mt-1">{sub}</div>}
    </div>
  );
}

export default function App() {
  const [summary, setSummary] = useState(null);
  const [daily, setDaily] = useState([]);
  const [sessions, setSessions] = useState([]);

  const load = useCallback(async () => {
    const [s, d, se] = await Promise.all([
      get("/api/summary"), get("/api/daily"), get("/api/sessions?limit=100"),
    ]);
    setSummary(s); setDaily(d); setSessions(se);
  }, []);

  useEffect(() => { load(); return subscribe(load); }, [load]);

  if (!summary) return <div className="p-8">Loading…</div>;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <h1 className="text-xl font-bold">Claude Code — Token Audit</h1>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Card label="Total cost (API-equiv)" value={usd(summary.total_cost)} />
        <Card label="Cache savings" value={usd(summary.cache_savings)}
              sub={`+ RTK ${usd(summary.rtk_savings)}`} />
        <Card label="Saved vs subscription" value={usd(summary.saved_vs_subscription)} />
        <Card label="Cache hit rate"
              value={`${(summary.cache_hit_rate * 100).toFixed(1)}%`} />
        <Card label="Input tokens" value={tok(summary.input_tokens)} />
        <Card label="Output tokens" value={tok(summary.output_tokens)}
              sub={`${summary.session_count} sessions`} />
      </div>

      <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-4">
        <div className="text-sm text-neutral-400 mb-2">Daily cost</div>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={daily}>
            <XAxis dataKey="date" tick={{ fill: "#888", fontSize: 11 }} />
            <YAxis tick={{ fill: "#888", fontSize: 11 }} />
            <Tooltip formatter={(v) => usd(v)}
                     contentStyle={{ background: "#111", border: "1px solid #333" }} />
            <Bar dataKey="cost" fill="#f59e0b" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="rounded-xl bg-neutral-900 border border-neutral-800 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-neutral-400 border-b border-neutral-800">
            <tr>
              <th className="text-left p-2">Session</th><th className="text-left p-2">Project</th>
              <th className="text-left p-2">Model(s)</th><th className="text-right p-2">Input</th>
              <th className="text-right p-2">Output</th><th className="text-right p-2">Cache read</th>
              <th className="text-right p-2">Cost</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.session_id} className="border-b border-neutral-800/50">
                <td className="p-2 font-mono text-xs">{(s.session_id || "").slice(0, 8)}</td>
                <td className="p-2 text-xs text-neutral-400">{s.project}</td>
                <td className="p-2 text-xs">{(s.models || []).join(", ")}</td>
                <td className="p-2 text-right">{tok(s.input)}</td>
                <td className="p-2 text-right">{tok(s.output)}</td>
                <td className="p-2 text-right">{tok(s.cache_read)}</td>
                <td className="p-2 text-right">{usd(s.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Build to verify it compiles**

Run: `cd token-audit/frontend && npm install && npm run build`
Expected: `dist/` created with `index.html` + assets, no build errors.

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add frontend/ && printf 'node_modules/\ndist/\n' > frontend/.gitignore
git add frontend/.gitignore
git commit -m "feat: react/vite/tailwind dashboard"
```

---

### Task 8: Runner script + README + full-flow verification

**Files:**
- Create: `token-audit/demo.sh`
- Create: `token-audit/README.md`
- Create: `token-audit/.gitignore`

**Interfaces:**
- Consumes: everything above.
- Produces: one-command startup; documented ports (backend 8010, frontend 5190).

- [ ] **Step 1: Write runner + gitignore + README**

```bash
# token-audit/demo.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv 2>/dev/null || true
. .venv/bin/activate
pip install -q -r requirements.txt

# backend (initial ingest runs on startup; watcher keeps it warm)
TOKEN_AUDIT_CONFIG=config.toml \
  uvicorn token_audit.server:app --host 127.0.0.1 --port 8010 &
BACK=$!
echo "backend  http://127.0.0.1:8010  (pid $BACK)"

# frontend dev server
( cd frontend && npm install --silent && npm run dev ) &
FRONT=$!
echo "frontend http://localhost:5190  (pid $FRONT)"

trap 'kill $BACK $FRONT 2>/dev/null || true' INT TERM
wait
```

```
# token-audit/.gitignore
.venv/
audit.db
__pycache__/
*.pyc
```

```markdown
<!-- token-audit/README.md -->
# Claude Code — Token Audit

Local realtime dashboard for Claude Code token usage, cache in/out, cost, and savings.
Reads `~/.claude/projects/**/*.jsonl` read-only into a local SQLite ledger.

## Run
```bash
./demo.sh
```
- Backend: http://127.0.0.1:8010 (API + SSE)
- Frontend: http://localhost:5190 (Vite dev)

## Tests
```bash
python -m pytest tests/ -v
```

## Config — `config.toml`
- `subscription_monthly_usd` — your flat plan fee, for the saved-vs-subscription figure.
- `projects_dir` — default `~/.claude/projects`.
- `db_path` — SQLite ledger, default `audit.db`.

## Design
See `docs/2026-07-02-token-audit-design.md`.
```

- [ ] **Step 2: Make runner executable**

Run: `cd token-audit && chmod +x demo.sh`
Expected: no output.

- [ ] **Step 3: Run the full backend test suite**

Run: `cd token-audit && python -m pytest tests/ -v`
Expected: PASS — all tests from Tasks 1–6 green.

- [ ] **Step 4: Smoke-test against real logs (read-only)**

Run:
```bash
cd token-audit && python - <<'PY'
from token_audit import config, db, ingest, metrics
cfg = config.load("config.toml")
conn = db.connect(cfg["db_path"])
n = ingest.ingest_all(conn, cfg["projects_dir"])
print("ingested rows:", n)
print("summary:", metrics.summary(conn, cfg["subscription_monthly_usd"]))
PY
```
Expected: prints a nonzero row count and a summary dict with `total_cost > 0`, `session_count > 0`. (This reads the user's real logs; it must not write to `~/.claude/`.)

- [ ] **Step 5: Commit**

```bash
cd token-audit && git add demo.sh README.md .gitignore
git commit -m "feat: runner, readme, full-flow verification"
```

---

## Self-Review notes

- **Spec coverage:** ingest (T3), SQLite store (T2), pricing (T1), metrics incl. all summary fields + daily + sessions + by-model (T4), RTK merge (T5), FastAPI endpoints + SSE + watcher (T6), React dashboard with stat cards/chart/table (T7), runner + README + real-log smoke test (T8). Non-goals (read-only, no egress, no cost persisted) enforced by design.
- **Type consistency:** `message_cost(model,input_tokens,cache_read,cache_create_5m,cache_create_1h,output_tokens)` signature identical across T1/T4; `_COLS` order in T3 matches the `messages` schema in T2; summary keys in T4 match frontend usage in T7.
- **Placeholders:** none — every code step is complete.
```
