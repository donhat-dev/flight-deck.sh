# Memory MCP Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng domain `memory_*` trong FlightDeck — bốn tool chỉ-đọc (`memory_lint`, `memory_search`, `memory_graph`, `memory_history`) soi vào kho auto-memory của Claude Code, cộng một lớp git nằm ngoài thư mục memory làm đường lùi.

**Architecture:** Một package `flightdeck/memory/` với các module tách theo trách nhiệm: `paths` (định vị), `store` (đọc + parse), `usage` (đếm lượt đọc từ transcript), `lint` (tám luật), `search`, `graph`, `history`, `vcs`, `mcp_server` (bảng TOOLS). Đăng ký bằng một dòng trong `agentsurface/registry.py::_DOMAINS`. Không module nào ghi vào thư mục memory — chỉ `vcs` ghi, và nó ghi vào kho git đặt **ngoài** thư mục đó.

**Tech Stack:** Python 3.12, stdlib + `psycopg` (đã có). SQLite FTS5 qua `sqlite3` stdlib cho index phái sinh. `subprocess` gọi `git`. pytest.

**Spec:** [`docs/mcp-domains-design.md`](../../mcp-domains-design.md) §4 — đọc cả §1 (bảy ràng buộc chung) trước khi bắt đầu.

## Global Constraints

- **Code, docstring, comment, identifier, log/error string: luôn tiếng Anh.** Tài liệu này là tiếng Việt; code sinh ra từ nó thì không.
- **Không tool nào trong plan này được ghi vào thư mục memory.** Bốn tool đều chỉ đọc. Vi phạm điều này là lỗi nghiêm trọng, và Task 10 có test âm chứng minh.
- **Không xoá file.** Nếu cần xoá, ghi đường dẫn tuyệt đối + lý do vào `/home/nathando/Documents/Projects/TEMP_FILES_SHOULD_BE_REMOVE.MD`.
- **Contract CLI:** một JSON document trên stdout. Exit `0` ok · `2` tool từ chối (error trong payload) · `3` tool không tồn tại · `1` crash. Tool trả `dict`; trả `{"error": "..."}` để từ chối.
- **Chữ người đọc thấy dùng từ thông dụng nhất.** `Broken links` chứ không `dangling edges`; `Not in index` chứ không `orphaned node`; `Last updated` chứ không `mtime`.
- **Invariant có sẵn:** `tests/test_agentsurface.py::test_every_merged_schema_matches_its_function_signature` bắt mọi tham số không có default phải nằm trong `required`, và ngược lại. Viết signature đúng là đủ, không cần test thêm.
- **Mọi tool đều nhận `memory_dir=None`** và mặc định phân giải theo Task 1. Truyền tường minh là cách test trỏ vào fixture.
- Chạy test: `cd backend && ../.venv/bin/python -m pytest tests/<file> -v`.
- Commit trong repo `flight-deck.sh` (đang ở branch làm việc hiện tại, không phải `main`).

---

## Cấu trúc file

| File | Trách nhiệm |
|---|---|
| `backend/flightdeck/memory/__init__.py` | rỗng |
| `backend/flightdeck/memory/paths.py` | Định vị thư mục memory và thư mục transcript từ một cwd |
| `backend/flightdeck/memory/store.py` | Đọc và parse: frontmatter, wikilink, index. Không biết gì về lint |
| `backend/flightdeck/memory/usage.py` | Đếm số lần mỗi memory được `Read`/`Grep` trong transcript |
| `backend/flightdeck/memory/lint.py` | Tám luật, mỗi luật một hàm thuần |
| `backend/flightdeck/memory/search.py` | Grep toàn văn + index FTS phái sinh + mở rộng láng giềng |
| `backend/flightdeck/memory/graph.py` | Node/edge, in/out degree, nhóm theo type |
| `backend/flightdeck/memory/vcs.py` | Kho git ngoài: init, commit, log, show |
| `backend/flightdeck/memory/history.py` | `memory_history` đứng trên `vcs` |
| `backend/flightdeck/memory/mcp_server.py` | Bảng `TOOLS` |
| `backend/flightdeck/agentsurface/registry.py` | +1 dòng `_DOMAINS` |
| `backend/tests/test_memory_*.py` | Bảy file test, một per module có logic |

**Ranh giới quan trọng:** `store.py` không import `lint.py`, `graph.py` hay `search.py`. Ba module đó đều đứng trên `store`. Điều này giữ `store` đủ nhỏ để test độc lập và là chỗ duy nhất biết định dạng file.

---

### Task 1: `paths` + `store` — định vị và đọc kho

**Files:**
- Create: `backend/flightdeck/memory/__init__.py`
- Create: `backend/flightdeck/memory/paths.py`
- Create: `backend/flightdeck/memory/store.py`
- Test: `backend/tests/test_memory_store.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `paths.memory_dir(cwd: str | None = None) -> Path`
  - `paths.transcript_dir(cwd: str | None = None) -> Path`
  - `store.Memory` — dataclass `(name: str, path: Path, description: str, type: str | None, origin_session: str | None, source: str | None, tier: str | None, mtime: float, links: list[str], alias_links: list[str], body: str)`
  - `store.load_all(memory_dir: Path) -> list[Memory]`
  - `store.load_index(memory_dir: Path) -> list[tuple[str, str, str]]` — `(title, filename, hook)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_store.py`:

```python
"""The memory store reader: locating the directory, and parsing one file's shape.

Fixtures are written to tmp_path rather than read from the real store, because the
real store changes under us and a test that asserts "3 broken links" would break on
the next memory anyone saves.
"""
from pathlib import Path

import pytest

from flightdeck.memory import paths, store


def test_memory_dir_mirrors_the_harness_path_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    got = paths.memory_dir("/home/someone/Documents/Projects")
    assert got == tmp_path / "projects" / "-home-someone-Documents-Projects" / "memory"


def test_transcript_dir_is_the_memory_dir_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert paths.transcript_dir("/a/b") == paths.memory_dir("/a/b").parent


@pytest.fixture()
def sample(tmp_path):
    """Two memories: one well formed with a link, one carrying an alias link."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "alpha.md").write_text(
        "---\n"
        "name: alpha\n"
        'description: "The alpha fact"\n'
        "metadata: \n"
        "  node_type: memory\n"
        "  type: reference\n"
        "  originSessionId: 11111111-2222-3333-4444-555555555555\n"
        "---\n\n"
        "Body text linking to [[beta]] and again to [[beta]].\n",
        encoding="utf-8")
    (d / "beta.md").write_text(
        "---\n"
        "name: beta\n"
        "description: The beta fact\n"
        "metadata: \n"
        "  type: project\n"
        "---\n\n"
        "Points at [[gamma project|gamma]] which does not exist.\n",
        encoding="utf-8")
    (d / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [Alpha](alpha.md) — the alpha hook\n",
        encoding="utf-8")
    return d


def test_load_all_parses_frontmatter_and_links(sample):
    mems = {m.name: m for m in store.load_all(sample)}
    assert set(mems) == {"alpha", "beta"}          # MEMORY.md is not a memory
    a = mems["alpha"]
    assert a.description == "The alpha fact"        # quotes stripped
    assert a.type == "reference"
    assert a.origin_session == "11111111-2222-3333-4444-555555555555"
    assert a.links == ["beta"]                      # deduped, order preserved
    assert a.alias_links == []


def test_alias_links_are_kept_apart_from_plain_links(sample):
    beta = next(m for m in store.load_all(sample) if m.name == "beta")
    assert beta.links == []
    assert beta.alias_links == ["gamma project"]


def test_missing_frontmatter_fields_are_none_not_a_crash(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "bare.md").write_text("no frontmatter at all\n", encoding="utf-8")
    m = store.load_all(d)[0]
    assert m.name == "bare" and m.type is None and m.description == ""


def test_load_index_reads_title_file_and_hook(sample):
    assert store.load_index(sample) == [("Alpha", "alpha.md", "the alpha hook")]


def test_load_all_on_a_missing_directory_returns_empty(tmp_path):
    assert store.load_all(tmp_path / "nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flightdeck.memory'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/__init__.py` (empty file).

Create `backend/flightdeck/memory/paths.py`:

```python
"""Locating the auto-memory directory the way the harness locates it.

