# Cost Audit, Cost per Task, Agent Work-Hours

**Date:** 2026-09-23
**Status:** Implemented on the running checkout, uncommitted. Decisions and what was built are in section 4; sections 1–3 keep the sketch as reviewed, with corrections marked.
**Branch observed:** `odoo-19-experiment` (running service reads this checkout).

## Summary

- **The dashboard's $43,105 all-time API-equivalent cost is about 3.7× too high. The corrected figure is about $11,568.** The main error is counting: one API response appears as 2–3 ledger rows. The price table has three smaller errors.
- **The $223,557 "cache savings" figure is about 3.3× too high.** Net savings, computed as uncached price minus cached price over the corrected tokens, are about **$67,306**: $78,874 without caching against $11,568 with it.
- **Cost per task works with a prototype.** Over 30 days: 1,765 human-started turns, median $0.63, p90 $7.32. The cost attributed to turns ($4,489) is within 0.4% of the deduplicated ledger ($4,507).
- **Agent work-hours per day work with a prototype.** Mean 8.8 agent-hours per active day, of which subagents are 0–8 hours. The figure moves only 8.6–9.1 when the idle-gap cap goes from 5 to 30 minutes.

## 1. Cost audit

### What is wrong

| # | Finding | Evidence | Effect (all time) |
|---|---|---|---|
| F1 | **One API response is stored 2–3 times.** Claude Code writes one jsonl line per content block (thinking, text, tool_use). Each line carries the response's `usage`, and early lines can carry a partial `output_tokens` (for example 5, then 145). `messages` is keyed on line `uuid`, so every line becomes a row | On the 100 sessions still on disk: 65,392 ledger rows versus 32,873 unique `(message.id, requestId)` | $43,105 → $12,169 at the current prices |
| F2 | **Resumed and forked sessions copy history with new uuids and a new session id.** The copied rows are billed again | 6,027 input-side token tuples appear in more than one session; 6,017 of them have identical timestamps, which means copies. Mostly 2026-07 (one session copied into 11 forks) | Included in F1's figure |
| F3 | **Opus 5.5 is missing from `RATES`.** The `claude-opus-5` prefix matches it, so it is priced at $5/$25 with 0.1× cache reads instead of $4/$20 with 0.05× | `pricing.py`; Claude pricing page, 2026-09-23 | Small today ($24 of real spend) but grows with use |
| F4 | **Fable 5.1 cache reads use 0.1× instead of 0.025×.** `claude-fable-5` matches `claude-fable-5-1` | Same | Fable 5.1 cache reads priced at $1.00 instead of $0.25 per MTok |
| F5 | **Sonnet 5 is billed at $3/$15 from 2026-09-01.** Anthropic kept the $2/$10 introductory price as the standard price | Pricing page note "will not occur" | Sonnet 5 turns since September are overstated 1.5× |
| F6 | **`cache_savings` counts reads only.** The formula is `cache_read × 0.9 × input_rate`; it ignores the write premium (1.25× and 2×) and inherits F1 | `pricing.cache_savings` | Savings overstated. On the stored rows, the write premium alone is about $4.1k on Opus 5 |
| F7 | **Fast mode and US-only inference are not captured.** `usage.speed` and `usage.inference_geo` exist in transcripts but are not stored. Every current line is `standard`, so the effect is zero today | Transcript scan | 2× (fast) and 1.1× (US) multipliers would be missed later |

### Decomposition of the headline

| Step | All time | Last 30 days |
|---|---:|---:|
| Dashboard today | $43,105 | $11,210 |
| Price fixes F3–F5 only | $41,609 | $9,714 |
| Deduplication F1–F2 only | $12,169 | $5,108 |
| **Both** | **$11,568** | **$4,507** |
| Uncached price of the same tokens | $78,874 | $33,323 |
| **Net cache savings** (uncached − cached) | **$67,306** | **$28,815** |

