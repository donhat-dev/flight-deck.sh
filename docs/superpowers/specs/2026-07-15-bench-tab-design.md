# Bench — A/B/N comparison harness for Claude Code CLI conditions

## Context & motivation

This generalizes a pattern that already exists twice, independently, in ad hoc form:

1. **`nakivo-graph/token-ab*.sh` + `token-golden.sh`** — headless `claude -p --output-format json`
   A/B/N runs comparing grep-only vs graph-MCP vs graph+skill conditions, scored against a
   "golden set" of questions with verifiable ground-truth answers (`TOKEN-BENCHMARK.md`,
   `EVAL-GUIDE.md`).
2. **This session's Ponytail/Caveman/YAGNI benchmark** — the same shape of harness, hand-built
   with bash scripts spawning isolated `claude -p` calls per condition (different `.claude/skills/`
   folders, different `--append-system-prompt`, different `--allowedTools`), a Python regex LOC
   counter, and manual correctness checks (extracting + executing generated code).

Both converge on the same abstraction: **a fixed set of tasks, run under a fixed set of named
conditions (system-prompt / skill / tool variants), scored by pluggable checks, compared side by
side.** Bench formalizes this into one reusable, declarative tool instead of a new one-off script
per experiment.

**Prior art considered and not adopted as dependencies:**
- **promptfoo** — good mechanism ideas (declarative provider × test matrix, custom JS/Python
  assertions, a local comparison UI, cost/token tracking). Not adopted directly: it has no native
  concept of per-condition sandboxed working directories with different `.claude/skills/` folders
  (our core requirement), and it was acquired by OpenAI (2026-03-09), integrating into "OpenAI
  Frontier" — still open source under the current license, but future roadmap direction is
  uncertain for a general-purpose eval use case. We borrow its *mechanism* (matrix config, pluggable
  assertions, comparison UI) and build in-house instead.
- **Microsoft SkillOpt** — a different tool: validation-gated *optimization* of a skill document
  (bounded add/delete/replace edits, accepted only when they strictly improve a held-out validation
  score; supports Claude Code CLI as an execution harness). Complementary, not competing — Bench
  answers "which of these N hand-picked candidates is best," SkillOpt answers "what is the optimal
  skill text." Not adopted now (different scope). *(Self-review note: an earlier draft justified the
  `CheckResult` shape below by its potential future reuse as SkillOpt's reward function — that is
  speculative justification for a non-goal and was cut. The shape is kept only because it is already
  the minimal, importable interface Bench itself needs; any future reuse is a bonus, not a design
  driver.)*

## Goals

- One TOML-declared **suite** = a set of **conditions** (skill/system-prompt/tool variants) ×
  **tasks** (a prompt + one or more **checks**).
- Runs are triggered from the terminal (`python -m token_audit.bench run <suite.toml>`); the UI is
  read-only (browse/compare already-recorded results). Rejected the alternative (a "Run" button in
  the browser triggering execution) because each run spawns real, paid `claude -p` calls that should
  execute sequentially and under human control — building a job-queue/background-task system to
  support browser-triggered runs is unjustified complexity for a need nobody has expressed.
- Pluggable scoring: built-in checks (`loc`, `contains`, `regex`, `equals`) plus custom Python check
  files for functional/correctness verification (e.g., actually executing generated code and
  asserting on it, as done by hand for the discount-engine benchmark this session).
- Results persist in the SQLite database token-audit already uses (`audit.db`), surfaced through a
  new read-only API and a new "Bench" tab in the existing React dashboard — reusing the stack
  already running at :8010 instead of building new infrastructure.
- V1 scope: **Claude Code CLI only** as the thing being benchmarked (not Codex or other providers).
  This is a deliberate scope cut, not an oversight — both proven precedents (graph-vs-grep,
  Ponytail-vs-YAGNI) are Claude Code CLI condition comparisons; broadening to other providers can
  be added later if a real need appears.
- Migrate the existing `nakivo-graph` golden set (`TOKEN-BENCHMARK.md` Q1–Q3,
  `EVAL-GUIDE.md` G1–G8) into a Bench suite as the acceptance test for "this generalizes for real,"
  then retire `token-ab*.sh` / `token-golden.sh`.

## Non-goals (V1)

- No browser-triggered execution.
- No non-Claude providers (Codex, raw API, etc.).
- No automated skill-document optimization (that's SkillOpt's job, a separate tool/decision).

## Architecture / data flow