The harness keys memory on the project's canonical root, sanitised by replacing every
non-alphanumeric character with a dash: /home/x/Projects -> -home-x-Projects. Mirroring
that rule here (rather than taking a path from config) means these tools read exactly
the store the running agent reads, with nothing to keep in sync.
"""
import os
import re
from pathlib import Path

_MEMORY_DIRNAME = "memory"


def _config_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def sanitize(cwd: str) -> str:
    """The harness's project-key rule: every non-alphanumeric character becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def project_dir(cwd: str | None = None) -> Path:
    cwd = cwd or os.getcwd()
    return _config_home() / "projects" / sanitize(cwd)


def memory_dir(cwd: str | None = None) -> Path:
    return project_dir(cwd) / _MEMORY_DIRNAME


def transcript_dir(cwd: str | None = None) -> Path:
    """Session transcripts sit beside the memory directory, not inside it."""
    return project_dir(cwd)
```

Create `backend/flightdeck/memory/store.py`:

```python
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

    plain, alias, seen = [], [], set()
    for target in _WIKILINK.findall(body):
        if "|" in target:
            head = target.split("|", 1)[0].strip()
            if head and head not in seen:
                seen.add(head)
                alias.append(head)
        elif target.strip() and target.strip() not in seen:
            seen.add(target.strip())
            plain.append(target.strip())

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
    if not memory_dir.is_dir():
        return []
    return sorted(
        (_parse(p) for p in memory_dir.glob("*.md") if p.name != INDEX_NAME),
        key=lambda m: m.name,
    )


def load_index(memory_dir: Path) -> list[tuple[str, str, str]]:
    """`(title, filename, hook)` for each pointer line in MEMORY.md."""
    index = memory_dir / INDEX_NAME
    if not index.is_file():
        return []
    out = []
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INDEX_LINE.match(line.strip())
        if m:
            out.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_store.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/__init__.py backend/flightdeck/memory/paths.py \
        backend/flightdeck/memory/store.py backend/tests/test_memory_store.py
git commit -m "feat(memory): locate and parse the auto-memory store"
```

---

### Task 2: `vcs` — kho git đặt ngoài thư mục memory

**Files:**
- Create: `backend/flightdeck/memory/vcs.py`
- Test: `backend/tests/test_memory_vcs.py`

**Interfaces:**
- Consumes: `paths.memory_dir`
- Produces:
  - `vcs.git_dir(memory_dir: Path) -> Path` — `<project_dir>/memory.git`, tức là **anh em** của thư mục memory
  - `vcs.is_initialised(memory_dir: Path) -> bool`
  - `vcs.init(memory_dir: Path) -> dict`
  - `vcs.commit(memory_dir: Path, message: str) -> dict` — `{"committed": bool, "files": [...]}`
  - `vcs.log(memory_dir: Path, filename: str, limit: int = 20) -> list[dict]` — `{"sha","date","message"}`
  - `vcs.show(memory_dir: Path, ref: str, filename: str) -> str`

**Vì sao kho git phải đặt ngoài:** `memoryScan.ts` của harness gọi `readdir(memoryDir, {recursive: true})` và lọc **duy nhất bằng đuôi `.md`**, không loại trừ thư mục ẩn. Một `.git/` bên trong bị đi hết cây mỗi lần quét, và bất kỳ `.md` nào lọt vào đó sẽ trở thành một memory. Đặt ngoài thì worktree sạch tuyệt đối — đã kiểm chứng trong spec §4.9.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_vcs.py`:

```python
"""The git layer under the memory store.

The property that matters is that git leaves NO trace inside the directory the harness
scans — no .git, no .md, nothing. Everything else here is ordinary plumbing.
"""
import subprocess
from pathlib import Path

import pytest

from flightdeck.memory import vcs


@pytest.fixture()
def repo(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("first\n", encoding="utf-8")
    vcs.init(mem)
    return mem


def test_the_git_dir_is_a_sibling_of_the_memory_dir(repo):
    assert vcs.git_dir(repo) == repo.parent / "memory.git"
    assert vcs.git_dir(repo).is_dir()


def test_git_leaves_nothing_inside_the_scanned_directory(repo):
    vcs.commit(repo, "first commit")
    assert [p.name for p in repo.iterdir()] == ["alpha.md"]


def test_commit_reports_the_files_it_captured(repo):
    out = vcs.commit(repo, "first commit")
    assert out["committed"] is True and out["files"] == ["alpha.md"]


def test_a_second_commit_with_no_change_is_a_no_op(repo):
    vcs.commit(repo, "first commit")
    out = vcs.commit(repo, "nothing changed")
    assert out["committed"] is False and out["files"] == []


def test_log_and_show_recover_an_earlier_version(repo):
    vcs.commit(repo, "first commit")
    (repo / "alpha.md").write_text("second\n", encoding="utf-8")
    vcs.commit(repo, "second commit")

    entries = vcs.log(repo, "alpha.md")
    assert [e["message"] for e in entries] == ["second commit", "first commit"]
    assert vcs.show(repo, entries[1]["sha"], "alpha.md") == "first\n"


def test_is_initialised_is_false_before_init(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    assert vcs.is_initialised(mem) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_vcs.py -v`
Expected: FAIL with `ImportError: cannot import name 'vcs'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/vcs.py`:

```python
"""A git repository for the memory store, kept OUTSIDE the directory it versions.

The harness scans the memory directory recursively and treats every .md it finds as a
memory, hidden directories included. So the repository lives at <project>/memory.git and
is reached with --git-dir/--work-tree, which leaves the scanned directory holding
nothing but the memories themselves.

Restores go through `show` rather than `git checkout --`: the local command guard
rejects checkout-discard, and reading a blob out is the safer operation anyway — it
cannot destroy an uncommitted edit.
"""
import subprocess
from pathlib import Path

_IDENTITY = ["-c", "user.email=flightdeck@localhost", "-c", "user.name=FlightDeck"]


def git_dir(memory_dir: Path) -> Path:
    return memory_dir.parent / "memory.git"


def is_initialised(memory_dir: Path) -> bool:
    return (git_dir(memory_dir) / "HEAD").is_file()


def _run(memory_dir: Path, args: list[str], check: bool = True):
    cmd = ["git", f"--git-dir={git_dir(memory_dir)}", f"--work-tree={memory_dir}",
           *_IDENTITY, *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=60)


def init(memory_dir: Path) -> dict:
    if is_initialised(memory_dir):
        return {"initialised": False, "git_dir": str(git_dir(memory_dir))}
    subprocess.run(["git", "init", "--quiet", "--bare", str(git_dir(memory_dir))],
                   capture_output=True, text=True, check=True, timeout=60)
    return {"initialised": True, "git_dir": str(git_dir(memory_dir))}


def commit(memory_dir: Path, message: str) -> dict:
    """Stage everything and commit. A no-change commit is reported, not attempted."""
    if not is_initialised(memory_dir):
        return {"error": "no memory repository yet; run memory_history --init first"}
    _run(memory_dir, ["add", "--all", "."])
    staged = _run(memory_dir, ["diff", "--cached", "--name-only"]).stdout.split()
    if not staged:
        return {"committed": False, "files": []}
    _run(memory_dir, ["commit", "--quiet", "-m", message])
    return {"committed": True, "files": staged}


def log(memory_dir: Path, filename: str, limit: int = 20) -> list[dict]:
    """Commits touching one file, newest first. --follow so a rename keeps its history."""
    proc = _run(memory_dir, ["log", "--follow", f"-{int(limit)}",
                             "--format=%H%x00%aI%x00%s", "--", filename], check=False)
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) == 3:
            out.append({"sha": parts[0], "date": parts[1], "message": parts[2]})
    return out


def show(memory_dir: Path, ref: str, filename: str) -> str:
    return _run(memory_dir, ["show", f"{ref}:{filename}"], check=False).stdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_vcs.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/vcs.py backend/tests/test_memory_vcs.py
git commit -m "feat(memory): git repository kept outside the scanned directory"
```