Corrected all-time spend by model: Opus 5 $7,579 · Opus 4.8 $1,829 · Fable 5 $1,013 · Fable 5.1 $671 · Sonnet 5 $451 · Opus 5.5 $24 · Haiku 4.5 $2. Against the $200/month subscription (about 2.5 months, roughly $500), the API-equivalent surplus is about **$11.1k, not $42.6k**. GPT/Codex rows (63.8M tokens) remain unpriced and are excluded from every figure.

**Independent check of F1.** `ccusage` (which dedupes on `message.id` + `requestId`) reports the on-disk corpus at 10.24B cache-read and 205.9M cache-write tokens. The exact-key scan gives 10.20B and 205.9M; the uuid rows FlightDeck stores give 20.14B and 500.4M. Output differs about 10% (37.9M against 34.5M), probably because `ccusage` includes non-Claude rows; not investigated.

**How the deduplicated figures were computed.** The ledger does not store `message.id`, and 66 of 166 sessions no longer have transcripts on disk. `scripts/cost-audit/audit_cost.py` therefore keys on the input-side token tuple `(model, input, cache_read, cache_write_5m, cache_write_1h)`. It merges across sessions when the cached part is at least 10k tokens, otherwise within the session, and keeps the largest output. Against the exact key on the on-disk corpus, this key is 0.3–1.4% high per token class (`heuristic_check.py`). That corpus only covers recent sessions. The older rows (mostly July, where F2 is heavy) could not be checked against the exact key, so the all-time figure is less certain than the 30-day one.

### Proposed fixes

1. **Store the exact key.** Add `message_id` and `request_id` to `messages` with a unique index. On conflict, keep the row and raise `output_tokens` to the larger value. Keep `uuid` for `message_text`.
2. **Backfill.** Re-scan the files still on disk; that key is exact. For rows whose transcripts are gone, collapse duplicates with the heuristic key and mark them `dedupe_method = 'heuristic'`, so the dashboard can show how much of a range rests on it.
3. **Price table.** Add `claude-opus-5-5` (4, 20) and `claude-fable-5-1` (10, 50). Replace the single `CACHE_READ_MULT` with a per-prefix override (Opus 5.5 0.05, Fable/Mythos 5.1 0.025, others 0.1). Set Sonnet 5 to (2, 10) and drop its promotion row. Cite the page read on 2026-09-23.
4. **Savings.** Rename the current figure, or replace it with `net_cache_savings = uncached − cached`, where uncached prices every input-side token at the base input rate.
5. **Capture `usage.speed` and `usage.inference_geo`** and apply the fast-mode rates and the 1.1× US multiplier.
6. **Tests.**
   - A fixture message split into thinking + tool_use lines, with output 5 then 145, must yield one row with output 145.
   - A resumed-session copy of that message in another file must add no row.
   - Opus 5.5, Fable 5.1 and Sonnet 5 rows at known tokens must match the hand totals in `audit_cost.txt`.
   - Run the tests against Postgres too (the `Decimal` trap in `metrics.rollup`).

Costs are derived from tokens at read time, so these fixes restate history automatically. The all-time tile will drop from $43k to about $11.6k.

## 2. Cost per task

### Definition

A **task** starts at a human prompt and ends when the agent's work for it ends. Classification then groups prompts into three kinds:

- **task**: a new goal.
- **sub-task**: a follow-up, correction or next step of an open task. Answers to gate questions and "continue" prompts land here.
- **merge task**: a prompt that integrates the results of two or more earlier tasks, such as "commit everything", "summarize today's work" or reconciling branches.

Subagents are sub-tasks by structure; they need no classifier.

### Pipeline