```
suite.toml (hand-written)
      │
      ▼
python -m token_audit.bench run suite.toml   ← terminal, like today's token-ab.sh
      │
      │  for each (condition × task) pair, sequentially:
      │  1. create an isolated tmp working directory
      │  2. stage the condition's skill_dir into .claude/skills/<name>/ and/or its
      │     mcp_config into .mcp.json, whichever is set
      │  3. spawn `claude -p ... --output-format json` in that directory, under a timeout
      │  4. run every configured check against the parsed result text
      │  5. record one run row (+ one row per check)
      ▼
audit.db (SQLite, already used by token-audit)  — new tables: bench_suites / bench_runs / bench_checks
      │
      ▼
FastAPI — new read-only routes (/api/bench/...)
      │
      ▼
React "Bench" tab — condition × task matrix, drill-down per cell, cost/LOC summary chart
```

## Config schema (TOML)

```toml
[suite]
name = "ponytail-vs-yagni"
model = "sonnet"                      # default for all conditions; overridable per-condition

# ---- the "condition" axis: what varies about the Claude Code CLI invocation ----
[[condition]]
name = "baseline"

[[condition]]
name = "ponytail"
skill_dir = "skills/ponytail"         # staged into <tmpdir>/.claude/skills/ponytail/
allowed_tools = ["Skill"]

[[condition]]
name = "yagni"
append_system_prompt = "Follow YAGNI principles, and one-liner solutions."
allowed_tools = []
model = "opus"                        # per-condition override of [suite].model

[[condition]]
name = "graph_mcp"
mcp_config = "mcp/odoo-graph.json"    # staged as <tmpdir>/.mcp.json — required to reproduce the
                                       # existing graph-vs-grep suite, whose "graph+MCP" condition
                                       # depends on the odoo_graph MCP server being registered
                                       # (see nakivo-graph/EVAL-GUIDE.md §1). `skill_dir` alone
                                       # cannot express this — the condition needs its own field.

# ---- the "task" axis: a fixed prompt plus one or more checks ----
[[task]]
name = "discount_engine"
prompt = "Write a discount-calculation service..."

  [[task.check]]
  name = "loc"                        # required, unique per task — identifies this row across runs
  type = "loc"                        # built-in: counts LOC of the first fenced code block only

  [[task.check]]
  name = "correctness"
  type = "python"
  file = "checks/discount_engine_check.py"   # must expose check(output_text) -> CheckResult
```

`condition` generalizes both precedents: `skill_dir` + `allowed_tools` covers the
Ponytail/graph-MCP-vs-skill shape, `mcp_config` covers the graph+MCP shape, and
`append_system_prompt` covers the YAGNI-one-liner shape. `task.check` generalizes both the
LOC-counting we did by hand and the golden-set exact-answer checks already used in `nakivo-graph`.

**Built-in check semantics** (kept deliberately simple for V1 — a check needing anything richer
is a `python` check):
- `loc` — counts non-blank lines inside the *first* fenced code block in the output text only
  (matches the convention already used by hand this session); `passed` is always `None` (pure
  measurement), `score` is the count.
- `contains` — case-sensitive substring match of `value` against the output text; `passed` is the
  match result, `score` is unused.
- `equals` — exact string match of `value` against the output text after stripping leading/trailing
  whitespace; `passed` is the match result.
- `regex` — `re.search(pattern, output_text)`, Python `re` syntax, case-sensitive by default
  (`ignore_case = true` opts in to `re.IGNORECASE`); `passed` is whether a match was found.

## Runner

For each (condition × task) pair, the runner builds and invokes exactly the command shape used by
hand throughout this session:

```
claude -p "<task.prompt>" --model <model> --setting-sources project --output-format json \
  --no-session-persistence [--append-system-prompt "<condition.append_system_prompt>"] \
  [--allowedTools "<condition.allowed_tools, comma-joined>"]
```

(`allowed_tools` is a TOML array; the runner comma-joins it into the single string `--allowedTools`
expects — e.g. `allowed_tools = ["Skill", "Read"]` becomes `--allowedTools "Skill,Read"`.)

in a freshly created, isolated working directory (with the condition's `skill_dir` and/or
`mcp_config`, if set, copied into `.claude/skills/<name>/` and `.mcp.json` respectively) — never
re-using a directory across runs, so no condition's staged files can leak into another.

**Timeout.** Each `claude -p` subprocess runs under a hard wall-clock timeout (`suite.timeout_s`,
default 300s). On expiry the runner sends `SIGTERM`, waits a few seconds, then `SIGKILL`s if still
alive, and records the run with `error = "timeout after <n>s"` — a hang in one condition/task pair
must never block the rest of the sequential suite.