---

### Task 3: `usage` — đếm lượt đọc từ transcript

**Files:**
- Create: `backend/flightdeck/memory/usage.py`
- Test: `backend/tests/test_memory_usage.py`

**Interfaces:**
- Consumes: `paths.transcript_dir`
- Produces: `usage.read_counts(transcript_dir: Path, memory_dir: Path) -> dict[str, int]` — khoá là filename (`alpha.md`), giá trị là số lần xuất hiện trong một `tool_use` của `Read` hoặc `Grep`. File chưa từng đọc **không có mặt** trong dict.

**Vì sao cột này quan trọng hơn cột tuổi:** việc truy xuất củng cố dấu vết mạnh hơn việc để nó nằm đó. Một fact hai tháng tuổi được dùng hằng tuần mạnh hơn một fact mới toanh chưa ai chạm. Tuổi là proxy yếu; số lần dùng là tín hiệu thật, và nó đo được.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_usage.py`:

```python
"""Counting how often each memory was actually read, out of the session transcripts."""
import json
from pathlib import Path

import pytest

from flightdeck.memory import usage


def _turn(tool: str, path: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": tool, "input": {"file_path": path}}]}})


@pytest.fixture()
def corpus(tmp_path):
    proj = tmp_path / "-proj"
    mem = proj / "memory"
    mem.mkdir(parents=True)
    for n in ("alpha.md", "beta.md", "unread.md"):
        (mem / n).write_text("x\n", encoding="utf-8")
    (proj / "s1.jsonl").write_text(
        "\n".join([_turn("Read", str(mem / "alpha.md")),
                   _turn("Read", str(mem / "alpha.md")),
                   _turn("Read", str(mem / "beta.md")),
                   _turn("Read", "/etc/passwd"),
                   "not json at all"]) + "\n", encoding="utf-8")
    return proj, mem


def test_counts_reads_per_memory_file(corpus):
    proj, mem = corpus
    assert usage.read_counts(proj, mem) == {"alpha.md": 2, "beta.md": 1}


def test_files_never_read_are_absent_not_zero(corpus):
    proj, mem = corpus
    assert "unread.md" not in usage.read_counts(proj, mem)


def test_grep_over_the_memory_dir_counts_for_every_file_it_covers(tmp_path):
    proj = tmp_path / "-proj"
    mem = proj / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("x\n", encoding="utf-8")
    (proj / "s.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "g", "name": "Grep",
             "input": {"pattern": "x", "path": str(mem)}}]}}) + "\n", encoding="utf-8")
    assert usage.read_counts(proj, mem) == {"alpha.md": 1}