| Stage | What it does | Cost |
|---|---|---|
| 0. Structural segmentation (ingest) | A turn starts at a `user` record with `turnOrigin = "human"`. Older versions: `promptSource` in (`user`, `sdk`), not meta, not a `tool_result`. System-origin turns (task notifications, hooks) attach to the open turn. Assistant rows attach to the turn in force when they are read. Subagent files attach through the parent turn's `promptId`, which their records carry | Free |
| 1. Rules | A turn under `/tmp` is automated. A turn with no billed rows is empty (slash commands never open a turn, so they need no rule). A short acknowledgment (≤ 24 chars) within 30 min of the previous turn's end is a sub-task | Free |
| 2. Jev first pass (chosen before the LLM because the workspace's Jev bench shows its probability spread flags its own errors) | `Choice` over {task, sub-task, merge task}. State: the prompt (≤2k chars), the previous turn's first prompt and final agent message (≤1k), gap minutes, same session, cwd/branch change, titles of tasks opened in the last 24 h | $0.042/MTok input: under $0.01 per month at this volume |
| 3. Escalation | When Jev's top answer is below 0.8 or its margin below 0.3, call GPT-6 Luna through `codex exec`, 15 turns per call, with a JSON schema: `kind, parent_id, merged_ids, title, confidence`. A cross-session follow-up escalates too, since it needs an id. Jev is also unsuited to picking merge targets, so a merge verdict escalates too. The workspace's Jev bench found that flat distributions are where Jev is wrong (confident 16/16 correct, flat 3/12) | Luna about $0.50/month, Haiku about $5/month if every prompt escalated |
| 4. Human queue | Disagreements and low-confidence cases go to a one-click review in FlightDeck. The labels become the evaluation set | Minutes per week |

Classification runs as a background job and never blocks ingest. Labels are stored with their source (`rule`, `jev`, `llm`, `human`) so accuracy can be measured per stage before cost per task is trusted.

**Cost per task** is the sum of the deduplicated API cost of the task's turns and subagents. The dashboard shows it two ways: the task alone, and rolled up with its sub-tasks. A merge task's cost stays with the merge task. It is not spread over the merged tasks.

### Prototype result (30 days on disk, before classification)

1,765 human-started turns. Cost per turn: median $0.63, p90 $7.32, max $79.74. Agent-hours per turn: median 0.02, p90 0.25, max 4.19. The top five turns are all multi-hour implementation or parallel-agent requests. Only $2 of the $4,491 total could not be attributed to a turn. Grouping turns into tasks will raise per-task figures; how much is unknown until the classifier runs.

### Data model

As built, one table holds turns and their labels, since a task is its root turn:

- `turns`: `turn_id, session_id, started_at, ended_at, prompt_head, final_head, cwd, git_branch, kind, parent_id, merged_ids (JSON), title, label_source, label_confidence, label_detail, needs_review, labeled_at`.
- `messages.prompt_id`, `messages.agent_id`: set at ingest. The ingest keeps the current `promptId` per file in `files`, so an incremental read continues the right turn.

### Risks

- **Outbound text.** Stages 2–3 send prompt and response text to TypeSafe, Anthropic or OpenAI. Prompts can hold internal material or secrets. Redact before sending, and do not enable this without your decision (open decision 3).
- **Jev is early access.** The SDK has broken every 2–3 days and rate limits can change without notice. Keep it behind an interface with a Haiku/Luna fallback.

## 3. Agent work-hours per day

### Definition

An agent is busy from the record that opens a turn until its final `end_turn`. Busy time is the sum of gaps between consecutive records inside an open turn. Each gap is capped at 15 minutes, so a long wait for a human does not count as work. Time between turns is idle.

Each subagent file counts on its own, so four agents busy for an hour count as four hours. The main agent keeps counting while it waits on its subagents; it is occupied, and this matches "one agent for one hour = 1 h".

Hours are split at UTC midnight, because the dashboard's days are UTC dates (the sketch said local midnight). Every model weighs 1:1 for now; a per-model `agent_hour_weight` (default 1.0) leaves room for a capability weighting later.

Not counted: background shells, and Codex/GPT sessions (not in Claude transcripts; needs a Codex ingest).

### Prototype result

Mean 8.8 agent-hours per active day over the last 30 days on disk (range 1.5–15.9). Subagents account for 0.0–8.3 of those hours. Days with heavy parallel-agent work (2026-09-11, 2026-09-14) reach about 16 h.

| Gap cap | Mean agent-h/day |
|---|---:|
| 5 min | 8.56 |
| 15 min (proposed) | 8.84 |
| 30 min | 9.06 |

### Storage

`agent_activity`: `session_id, agent_id ('' for the main agent), prompt_id, day, method, busy_seconds`. `method` is `transcript`, or `ledger` for the estimate kept for sessions whose transcript is gone. It is aggregated in the ingest pass from every `user`/`assistant`/`system` record's timestamp. Per-file state (`act_last_ts`, `act_in_turn`, `act_wait_human`, `act_prompt_id`, `act_answered`) sits in `files` so an incremental read continues exactly.

## 4. Decisions and what was built

Answered on 2026-09-23:

1. The main agent waiting on subagents counts.
2. Idle-gap cap 15 minutes, configurable (`TOKEN_AUDIT_AGENT_IDLE_CAP_MIN` or `agent_idle_cap_minutes`).
3. Outbound classification allowed, with redaction and the context passed mechanically.
4. Time after `AskUserQuestion` is excluded.
5. History restated.
6. Added: Token trend and Work-hours trend charts next to Cost trend, because falling prices hide workload in the cost trend.

### Built

| Area | Change |
|---|---|
| Ledger | `message_id`, `request_id`, `speed`, `inference_geo`, `prompt_id`, `agent_id`, `dup_of`, `dedupe_method` on `messages`. A repeat line is marked `dup_of` the response's row, never deleted. Every billed read filters `dup_of IS NULL` |
| Prices | F3–F7 as proposed; `cache_savings` is net (uncached − cached) |
| Turns | `activity.FileTracker` in the ingest pass. IDE context blocks in front of a prompt are stripped before the prompt rule. A prompt sent before the agent replied joins the open turn. A stretch the CLI re-appends to the same file (same uuids, old timestamps) keeps its first turn and adds no time |
| Repair | `python -m flightdeck.backfill [--apply]`: re-scan, heuristic collapse for gone transcripts, busy-time estimate for gone sessions |
| Labels | `python -m flightdeck.tasks [--apply] [--no-remote]`, and a loop in the service every `TOKEN_AUDIT_TASK_LABEL_INTERVAL` seconds (off when unset). Jev key read from `TYPESAFE_ENV_FILE`; codex path from `TOKEN_AUDIT_CODEX_BIN` |
| API | `GET /api/tasks?range=`, `POST /api/tasks/{turn_id}/label`; `/api/daily` gains `tokens`, `cache_write`, `agent_hours`, `agent_hours_sub`, `agent_hours_est` |
| UI | Spend view: Token trend, Work-hours trend (main / subagents / estimated), Cost per task with a review queue |
| Thinking (2026-09-28) | `response_timing` (derived, one row per response): request start = the record written just before the response's first line, end of thinking = the thinking block's line, `thinking_tokens` from usage, effort (`perTurnEffort` or `effort`). Filled in the ingest pass; an empty table triggers one full re-scan. `GET /api/thinking?range=&model=&effort=&agent=all|main|sub` returns per-day responses, share that thought, avg/p50/p90 time and avg tokens over responses that thought. Spend view: Thinking panel with those filters. Time includes the wait for the first token; transcripts hold no thinking text, and sessions whose transcript is gone have no timing |
| Burn rate (2026-09-24) | Every `/usage` poll is stored in `quota_readings` (logged table). The poll runs every `TOKEN_AUDIT_POLL_INTERVAL` seconds, or every `TOKEN_AUDIT_POLL_ACTIVE_INTERVAL` seconds within 10 minutes of a transcript change or a meter move; it skips MCP servers and writes no session file. `GET /api/burn?meter=session\|week\|<model>&range=` returns tick-to-tick spans (default 10 session points, 2 weekly), each with list-price cost by model, tokens by type, requests, sessions, subagent share and agent-minutes, flagged `coarse_edge`, `no_ledger`, `limit_change` or `unpriced`. `GET /api/burn/stats?range=&window=` feeds the Burn rate block under Cost concentration: meter points per hour over the last hour (weekly meter over 6 h), the ledger's prediction, and for each model in the Claude Code picker (`burn.LISTED_MODELS`), sorted by current burn, its weight (`burn.MODEL_WEIGHTS`, Opus 5.5 = 1), M tokens per meter point and current burn; the ledger total still counts every model. Across a reset or drop, the points between two readings are estimated from the ledger and kept between the new reading and new + 100 − old |

### Live results (2026-09-23)

- All time: 46,936 billed rows, **$11,614** API-equivalent, **$67,749** net cache savings. Backups in `~/.flightdeck/backups/`: the whole database before the first repair (`flightdeck-2026-09-23-pre-dedupe.dump`) and the ledger tables before the prompt-rule fix (`…-pre-idefix.dump`). Later re-scans were repeat reads of the same transcripts.
- 1,671 turns; 70% of billed rows carry a turn. The rest are sessions whose transcript is gone, so cost per task covers only sessions still on disk.
- Labels, all time: rule 195, Jev 900, GPT-6 Luna 574, 2 not yet labeled. Kinds: 282 task, 1,336 sub-task, 3 merge, 43 automated, 5 empty. Review queue: 72 (47 in the last 30 days).
- Last 30 days: 221 tasks, median $5.02, p90 $54.15 per task.
- Agent-hours: 230 h from transcripts, 133 h estimated for gone sessions; 8.65 h per day over 30 days.

### Limits

- **Labels are not validated.** No hand-labeled set exists yet. The 0.8 gate was set from Jev–Luna disagreement on the first run, not from accuracy. The review queue is where that set comes from.
- **Long sessions drift into one task.** Jev calls most prompts in a long session a sub-task, so a session that changes topic can pile unrelated work under its first task.
- **The `messages` table was UNLOGGED, and it was lost.** Crash recovery on 2026-09-29 emptied it (and on 2026-08-25 before that); the running service's file cache then kept 520 of 671 transcripts unread, so the dashboard showed 12 sessions. Repaired 2026-10-01: `messages` and `response_timing` are now LOGGED (schema in `db.py`), a restart re-read every transcript (140 sessions, $8,746), and `scripts/cost-audit/merge_dump_rows.py` put back 15,508 billed rows of 83 sessions with no transcript left from `…-pre-idefix.dump` ($4,186; 7 rows, $2.63, may double-count). Result: 223 sessions, $12,933 from 2026-07-10 (`agent_activity` knows 221). Rows written after the dump for a transcript deleted before the restart could not be recovered; the session counts suggest there are few or none. Backup before the repair: `flightdeck-2026-10-01-pre-repair.dump`.
- The tests run on SQLite only; the Postgres path was exercised by the live runs, not by the suite.
- The Plane view's own cost trend (`SpendComposed.jsx`) and the Odoo module's prompt rule were not changed.

## Reproduce

```bash
cd backend
URL=$(tr '\0' '\n' < /proc/$(systemctl --user show flightdeck.service -p MainPID --value)/environ \
      | sed -n 's/^TOKEN_AUDIT_DATABASE_URL=//p') ../.venv/bin/python ../scripts/cost-audit/audit_cost.py
python3 ../scripts/cost-audit/heuristic_check.py     # heuristic key vs (message.id, requestId) on disk
CAP=900 python3 ../scripts/cost-audit/tasks_workhours.py
```

All three open the ledger or transcripts read-only.