**`CheckResult` interface** (the scoring contract every check type — built-in or custom Python —
produces):

```python
@dataclass
class CheckResult:
    passed: bool | None    # None for pure measurements with no pass/fail concept (e.g. `loc`)
    score: float | None    # a numeric measurement, e.g. LOC count, cost, a similarity score
    detail: str = ""       # short human-readable note (e.g. which assertion failed)
```

This is a plain, importable Python function (`check(output_text: str) -> CheckResult`), not a
runner-internal detail, kept deliberately minimal for Bench's own needs — the interface happens to
be reusable elsewhere later (see the SkillOpt self-review note above) but that is not why it looks
this way.

**Checker crash vs. checker verdict.** If a `python` check itself raises an exception, that is an
*infrastructure* failure, not a signal that the condition's output was wrong — the two must not be
conflated into the same `passed=False` a real failed assertion produces. See Error handling.

## Storage (SQLite — new tables in the existing `audit.db`)

```
bench_suites   — one row per `bench run suite.toml` invocation
  id, name, config_path, config_hash, status (running|completed|interrupted),
  started_at, finished_at

bench_runs     — one row per (condition × task) executed within a suite run
  id, suite_run_id → bench_suites, condition_name, task_name, model,
  cost_usd, num_turns, duration_ms, permission_denials,
  output_text, error (null unless the claude -p call itself failed or timed out)

bench_checks   — one row per check evaluated within a run
  id, run_id → bench_runs, check_name, check_type, outcome (pass|fail|error),
  score, detail
```

Three fixes over the first draft, all from the same root cause (the append-only history claim was
under-specified):
- **`config_hash`** — a content hash of the resolved TOML, stored per suite run. Without it, editing
  `ponytail-vs-yagni.toml` and re-running is indistinguishable in history from an unmodified re-run,
  which defeats the stated trend-comparison purpose. `config_path` alone is not enough once a suite
  file can change over time.
- **`status`** — set to `running` when the suite starts, `completed` when every pair finishes, or
  `interrupted` if the process dies mid-run (best-effort, e.g. via an on-exit handler). The API/UI
  must not present an `interrupted` suite's missing cells as "this condition just has no data."
- **`model`** now lives on `bench_runs`, not only on `bench_suites` — a condition can override the
  suite's default model, so the model actually used for a given run must be recorded per-run, not
  inferred from the suite-level default after the fact.
- **`bench_checks.outcome`** replaces the earlier plain `passed: bool | None` column with an explicit
  three-way `pass | fail | error` — a check that crashed (bad check script, missing dependency) is
  `error`, not `fail`; only `fail` means "the check ran and determined the output was wrong." This
  is the same distinction as the `CheckResult`/Error-handling note above, reflected into storage.
  `check_name` disambiguates multiple checks of the same `check_type` on one task (e.g. two
  `regex` checks with different patterns).

`permission_denials` is captured because it is already present in the `claude -p --output-format
json` result being parsed anyway (near-zero marginal cost), but it has **no V1 consumer** — it is
not shown in the matrix, the summary chart, or counted by any check. It is available only via the
run drill-down (`GET .../runs/{run_id}`) for manual debugging. If it never gets used from there
either, drop the column rather than carry it indefinitely.