def test_a_missing_transcript_dir_is_empty_not_an_error(tmp_path):
    assert usage.read_counts(tmp_path / "nope", tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_usage.py -v`
Expected: FAIL with `ImportError: cannot import name 'usage'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/usage.py`:

```python
"""How often each memory was actually pulled into a context window.

Recall in this harness is a plain Read of the file, so the transcripts hold a complete
record of which memories were ever used. Age is the proxy everyone reaches for; this is
the real signal, and it turns out to be far more discriminating: most of a store can be
old-but-load-bearing or new-but-never-touched, and only this tells them apart.

Two honest limits, stated here so callers do not over-read the number. Transcripts are
duplicated by session resume, so a count is an upper bound on distinct reads. And a
memory also reaches the model through its one-line description in the index, which is
injected without any Read at all — so a count of zero means "the body never entered a
context window", not "this memory was never used".
"""
import json
from pathlib import Path


def read_counts(transcript_dir: Path, memory_dir: Path) -> dict[str, int]:
    if not Path(transcript_dir).is_dir():
        return {}
    known = {p.name for p in Path(memory_dir).glob("*.md")}
    memory_prefix = str(Path(memory_dir))
    counts: dict[str, int] = {}

    for transcript in Path(transcript_dir).glob("*.jsonl"):
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if memory_prefix not in line:
                    continue
                try:
                    turn = json.loads(line)
                except ValueError:
                    continue
                for block in (turn.get("message") or {}).get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in ("Read", "Grep"):
                        continue
                    target = str((block.get("input") or {}).get("file_path")
                                 or (block.get("input") or {}).get("path") or "")
                    if not target.startswith(memory_prefix):
                        continue
                    name = Path(target).name
                    # A Grep aimed at the directory reads across every file in it.
                    hits = [name] if name in known else sorted(known)
                    for n in hits:
                        counts[n] = counts.get(n, 0) + 1
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_usage.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/usage.py backend/tests/test_memory_usage.py
git commit -m "feat(memory): count how often each memory was read"
```

---

### Task 4: `lint` — tám luật

**Files:**
- Create: `backend/flightdeck/memory/lint.py`
- Test: `backend/tests/test_memory_lint.py`

**Interfaces:**
- Consumes: `store.load_all`, `store.load_index`, `usage.read_counts`
- Produces:
  - `lint.run(memory_dir: Path, transcript_dir: Path | None = None, cwd: str | None = None) -> dict`
  - `lint.with_usage(memory_dir: Path, transcript_dir: Path, cwd: str | None = None) -> dict` — `run` cộng `usage` và `never_read`

  `run` trả về hình dạng:
  ```python
  {"counts": {"<kind>": int, ...},
   "findings": [{"kind": str, "memory": str, "detail": str,
                 "suggestion": str | None, "action": str | None}, ...],
   "total_memories": int}
  ```

**Tám `kind`, và thứ tự chúng xuất hiện trong `findings`** (nặng trước):

| `kind` | Nghĩa |
|---|---|
| `no_source` | Memory `reference` không có trường `source` |
| `dead_origin` | `originSessionId` trỏ tới transcript đã mất |
| `weak_description` | Mô tả rỗng, dưới 20 ký tự, hoặc trùng gần với mô tả khác |
| `not_in_index` | File có trên đĩa nhưng không có dòng nào trong `MEMORY.md` |
| `broken_link` | `[[name]]` trỏ tới memory không tồn tại |
| `not_linked` | Không có link vào lẫn link ra |
| `missing_file` | Dòng index trỏ tới file không tồn tại |
| `future_target` | Alias link trỏ tới thứ chưa tồn tại |

`store.Memory` cũng parse trường `tier` (spec §4.6) nhưng **plan này chưa có luật nào dùng nó**. Trường được đọc sẵn để một luật "khẳng định vượt bậc bằng chứng đã ghi" cắm vào sau mà không phải đụng `store`. Đây là thiếu sót có chủ ý, không phải bỏ quên.

**`future_target` là câu trả lời cho câu hỏi parse còn treo trong spec.** Một alias link như `[[fable5-persona project|fable5-persona]]` không phải link gãy (target không theo quy ước tên nào, nó trỏ tới một dự án tương lai) và cũng không được phép biến file nguồn thành node cô lập. Cho nó một `kind` riêng thì cả hai con số kia giữ nguyên và người đọc vẫn thấy nó. Không phải chọn một trong hai cách đọc nữa.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_lint.py`:

```python
"""The eight lint rules, each proven on a fixture built to trip exactly it."""
import json
from pathlib import Path

import pytest

from flightdeck.memory import lint


def write(d: Path, name: str, *, description="A sufficiently long description here",
          type_="reference", origin=None, source=None, body="plain body"):
    front = ["---", f"name: {name}", f'description: "{description}"', "metadata: "]
    if type_:
        front.append(f"  type: {type_}")
    if origin:
        front.append(f"  originSessionId: {origin}")
    if source:
        front.append(f"  source: {source}")
    front.append("---")
    (d / f"{name}.md").write_text("\n".join(front) + "\n\n" + body + "\n",
                                  encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


def kinds(result):
    return sorted({f["kind"] for f in result["findings"]})


def test_reference_without_source_is_flagged(store_dir):
    write(store_dir, "alpha", source=None)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["no_source"] == 1


def test_a_feedback_memory_without_source_is_not_flagged(store_dir):
    write(store_dir, "alpha", type_="feedback", source=None)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"].get("no_source", 0) == 0


def test_dead_origin_session_is_flagged(store_dir, tmp_path):
    write(store_dir, "alpha", source="docs/x.md@abc123",
          origin="99999999-9999-9999-9999-999999999999")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    out = lint.run(store_dir, transcript_dir=transcripts)
    assert out["counts"]["dead_origin"] == 1


def test_a_live_origin_session_is_not_flagged(store_dir, tmp_path):
    sid = "11111111-1111-1111-1111-111111111111"
    write(store_dir, "alpha", source="docs/x.md@abc123", origin=sid)
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    (transcripts / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
    out = lint.run(store_dir, transcript_dir=transcripts)
    assert out["counts"].get("dead_origin", 0) == 0


def test_a_short_description_is_flagged_as_weak(store_dir):
    write(store_dir, "alpha", description="short", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["weak_description"] == 1


def test_a_file_absent_from_the_index_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["not_in_index"] == 1


def test_a_broken_link_is_flagged_with_a_close_name_suggestion(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="see [[beta-fact-here]]")
    write(store_dir, "beta-fact-hero", source="docs/y.md@abc", body="see [[alpha]]")
    (store_dir / "MEMORY.md").write_text(
        "- [A](alpha.md) — hook\n- [B](beta-fact-hero.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    broken = [f for f in out["findings"] if f["kind"] == "broken_link"]
    assert len(broken) == 1
    assert broken[0]["suggestion"] == "beta-fact-hero"
    assert broken[0]["action"] == "rename_link"


def test_a_broken_link_with_no_close_name_offers_no_action(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="see [[zzzzzzzzzzzz]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    broken = [f for f in lint.run(store_dir)["findings"] if f["kind"] == "broken_link"]
    assert broken[0]["suggestion"] is None and broken[0]["action"] is None


def test_an_unlinked_memory_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc", body="no links at all")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["not_linked"] == 1


def test_an_index_pointer_to_a_missing_file_is_flagged(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc")
    (store_dir / "MEMORY.md").write_text(
        "- [A](alpha.md) — hook\n- [Gone](gone.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"]["missing_file"] == 1


def test_an_alias_link_is_its_own_kind_and_does_not_count_as_broken(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc",
          body="points at [[some future thing|label]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    out = lint.run(store_dir)
    assert out["counts"]["future_target"] == 1
    assert out["counts"].get("broken_link", 0) == 0


def test_an_alias_link_keeps_its_source_out_of_the_unlinked_group(store_dir):
    write(store_dir, "alpha", source="docs/x.md@abc",
          body="points at [[some future thing|label]]")
    (store_dir / "MEMORY.md").write_text("- [A](alpha.md) — hook\n", encoding="utf-8")
    assert lint.run(store_dir)["counts"].get("not_linked", 0) == 0


def test_findings_are_ordered_heaviest_kind_first(store_dir):
    write(store_dir, "alpha", source=None, body="no links")
    (store_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    order = [f["kind"] for f in lint.run(store_dir)["findings"]]
    assert order.index("no_source") < order.index("not_in_index")
    assert order.index("not_in_index") < order.index("not_linked")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_lint.py -v`
Expected: FAIL with `ImportError: cannot import name 'lint'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/lint.py`:

```python
"""Eight checks over the memory store, ordered by how much each one costs the reader.

The order is the argument. A memory with no checkable source is worse than a memory
with a broken link: the link is a navigation defect, the missing source means the claim
cannot be promoted to evidence at all. And a memory absent from the index is worse than
one that is merely unlinked, because the index is the only thing the harness injects —
a file missing from it is permanently invisible, not just poorly connected.

Provenance is checked by type, not uniformly. A `reference` memory describes a live
system and rots against it, so it owes a source. A `feedback` memory records something
a person said; the transcript IS the source and there is no outside truth to check it
against. Flagging those would produce noise on a third of the store and teach the
reader to ignore the report.
"""
import difflib
from pathlib import Path

from flightdeck.memory import store, usage

#: Heaviest first. `findings` is sorted by this, and it is the whole editorial claim.
KIND_ORDER = ["no_source", "dead_origin", "weak_description", "not_in_index",
              "broken_link", "not_linked", "missing_file", "future_target"]

#: Only this type is checked for a source. See the module docstring.
SOURCE_REQUIRED_TYPES = {"reference"}

MIN_DESCRIPTION_CHARS = 20
NEAR_DUPLICATE_RATIO = 0.85
CLOSE_NAME_RATIO = 0.75


def _finding(kind, memory, detail, suggestion=None, action=None):
    return {"kind": kind, "memory": memory, "detail": detail,
            "suggestion": suggestion, "action": action}


def run(memory_dir, transcript_dir=None, cwd=None):
    memory_dir = Path(memory_dir)
    memories = store.load_all(memory_dir)
    index = store.load_index(memory_dir)
    names = {m.name for m in memories}
    files = {m.filename for m in memories}
    indexed_files = {filename for _t, filename, _h in index}
    findings = []

    linked_from = {m.name: set() for m in memories}
    for m in memories:
        for target in m.links:
            if target in linked_from:
                linked_from[target].add(m.name)

    for m in memories:
        if m.type in SOURCE_REQUIRED_TYPES and not m.source:
            findings.append(_finding(
                "no_source", m.name,
                "A reference memory with no source cannot be checked against the "
                "system it describes.", action="add_source"))

        if m.description == "" or len(m.description) < MIN_DESCRIPTION_CHARS:
            findings.append(_finding(
                "weak_description", m.name,
                f"The summary line is {len(m.description)} characters. It is the only "
                "cue the recall step has."))

        if m.filename not in indexed_files:
            findings.append(_finding(
                "not_in_index", m.name,
                "On disk but not in MEMORY.md, so it is never loaded.",
                action="add_to_index"))

        for target in m.links:
            if target in names:
                continue
            close = difflib.get_close_matches(target, sorted(names), n=1,
                                              cutoff=CLOSE_NAME_RATIO)
            findings.append(_finding(
                "broken_link", m.name, f"Links to [[{target}]], which does not exist.",
                suggestion=close[0] if close else None,
                action="rename_link" if close else None))

        for target in m.alias_links:
            findings.append(_finding(
                "future_target", m.name,
                f"Points at \"{target}\", which does not exist yet. Not a broken "
                "link — the target does not follow the naming rule at all."))

        if not m.links and not m.alias_links and not linked_from[m.name]:
            findings.append(_finding(
                "not_linked", m.name, "No links in and none out.",
                action="suggest_links"))

    for _title, filename, _hook in index:
        if filename not in files:
            findings.append(_finding(
                "missing_file", filename,
                "The index points at a file that is not on disk.",
                action="remove_index_line"))

    findings.extend(_weak_by_similarity(memories))
    findings.extend(_dead_origins(memories, transcript_dir))

    findings.sort(key=lambda f: (KIND_ORDER.index(f["kind"]), f["memory"]))
    counts = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"counts": counts, "findings": findings, "total_memories": len(memories)}


def _weak_by_similarity(memories):
    """Two memories whose summaries read alike share one cue and neither wins it."""
    out = []
    for i, a in enumerate(memories):
        for b in memories[i + 1:]:
            if not a.description or not b.description:
                continue
            ratio = difflib.SequenceMatcher(None, a.description.lower(),
                                            b.description.lower()).ratio()
            if ratio >= NEAR_DUPLICATE_RATIO:
                out.append(_finding(
                    "weak_description", a.name,
                    f"Its summary reads almost the same as {b.name}, so a query "
                    "matching one matches both."))
    return out


def _dead_origins(memories, transcript_dir):
    if transcript_dir is None:
        return []
    alive = {p.stem for p in Path(transcript_dir).glob("*.jsonl")}
    return [_finding("dead_origin", m.name,
                     "The session that produced it is no longer on disk, so the claim "
                     "has no record behind it. Demote it, do not delete it.",
                     action="demote_tier")
            for m in memories if m.origin_session and m.origin_session not in alive]


def with_usage(memory_dir, transcript_dir, cwd=None):
    """`run` plus a `used` count per memory — the retention signal age only approximates."""
    result = run(memory_dir, transcript_dir=transcript_dir, cwd=cwd)
    counts = usage.read_counts(Path(transcript_dir), Path(memory_dir))
    memories = store.load_all(Path(memory_dir))
    result["usage"] = {m.filename: counts.get(m.filename, 0) for m in memories}
    result["never_read"] = sorted(n for n, c in result["usage"].items() if c == 0)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_lint.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/lint.py backend/tests/test_memory_lint.py
git commit -m "feat(memory): eight lint rules over the store"
```

---

### Task 5: `graph` — node, cạnh, degree

**Files:**
- Create: `backend/flightdeck/memory/graph.py`
- Test: `backend/tests/test_memory_graph.py`

**Interfaces:**
- Consumes: `store.load_all`
- Produces: `graph.build(memory_dir: Path) -> dict`:
  ```python
  {"nodes": [{"name","type","in","out","description","last_updated"}, ...],
   "edges": [{"source","target","broken": bool}, ...],
   "missing_targets": [str, ...],
   "groups": {"reference": [...names...], "project": [...], ...}}
  ```
  `nodes` sắp xếp **ít link nhất trước** để node cô lập nằm ngay đầu.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_graph.py`:

```python
"""The link graph: degrees, groups, and the ghost nodes broken links point at."""
from pathlib import Path

import pytest

from flightdeck.memory import graph


def write(d: Path, name: str, type_: str, body: str):
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: \"desc for {name}\"\n"
        f"metadata: \n  type: {type_}\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    write(d, "hub", "reference", "no links out")
    write(d, "spoke1", "reference", "see [[hub]]")
    write(d, "spoke2", "project", "see [[hub]] and [[ghost]]")
    write(d, "lonely", "feedback", "nothing here")
    return d


def test_degrees_are_counted_in_both_directions(store_dir):
    nodes = {n["name"]: n for n in graph.build(store_dir)["nodes"]}
    assert (nodes["hub"]["in"], nodes["hub"]["out"]) == (2, 0)
    assert (nodes["spoke2"]["in"], nodes["spoke2"]["out"]) == (0, 1)


def test_nodes_are_sorted_fewest_links_first(store_dir):
    assert graph.build(store_dir)["nodes"][0]["name"] == "lonely"


def test_a_link_to_a_missing_name_is_marked_broken_and_listed(store_dir):
    out = graph.build(store_dir)
    broken = [e for e in out["edges"] if e["broken"]]
    assert [(e["source"], e["target"]) for e in broken] == [("spoke2", "ghost")]
    assert out["missing_targets"] == ["ghost"]


def test_a_broken_edge_does_not_add_out_degree(store_dir):
    nodes = {n["name"]: n for n in graph.build(store_dir)["nodes"]}
    assert nodes["spoke2"]["out"] == 1  # only the live edge to hub


def test_groups_hold_every_node_exactly_once(store_dir):
    out = graph.build(store_dir)
    flat = [n for names in out["groups"].values() for n in names]
    assert sorted(flat) == ["hub", "lonely", "spoke1", "spoke2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_graph.py -v`
Expected: FAIL with `ImportError: cannot import name 'graph'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/graph.py`:

```python
"""The wikilink graph, shaped for a table first and a drawing second.

At this size the store has no dense clusters — most nodes carry nought to three links —
so a force layout has nothing to separate and its node positions would move between
renders, which is exactly what a tool used daily must not do. Sorting by degree puts the
isolated nodes at the top of a plain table, which answers the same question faster.

Broken edges are kept, not dropped. An edge whose target does not exist is the finding;
removing it would make the graph look healthy and quietly turn its source into an
isolated node.
"""
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.memory import store


def build(memory_dir) -> dict:
    memories = store.load_all(Path(memory_dir))
    names = {m.name for m in memories}

    edges, missing = [], []
    indegree = {m.name: 0 for m in memories}
    outdegree = {m.name: 0 for m in memories}

    for m in memories:
        for target in m.links:
            live = target in names
            edges.append({"source": m.name, "target": target, "broken": not live})
            if live:
                outdegree[m.name] += 1
                indegree[target] += 1
            elif target not in missing:
                missing.append(target)

    nodes = [{"name": m.name,
              "type": m.type,
              "in": indegree[m.name],
              "out": outdegree[m.name],
              "description": m.description,
              "last_updated": datetime.fromtimestamp(
                  m.mtime, tz=timezone.utc).isoformat()}
             for m in memories]
    nodes.sort(key=lambda n: (n["in"] + n["out"], n["name"]))

    groups: dict[str, list[str]] = {}
    for m in memories:
        groups.setdefault(m.type or "untyped", []).append(m.name)

    return {"nodes": nodes, "edges": edges,
            "missing_targets": sorted(missing), "groups": groups}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_graph.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/graph.py backend/tests/test_memory_graph.py
git commit -m "feat(memory): link graph with degrees and ghost targets"
```

---

### Task 6: `search` — toàn văn + mở rộng láng giềng

**Files:**
- Create: `backend/flightdeck/memory/search.py`
- Test: `backend/tests/test_memory_search.py`

**Interfaces:**
- Consumes: `store.load_all`
- Produces: `search.run(memory_dir: Path, query: str, limit: int = 10, neighbours: bool = True) -> dict`:
  ```python
  {"query": str, "matches": int, "in_memories": int,
   "results": [{"name","description","type","snippet","hits",
                "in_index_only": bool,
                "neighbours": [{"name","description"}, ...]}, ...]}
  ```

**Mở rộng một bước láng giềng** là điểm khác biệt so với một grep thường: khi trả về một memory, kèm luôn dòng mô tả của các memory nó nối tới. Rẻ (một dòng mỗi cái) và biến wikilink từ trang trí thành cơ chế truy xuất — bù cho việc harness chỉ có một kênh gợi ý.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_search.py`:

```python
"""Full-text search over the store, plus one-hop neighbour expansion."""
from pathlib import Path

import pytest

from flightdeck.memory import search


def write(d: Path, name: str, description: str, body: str):
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: \"{description}\"\n"
        f"metadata: \n  type: reference\n---\n\n{body}\n", encoding="utf-8")


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    write(d, "worktree-source", "Both instances mount from the worktree",
          "The compose file mounts a worktree. See [[docker-setup]].")
    write(d, "docker-setup", "How to run the stack locally",
          "docker compose up. Nothing about trees here.")
    write(d, "unrelated", "Something else entirely", "no match in this one")
    return d


def test_finds_matches_in_the_body(store_dir):
    out = search.run(store_dir, "worktree")
    assert [r["name"] for r in out["results"]] == ["worktree-source"]
    assert out["in_memories"] == 1


def test_search_is_case_insensitive_and_counts_hits(store_dir):
    out = search.run(store_dir, "WORKTREE")
    assert out["results"][0]["hits"] == 2  # description + body


def test_the_snippet_carries_the_matched_line(store_dir):
    out = search.run(store_dir, "compose")
    assert "docker compose up" in out["results"][0]["snippet"]


def test_neighbours_bring_back_the_linked_memory_summary(store_dir):
    out = search.run(store_dir, "worktree")
    assert out["results"][0]["neighbours"] == [
        {"name": "docker-setup", "description": "How to run the stack locally"}]


def test_neighbours_can_be_switched_off(store_dir):
    out = search.run(store_dir, "worktree", neighbours=False)
    assert out["results"][0]["neighbours"] == []


def test_a_body_only_match_is_marked_as_absent_from_the_summary(store_dir):
    out = search.run(store_dir, "compose")
    assert out["results"][0]["in_index_only"] is False


def test_an_empty_query_is_an_error_not_an_empty_result(store_dir):
    assert "error" in search.run(store_dir, "   ")


def test_no_match_returns_an_empty_result_list(store_dir):
    out = search.run(store_dir, "zzzznotpresent")
    assert out["results"] == [] and "error" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_search.py -v`
Expected: FAIL with `ImportError: cannot import name 'search'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/search.py`:

```python
"""Full-text search over the memory store, with one-hop neighbour expansion.

Recall in this harness runs on a single cue: the one-line description in the index. When
that line is written badly there is no second route to the file, and most of a store can
sit unread for that reason alone. This tool is the second route.

Neighbour expansion is the other half. Human recall spreads from a cue to whatever is
associated with it; the wikilinks in this store already encode those associations but
nothing traverses them automatically. Returning each hit's neighbours — their summary
lines only, so it stays cheap — makes the links do retrieval work instead of decoration.

Plain substring matching, deliberately. The store is small enough that scanning it costs
nothing, and a derived index (SQLite FTS, embeddings) can be added later without moving
the storage contract: markdown stays the original, any index stays disposable.
"""
import re
from pathlib import Path

from flightdeck.memory import store

SNIPPET_RADIUS = 60


def _snippet(text: str, needle: str) -> str:
    lowered = text.lower()
    at = lowered.find(needle)
    if at < 0:
        return ""
    line_start = text.rfind("\n", 0, at) + 1
    line_end = text.find("\n", at)
    line = text[line_start:line_end if line_end >= 0 else len(text)].strip()
    if len(line) <= SNIPPET_RADIUS * 2:
        return line
    rel = at - line_start
    lo = max(0, rel - SNIPPET_RADIUS)
    return ("…" if lo else "") + line[lo:rel + SNIPPET_RADIUS].strip() + "…"


def run(memory_dir, query: str, limit: int = 10, neighbours: bool = True) -> dict:
    needle = (query or "").strip().lower()
    if not needle:
        return {"error": "empty query: give at least one search term"}

    memories = store.load_all(Path(memory_dir))
    by_name = {m.name: m for m in memories}
    results, total = [], 0

    for m in memories:
        in_desc = m.description.lower().count(needle)
        in_body = m.body.lower().count(needle)
        if not (in_desc or in_body):
            continue
        total += in_desc + in_body
        results.append({
            "name": m.name,
            "description": m.description,
            "type": m.type,
            "snippet": _snippet(m.body, needle) or _snippet(m.description, needle),
            "hits": in_desc + in_body,
            "in_index_only": in_body == 0,
            "neighbours": [
                {"name": t, "description": by_name[t].description}
                for t in m.links if t in by_name
            ] if neighbours else [],
        })

    results.sort(key=lambda r: (-r["hits"], r["name"]))
    return {"query": query, "matches": total, "in_memories": len(results),
            "results": results[:max(1, int(limit))]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_search.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/search.py backend/tests/test_memory_search.py
git commit -m "feat(memory): full-text search with one-hop neighbour expansion"
```

---

### Task 7: `history` — đứng trên `vcs`

**Files:**
- Create: `backend/flightdeck/memory/history.py`
- Test: `backend/tests/test_memory_history.py`

**Interfaces:**
- Consumes: `vcs.log`, `vcs.show`, `vcs.is_initialised`, `store.load_all`
- Produces: `history.run(memory_dir: Path, name: str, limit: int = 20, at: str | None = None) -> dict`
  - không có `at`: `{"memory","filename","commits":[{"sha","date","message"}]}`
  - có `at`: thêm `{"at": ref, "content": "<file at that commit>"}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_history.py`:

```python
"""memory_history: how one memory changed, read out of the sidecar git repository."""
from pathlib import Path

import pytest

from flightdeck.memory import history, vcs


@pytest.fixture()
def repo(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"v1\"\nmetadata: \n  type: reference\n---\n\nfirst\n",
        encoding="utf-8")
    vcs.init(mem)
    vcs.commit(mem, "add alpha")
    (mem / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"v2\"\nmetadata: \n  type: reference\n---\n\nsecond\n",
        encoding="utf-8")
    vcs.commit(mem, "correct alpha")
    return mem


def test_lists_commits_newest_first(repo):
    out = history.run(repo, "alpha")
    assert [c["message"] for c in out["commits"]] == ["correct alpha", "add alpha"]


def test_reads_the_file_back_at_an_earlier_commit(repo):
    out = history.run(repo, "alpha")
    older = out["commits"][1]["sha"]
    assert "first" in history.run(repo, "alpha", at=older)["content"]


def test_an_unknown_memory_name_is_an_error(repo):
    assert "error" in history.run(repo, "nope")


def test_a_store_with_no_repository_says_so(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "alpha.md").write_text("---\nname: alpha\n---\n\nx\n", encoding="utf-8")
    out = history.run(mem, "alpha")
    assert "error" in out and "memory_history --init" in out["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_history.py -v`
Expected: FAIL with `ImportError: cannot import name 'history'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/history.py`:

```python
"""How one memory changed over time, and what it said before.

A memory file only states what is believed now. The sidecar repository is what answers
"what did this say three weeks ago, and why did it change" — the temporal dimension a
plain file store cannot give, obtained from git rather than from a separate temporal
index.

Reading an old version goes through `git show`, never `git checkout --`: showing a blob
cannot destroy an uncommitted edit, and the local command guard rejects checkout-discard
anyway.
"""
from pathlib import Path

from flightdeck.memory import store, vcs


def run(memory_dir, name: str, limit: int = 20, at: str | None = None) -> dict:
    memory_dir = Path(memory_dir)
    match = next((m for m in store.load_all(memory_dir) if m.name == name), None)
    if match is None:
        return {"error": f"no memory named {name!r} in {memory_dir}"}
    if not vcs.is_initialised(memory_dir):
        return {"error": "this store is not under version control yet; "
                         "run memory_history --init to create the repository"}

    out = {"memory": name, "filename": match.filename,
           "commits": vcs.log(memory_dir, match.filename, limit=limit)}
    if at:
        out["at"] = at
        out["content"] = vcs.show(memory_dir, at, match.filename)
    return out


def init(memory_dir) -> dict:
    """Create the sidecar repository and take the first snapshot in one act."""
    memory_dir = Path(memory_dir)
    created = vcs.init(memory_dir)
    committed = vcs.commit(memory_dir, "snapshot: memory store before tooling")
    return {**created, **committed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_history.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/history.py backend/tests/test_memory_history.py
git commit -m "feat(memory): read a memory's history out of the sidecar repository"
```

---

### Task 8: `mcp_server` + đăng ký domain + test âm

**Files:**
- Create: `backend/flightdeck/memory/mcp_server.py`
- Modify: `backend/flightdeck/agentsurface/registry.py:19-23`
- Test: `backend/tests/test_memory_mcp.py`

**Interfaces:**
- Consumes: mọi module trên
- Produces: bốn tool trong `registry.merged()` — `memory_lint`, `memory_search`, `memory_graph`, `memory_history`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_mcp.py`:

```python
"""The memory domain as it appears on the agent surface.

The last test is the negative one the workspace convention requires: these four tools
are read-only, and that has to be demonstrated by running them against a real store and
showing the bytes did not move — not asserted from reading the code.
"""
import hashlib
from pathlib import Path

import pytest

from flightdeck.agentsurface import registry
from flightdeck.memory import mcp_server


@pytest.fixture()
def store_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    (d / "alpha.md").write_text(
        "---\nname: alpha\ndescription: \"A long enough description here\"\n"
        "metadata: \n  type: project\n---\n\nbody with [[beta]]\n", encoding="utf-8")
    (d / "beta.md").write_text(
        "---\nname: beta\ndescription: \"Another long enough description\"\n"
        "metadata: \n  type: project\n---\n\nplain body\n", encoding="utf-8")
    (d / "MEMORY.md").write_text(
        "- [Alpha](alpha.md) — hook\n- [Beta](beta.md) — hook\n", encoding="utf-8")
    return d


def test_the_domain_is_registered_and_every_name_wears_its_prefix():
    tools = registry.merged()
    assert {"memory_lint", "memory_search", "memory_graph",
            "memory_history"} <= set(tools)
    assert all(n.startswith("memory_") for n in mcp_server.TOOLS)


def test_every_tool_has_a_description_and_a_schema():
    for name, (fn, description, props, required) in mcp_server.TOOLS.items():
        assert callable(fn), name
        assert len(description) > 40, name
        assert isinstance(props, dict) and isinstance(required, list), name


def test_lint_dispatches_through_the_registry(store_dir):
    out = registry.dispatch("memory_lint", {"memory_dir": str(store_dir)})
    assert out["total_memories"] == 2
    assert "counts" in out and "findings" in out


def test_search_dispatches_through_the_registry(store_dir):
    out = registry.dispatch("memory_search",
                            {"memory_dir": str(store_dir), "query": "body"})
    assert out["in_memories"] == 2


def test_an_unknown_memory_tool_is_reported_as_data(store_dir):
    assert "error" in registry.dispatch("memory_delete", {})


def test_the_four_tools_do_not_write_to_the_store(store_dir):
    """The live negative test. Run all four, then prove nothing on disk moved."""
    def fingerprint():
        return sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in Path(store_dir).iterdir())

    before = fingerprint()
    registry.dispatch("memory_lint", {"memory_dir": str(store_dir)})
    registry.dispatch("memory_search", {"memory_dir": str(store_dir), "query": "body"})
    registry.dispatch("memory_graph", {"memory_dir": str(store_dir)})
    registry.dispatch("memory_history", {"memory_dir": str(store_dir), "name": "alpha"})
    assert fingerprint() == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_mcp.py -v`
Expected: FAIL with `ImportError: cannot import name 'mcp_server' from 'flightdeck.memory'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/mcp_server.py`:

```python
"""The `memory_*` agent surface: four read-only views over the auto-memory store.

None of these four writes to the store. That is the point of the split — the harness
owns the files and stamps their metadata on write; this domain only reads, derives, and
reports, so there is nothing here that can race the harness or corrupt what it wrote.
The one thing that does write is the sidecar git repository, and it lives outside the
directory the harness scans.

Together they answer four different questions, and each tool exists because a plain
`ls` of the store cannot answer its one:

- `memory_lint`   — is this store still trustworthy, and what needs doing
- `memory_search` — what did I write about X, when the summary line does not say so
- `memory_graph`  — how do these connect, and where is the graph torn
- `memory_history`— what did this memory say before, and why did it change
"""
from pathlib import Path

from flightdeck.memory import graph as graph_mod
from flightdeck.memory import history as history_mod
from flightdeck.memory import lint as lint_mod
from flightdeck.memory import paths
from flightdeck.memory import search as search_mod


def _dirs(memory_dir=None, cwd=None):
    if memory_dir:
        target = Path(memory_dir)
        return target, target.parent
    return paths.memory_dir(cwd), paths.transcript_dir(cwd)


def memory_lint(memory_dir=None, cwd=None, with_usage=True):
    store_dir, transcripts = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    if with_usage:
        return lint_mod.with_usage(store_dir, transcripts, cwd=cwd)
    return lint_mod.run(store_dir, transcript_dir=transcripts, cwd=cwd)


def memory_search(query, memory_dir=None, cwd=None, limit=10, neighbours=True):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    return search_mod.run(store_dir, query, limit=limit, neighbours=neighbours)


def memory_graph(memory_dir=None, cwd=None):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    return graph_mod.build(store_dir)


def memory_history(name=None, memory_dir=None, cwd=None, limit=20, at=None, init=False):
    store_dir, _ = _dirs(memory_dir, cwd)
    if not store_dir.is_dir():
        return {"error": f"no memory store at {store_dir}"}
    if init:
        return history_mod.init(store_dir)
    if not name:
        return {"error": "give a memory name, or pass init=true to create the "
                         "version history for this store"}
    return history_mod.run(store_dir, name, limit=limit, at=at)


_DIR_PROP = {"type": "string",
             "description": "path to the memory store; omit to resolve it from cwd "
                            "the way the harness does"}
_CWD_PROP = {"type": "string",
             "description": "project directory to resolve the store from; defaults "
                            "to the current working directory"}

TOOLS = {
    "memory_lint": (
        memory_lint,
        "Check the memory store for the eight ways it drifts, heaviest first: a "
        "reference memory with no checkable source, a pointer to a session that no "
        "longer exists, a summary line too weak or too generic to retrieve on, a file "
        "missing from the index (which makes it permanently invisible), a link to a "
        "memory that does not exist, a file nothing links to, an index line pointing "
        "at nothing, and a link to something that does not exist yet. Also returns a "
        "read count per memory, which says more about what to keep than age does.",
        {"memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "with_usage": {"type": "boolean",
                        "description": "include the per-memory read count; scans the "
                                       "transcripts, so it costs more. Default true."}},
        []),
    "memory_search": (
        memory_search,
        "Search the full text of every memory, not just the one-line summaries the "
        "recall step reads. Use it when you suspect something was written down but "
        "the index says nothing about it. Each hit comes back with the summary lines "
        "of the memories it links to, so one search surfaces a neighbourhood rather "
        "than a single file.",
        {"query": {"type": "string", "description": "text to look for; "
                                                    "case-insensitive substring"},
         "memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "limit": {"type": "integer", "description": "default 10"},
         "neighbours": {"type": "boolean",
                        "description": "include linked memories' summaries. "
                                       "Default true."}},
        ["query"]),
    "memory_graph": (
        memory_graph,
        "How the memories link to each other: every memory with its link counts in "
        "and out, grouped by type, sorted fewest links first so the unconnected ones "
        "come out on top. Links pointing at names that do not exist are kept and "
        "marked, and their targets are listed separately.",
        {"memory_dir": _DIR_PROP, "cwd": _CWD_PROP},
        []),
    "memory_history": (
        memory_history,
        "How one memory changed over time, read from a git repository kept beside "
        "the store. Without a commit reference it lists the changes; with one it "
        "returns what the file said at that point. Pass init=true once to create the "
        "repository and take the first snapshot.",
        {"name": {"type": "string", "description": "the memory's name, not its filename"},
         "memory_dir": _DIR_PROP, "cwd": _CWD_PROP,
         "limit": {"type": "integer", "description": "how many changes, default 20"},
         "at": {"type": "string", "description": "commit reference to read the file at"},
         "init": {"type": "boolean",
                  "description": "create the version history for this store"}},
        []),
}
```

Modify `backend/flightdeck/agentsurface/registry.py` — add one line to `_DOMAINS`:

```python
_DOMAINS = {
    "radar_": "flightdeck.radar.mcp_server",
    "treasure_": "flightdeck.treasures.mcp_server",
    "session_": "flightdeck.sessions.mcp_server",
    "memory_": "flightdeck.memory.mcp_server",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_mcp.py tests/test_agentsurface.py -v`
Expected: all passed — including `test_every_merged_schema_matches_its_function_signature`, which is what catches a schema/signature drift in the new domain.

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/mcp_server.py \
        backend/flightdeck/agentsurface/registry.py backend/tests/test_memory_mcp.py
git commit -m "feat(memory): register the memory domain on the agent surface"
```

---

### Task 9: Stop hook — commit sau mỗi lượt

**Files:**
- Create: `backend/flightdeck/memory/hook.py`
- Test: `backend/tests/test_memory_hook.py`

**Interfaces:**
- Consumes: `paths.memory_dir`, `vcs.commit`, `vcs.is_initialised`
- Produces: `hook.main(argv: list[str] | None = None) -> int` — đọc JSON hook payload trên stdin, commit, in JSON trên stdout, luôn exit `0`

**Vì sao hook phải luôn exit 0:** một Stop hook thất bại không được phép làm hỏng lượt của người dùng. Hook này hoặc commit được, hoặc im lặng bỏ qua — nó không bao giờ là lý do một phiên dừng.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_memory_hook.py`:

```python
"""The Stop hook that snapshots the memory store after each turn."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def run_hook(env_extra, payload="{}"):
    return subprocess.run(
        [sys.executable, "-m", "flightdeck.memory.hook"],
        cwd=str(BACKEND), input=payload, capture_output=True, text=True,
        timeout=60, env={**__import__("os").environ, **env_extra})


@pytest.fixture()
def configured(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("first\n", encoding="utf-8")
    return tmp_path, mem


def test_the_hook_commits_a_changed_store(configured):
    tmp_path, mem = configured
    from flightdeck.memory import vcs
    vcs.init(mem)
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(mem)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is True


def test_the_hook_exits_zero_when_there_is_no_repository(configured):
    _tmp, mem = configured
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(mem)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is False


def test_the_hook_exits_zero_when_the_store_does_not_exist(tmp_path):
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(tmp_path / "nope")})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_hook.py -v`
Expected: FAIL with `No module named flightdeck.memory.hook`

- [ ] **Step 3: Write minimal implementation**

Create `backend/flightdeck/memory/hook.py`:

```python
"""Stop hook: snapshot the memory store into its sidecar repository after each turn.

The harness writes memories asynchronously — once at the end of a turn, and again
whenever background consolidation runs. Without a commit between those writes the
changes pile up and the boundary between "what this turn changed" and "what the last one
did" is lost, which is exactly the boundary anyone reviewing a consolidation needs.

It always exits 0. A hook that fails must not be the reason a session stops, so a
missing store, a missing repository and a git error all come back as
`{"committed": false}` and a zero exit.

Wire it up in ~/.claude/settings.json:

    "Stop": [{"matcher": "*", "hooks": [{"type": "command",
      "command": "<repo>/.venv/bin/python -m flightdeck.memory.hook"}]}]
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from flightdeck.memory import paths, vcs


def main(argv=None) -> int:
    try:
        sys.stdin.read()  # drain the hook payload; nothing in it is needed yet
    except Exception:
        pass

    try:
        override = os.environ.get("FLIGHTDECK_MEMORY_DIR")
        store_dir = Path(override) if override else paths.memory_dir()
        if not store_dir.is_dir() or not vcs.is_initialised(store_dir):
            result = {"committed": False, "files": []}
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            result = vcs.commit(store_dir, f"memory: turn snapshot {stamp}")
    except Exception as e:  # a hook must never break the turn
        result = {"committed": False, "files": [], "error": f"{type(e).__name__}: {e}"}

    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_memory_hook.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/hook.py backend/tests/test_memory_hook.py
git commit -m "feat(memory): Stop hook snapshots the store each turn"
```

---

### Task 10: Chạy trên kho thật và ghi lại số đo

**Files:**
- Create: `backend/flightdeck/memory/README.md`
- Test: cả bộ, `backend/tests/`

**Interfaces:**
- Consumes: tất cả
- Produces: không có API mới — task này biến plan thành bằng chứng

Đây là **architecture check** theo quy ước workspace: mỗi khẳng định đi kèm lệnh và output thật, để người đọc chạy lại được từng dòng.

- [ ] **Step 1: Chạy toàn bộ test suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/ -q`
Expected: toàn bộ pass, không có test nào của domain khác hỏng. Nếu `test_agentsurface.py` hỏng thì đó là schema/signature lệch — sửa signature, đừng sửa test.

- [ ] **Step 2: Chụp bản sao kho thật trước khi chạm vào nó**

```bash
cp -a ~/.claude/projects/-home-nathando-Documents-Projects/memory \
      ~/.claude/projects/-home-nathando-Documents-Projects/memory.bak-$(date +%F)
```

Đây là bản lùi duy nhất: `~/.claude` không phải git repo, và `~/.claude/backups` chỉ chứa bản sao `.claude.json`.

- [ ] **Step 3: Chạy bốn tool trên kho thật và ghi lại output**

```bash
cd backend
../.venv/bin/python -m flightdeck.cli memory_lint | python3 -m json.tool | head -40
../.venv/bin/python -m flightdeck.cli memory_graph | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d['nodes']),'nodes',len(d['edges']),'edges',d['missing_targets'])"
../.venv/bin/python -m flightdeck.cli memory_search --query worktree --limit 3
../.venv/bin/python -m flightdeck.cli memory_history --init
```

Đối chiếu với số đo trong spec §4.2. Bốn con số cấu trúc phải khớp: `not_in_index` **1**, `broken_link` **3**, `not_linked` **3**, `missing_file` **0**. `future_target` phải là **1**.

Nếu một con số lệch, **đừng sửa test cho khớp** — tìm hiểu vì sao. Kho có thể đã đổi từ lúc đo (2026-08-28); nếu vậy, ghi số mới và ghi cả lý do lệch.

- [ ] **Step 4: Viết README của domain với số đo thật**

Create `backend/flightdeck/memory/README.md` — bảng `claim → lệnh → output thật` cho từng khẳng định ở Step 3, cộng ba đoạn ngắn: bốn tool làm gì, vì sao kho git nằm ngoài thư mục memory, và cách gắn Stop hook. Trích output thật, không mô tả nó.

- [ ] **Step 5: Commit**

```bash
git add backend/flightdeck/memory/README.md
git commit -m "docs(memory): domain README with measurements from the real store"
```

---

## Sau khi xong

**Chưa làm trong plan này, và lý do:**

- **`memory_add`** — harness đóng dấu `node_type`/`originSessionId`/`modified` bằng cách chặn tool `Write`. Một `memory_add` ghi qua subprocess thì harness không thấy. Phải thử thật rồi mới thiết kế được; xem spec §4.4.
- **Index phái sinh (SQLite FTS, ngữ nghĩa cục bộ)** — `search.run` hiện quét thẳng. Ở 37 file chi phí bằng không. Thêm index khi kho lớn hơn, và thêm dưới dạng **sản phẩm phái sinh xoá-dựng-lại-được**: markdown vẫn là bản gốc.
- **Tab UI** — chặn bởi việc hợp nhất hai tài liệu design system của FlightDeck (spec §4.10). Mock Pencil đã có, bốn screen.
- **Phủ index lên 1.2GB transcript** — spec §4.11.

**Việc kế tiếp sau khi Task 10 xanh:** bật `autoDreamEnabled` trong `~/.claude/settings.json`, rồi `memory_lint` lần nữa và `git diff` để xem dream đã đổi gì. Đây là lần đầu tiên câu hỏi "dream có hữu ích không" trả lời được bằng số. Lưu ý dream sẽ nổ **ngay ở lượt đầu tiên** sau khi bật, không phải sau 24 giờ — spec §5.

**Hai domain còn lại có plan riêng**, và mỗi cái đang bị chặn bởi một câu hỏi thật:
- `odoo_*` — cần login/password thật của `odoo12-local`
- `ssh_*` — cần một phép đo ControlMaster thật trên đường tới staging