Suite runs are append-only: re-running the same `suite.toml` creates a new `bench_suites` row
rather than overwriting the previous one, preserving history for trend comparison (e.g., "did this
suite's numbers change after a model update"), consistent with how token-audit already treats its
usage ledger.

**Explicitly accepted limitations (not solved in V1, revisit only if they bite):**
- No pagination/retention policy on `output_text` or the suites list — acceptable for a
  single-user, occasionally-run internal tool; revisit if `audit.db` size or list load time ever
  becomes a real problem.
- No cross-process write-locking beyond SQLite's own WAL mode (already enabled for `audit.db`) —
  Bench is not designed for two suite runs to execute concurrently against the same database, which
  is not an expected usage pattern for a tool one person runs from one terminal at a time.

## API (read-only)

- `GET /api/bench/suites` — list past suite runs, newest first.
- `GET /api/bench/suites/{id}` — full condition × task matrix for one suite run (cost, turns,
  checks).
- `GET /api/bench/suites/{id}/runs/{run_id}` — drill into one cell: full output text and every
  check's detail.

## UI (new "Bench" tab)

Follows the existing tab pattern (`GraphView.jsx`, `RepoDiff.jsx`, `RouteLoom.jsx`): a suite picker,
a condition × task matrix, a click-through detail drawer per cell (full output + per-check
breakdown), and a summary bar chart (average cost/LOC per condition) using the Recharts setup
already in the dashboard — replacing the hand-built markdown comparison tables produced by hand
this session.

**Cell badge rule** (undefined in the first draft — a task can have multiple checks, and some
checks are pure measurements with no pass/fail):
- If the run itself has an `error` (CLI failure or timeout) → badge is **error**, regardless of
  checks (none ran).
- Else if any check has `outcome = "error"` → badge is **error** (a checker crashed; the condition's
  actual output was never judged, so it cannot be shown as pass or fail).
- Else if every check with `outcome ∈ {pass, fail}` is `pass` → badge is **pass** (a task with only
  measurement checks, e.g. just `loc`, always shows **pass** by this rule — there is nothing to
  fail).
- Else → badge is **fail**.

Regardless of badge, every check's individual `score` (LOC, or whatever it measures) is always shown
alongside the cell, since a passing cell can still be worth comparing on cost/LOC across conditions.

## File layout

```
token_audit/bench.py          — runner + CLI entrypoint (`python -m token_audit.bench run ...`)
token_audit/bench_checks.py   — built-in check types (loc/contains/regex/equals) + python-check loader
bench/suites/*.toml           — hand-written suites (e.g. ponytail-vs-yagni.toml, graph-vs-grep.toml)
bench/skills/ponytail/...     — skill fixtures staged into isolated run directories
bench/mcp/odoo-graph.json     — mcp_config fixtures staged as .mcp.json (e.g. for graph-vs-grep)
bench/checks/*.py             — custom Python check functions
frontend/src/BenchView.jsx    — the new tab component
```

User-authored content (`bench/`) is kept separate from the tool's own source, mirroring how
`nakivo-graph` keeps `TOKEN-BENCHMARK.md` separate from `ingest.py`.

## Error handling

- `claude -p` itself fails (non-zero exit, unparseable JSON, or hits the timeout described in
  §Runner) → record the run with `error` set, skip its checks entirely, continue to the next
  (condition × task) pair — one failure never aborts the whole suite.
- A referenced `skill_dir` or `mcp_config` path does not exist → validated when the suite config is
  loaded, **before** any `claude -p` call is spawned — a config mistake should fail fast, not
  silently burn cost running the wrong condition.
- A custom Python check raises an exception → caught and recorded as `bench_checks.outcome =
  "error"` (not `"fail"`) with `detail=<exception message>`. This is deliberately a different
  outcome than a check that ran successfully and determined the output was wrong (`"fail"`) —
  conflating the two would make a broken checker look identical to a condition producing bad
  output, silently corrupting the comparison. Does not abort the suite.
- If the runner process itself is interrupted (Ctrl-C, crash) mid-suite → the current
  `bench_suites.status` stays `"running"` unless an on-exit handler manages to flip it to
  `"interrupted"`; the UI must treat any suite that is `"running"` long after its process should have
  finished as incomplete, not as "this condition has no data."

## Testing

Following the existing `tests/` + pytest convention:
- TOML suite parsing (valid and invalid configs).
- The runner's command-line construction (a pure function: condition → argv list), testable without
  invoking the real CLI.
- Built-in check types (`loc`, `contains`, `regex`, `equals`) as pure-function unit tests.
- The new SQLite schema/migration.

## Migration plan / acceptance criteria

Port `nakivo-graph`'s existing golden set (`TOKEN-BENCHMARK.md` Q1–Q3 questions and conditions,
`EVAL-GUIDE.md` G1–G8) into `bench/suites/graph-vs-grep.toml`. Acceptance is **directional, not
exact** — a live `claude -p` run is not perfectly reproducible run-to-run (sampling variance), so
"reproduces" means: the same condition ranking holds (e.g., GRAPH+skill still cheapest and fewest
turns among the four) and each number falls within roughly the same order of magnitude as recorded
in `TOKEN-BENCHMARK.md` (e.g., GRAPH+skill in the ballpark of $0.54 / 14 turns, not exactly that
figure to the cent). Only after this directional reproduction succeeds do `token-ab*.sh` and
`token-golden.sh` get retired.

## Future extensions (explicitly out of scope for V1)

- A Codex (or other-provider) condition type, once a real need for cross-model comparison inside
  Bench (rather than as an ad hoc differently-blind reviewer, as used elsewhere this session)
  appears.
- Browser-triggered runs, if the terminal-first workflow ever becomes a real friction point.
- Wiring Bench's `check()` functions into SkillOpt as its validation-gate reward function, if
  automated skill-document optimization becomes a goal rather than manual A/B/N comparison.
