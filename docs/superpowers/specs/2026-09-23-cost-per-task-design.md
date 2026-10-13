# Cost per task in the Odoo FlightDeck module — design

Date: 2026-09-23. Status: approved in chat; implemented the same day (see "As built").
Scope: `odoo/flight_deck` (Odoo 19) and `docker/odoo19`. The FastAPI backend is not changed.

## Goal

Show what each piece of work cost. A task is one goal a person gave an agent,
with its follow-ups. Each human prompt (a turn) is labelled `task`, `subtask`
or `no_work`, the same mechanism as `backend/flightdeck/tasks.py`: rules first,
then TypeSafe Jev, then GPT-6 Luna when Jev is unsure, and a person for the
conflicts. Cost, tokens and agent work-hours roll up from turns to their task.

Decided by the user:

- Fast LLM: GPT-6 Luna through `codex exec`, as in the backend.
- Backend labels are imported once; Odoo labels new turns itself afterwards.
- Subagent spend goes into the Odoo ledger (it was missing: about 20% of
  September's cost).
- Tags are set automatically: ticket keys by regex, work type by Jev/Luna.
- Only `task` and `subtask` are classifier outcomes; `merge` is dropped.
  `no_work` (automated or empty) is set by rule.

## 1. Ledger data

- **Subagent spend.** Every `<project>/<session>/subagents/**/agent-*.jsonl` is
  read for billed rows with its own position (`spend_bytes` on
  `flightdeck.activity.cursor`). Rows are stored under the parent session;
  `flightdeck.message.agent_key` names the agent (empty for the main agent).
- **Prompt per message.** Every `user` line carries `promptId`, tool results
  included. An assistant row takes the `promptId` of the nearest `user` line
  before it in the same file (`transcript_reader.read_usage` returns it).
  Stored in `flightdeck.message.prompt_id`. A rescan fills it in on rows already
  stored, without adding rows.
- **Turn meta.** The activity reader also records `cwd`, `git_branch`,
  `prompt_text` (2,000 chars), `final_head` (the agent's last text, 400 chars),
  `ended_at`, and `byte_start`/`byte_end` of the turn in its file.

## 2. Model: `flightdeck.turn`, extended

One record per human prompt; the model already exists for copy detection.

```text
kind              Selection  task | subtask | no_work
parent_id         M2o self   the turn a subtask continues
task_id           M2o self   the root this turn counts under (itself for a task); turn_ids O2m
title             Char
label_source      Selection  rule | jev | llm | human
label_confidence  Float
needs_review      Boolean;   review_reason Char;  labeled_at Datetime
jev_kind, jev_top1, jev_margin, jev_probs (Json)
llm_kind, llm_confidence
tag_ids           M2m flightdeck.task.tag
own_cost, own_output, own_tokens, own_calls, own_hours      this turn only
cost, output, tokens, calls, hours, turn_count              task totals (on roots)
session_ref_id, project_id, started_at, ended_at, cwd, git_branch
prompt_text, final_head, byte_start, byte_end
```

A human label is never overwritten by a later pass. `task_id` is resolved by
following `parent_id` to a turn that is not a subtask; a subtask with no parent
is its own task.

## 3. Classifier (`models/task_classifier.py`, free of Odoo)

Cron every 15 minutes over turns that ended, or started more than 2 hours ago,
and have no label:

1. **Rule.** cwd under `/tmp` or no billed row → `no_work`. A prompt of at most
   24 characters on one line, within 30 minutes of the previous turn in the
   session → `subtask` of that turn.
2. **Jev.** One `systemone` call with two choice questions: `kind`
   (task/subtask) and `work` (the work tags that have a description, plus
   `none`). Accepted when top1 ≥ 0.8 and margin ≥ 0.3 (the backend's
   calibration). A clear `subtask` with no earlier turn in its session needs an
   id, so it goes on to Luna.
3. **Luna.** `codex exec -m gpt-6-luna` with an output schema, 15 turns per
   call, 3 calls in parallel. It returns kind, parent id (only ids it was
   offered), title, work tag and confidence.
4. **Review** when Luna's confidence is below 0.7, when it contradicts a clear
   Jev answer, or when its parent id was not offered. The queue is the turns
   with `needs_review` and a source other than `human`.

Text passes through `redact()` (the backend's patterns) before it leaves the
machine. Every engine call is stored in `flightdeck.label.run`.

## 4. Tags: `flightdeck.task.tag`

`name`, `code` (ticket key, unique), `kind` (ticket | work), `description`
(what the classifier reads), `color`, `active`.

- Ticket tags: `\b(PREFIX)-\d+\b` over prompt text, git branch and session title,
  prefixes from `flight_deck.ticket_prefixes` (default `CRM`). A new key creates
  a tag named after the key; a person renames it.
- Work tags: seeded Harness, Research, Infrastructure, Bug, each with a
  description. The classifier picks at most one; when unsure it picks none, and
  that is not a review.

## 5. Views

- **Tasks > Cost per task**: list of root tasks (`task_id = id`, kind not
  `no_work`), newest first by default, with a panel above it (`js_class`):
  task count, median and p90 cost, unlabelled count, and the review queue as
  cards with task / subtask / no-work buttons.
- Task form: header buttons (Mark task, Mark subtask, No work, Classify again),
  meta, tags, subtasks, classification detail, engine runs, transcript.
- **Tasks > Turns**, **Review queue**, **Tags**, **Engine runs**
  (list/graph/pivot).

## 6. Transcript widget for one task

Reads the session file between the turn's `byte_start` and `byte_end` and
renders prompt, assistant text and tool calls with the module's markdown
renderer. Does not depend on `flightdeck.message.text` (58 of 110 sessions with
turns have lines there). On a task with subtasks, a picker switches between its
turns. A file no longer on disk is said so.

## 7. Jev analytics and LLM analytics

Two full-page forms on a transient model `flightdeck.label.probe`:

- Stats over the last 30 days of `flightdeck.label.run` (probes excluded):
  calls, accepted share, flat share, agreement with Luna / Jev and with human
  labels, median latency.
- Try it: pick a turn, or type a prompt and its context, press Run. The answer
  (probabilities or Luna's JSON, the text sent, latency) is shown and stored as
  a run with `probe = True`. No label is written.

## 8. Import from the backend

`odoo/migration/import_task_labels.py`, run once in the container. Maps
`turns.turn_id` to `flightdeck.turn.prompt_id` and copies kind, parent, title,
source, confidence and review flag. `merge` becomes `task` and keeps its
original kind in the detail. Needs `GRANT SELECT ON turns TO fdreader`.

## Infrastructure

- The image installs the pinned standalone codex binary; `CODEX_HOME` sits in
  the odoo-data volume. Sign in once:
  `docker exec -it flightdeck-odoo19 codex login --device-auth`.
- `~/.config/jev-browser/env` is mounted read-only; `TYPESAFE_ENV_FILE` points
  at it.

## Not doing

Merge labels, syncing labels back to the backend, new tags created by an
engine, review of tag disagreements.

## Checks

- Classifier rules, redaction, parent resolution and prompt attribution tested
  without Odoo.
- A labelling pass on a sample compared with the imported backend labels.
- UI checked with headless Playwright; screenshots under `devtools_mcp_trace/`.

## As built (2026-09-23)

Differences from the design above, and what was added while building it:

- Imported labels keep their backend source (rule, jev, llm); `label_detail`
  holds `imported: true` and the backend kind. No `import` source.
- A turn Jev was unsure of keeps Jev's answer on the turn and waits for Luna;
  the next pass sends it to Luna without asking Jev again. The pass checks
  `codex login status` first and skips Luna when codex is not signed in.
- **Pick work tag** (list button, `action_pick_work_tag`): asks Jev only the
  work question, for turns labelled before work tags existed. Used once for the
  305 imported root tasks; a flat answer leaves the turn untagged.
- The busy-time reader locks a file's cursor row (`FOR UPDATE SKIP LOCKED`)
  while it reads: the cron and the follow tick could otherwise both add the
  same busy time.
- A work-tag-only Jev call is kept out of the Jev kind figures.
- Titles skip lines that are only a tag (the `<pasted_content>` wrapper).
- Codex needs `CODEX_HOME` to exist before it starts; the image creates it.
- A billed row counts under the turn with its prompt id, or else under the
  session's turn that was running when it was written (a task notification
  starts a prompt id no person wrote). The rows imported from the old ledger
  by line uuid (2-3 per call) were replaced with the backend's deduplicated
  calls by `migration/dedupe_imported_ledger.py`; a row from the backend is
  now told apart by its uuid shape (a line uuid, not `msg_…`).
- A compaction summary line is not a prompt (it carries `turnOrigin: human`
  and the promptId it continues). A steer typed while the agent works is a
  `queued_command` attachment and is not read yet.
- `duration` on a turn: hours from the prompt to the last reply, clamped at 0.
- An agent file whose prompt id is not a turn is tied to the person's turn
  running when the agent started.
- A fifth work tag, Feature, is seeded next to the four named ones.
- Known edge: an agent started by a task notification, running in the
  background past the next prompt, has its hours on the turn it started in and
  its later calls' cost on the turn running when each call was made.

## Intent labels (approved in chat 2026-09-23, built the same day)

Task and subtask could not say how many tasks a session or ticket did, or how
often a person had to redirect the agent. A turn now has an **intent**, and
`kind` follows from it for the rollup:

| intent | meaning | kind | first decided by |
|---|---|---|---|
| task | a deliverable no earlier turn asked for | task | Jev, then Luna for the title |
| follow_up | moves an earlier task forward without changing what was delivered | subtask | rule for "continue"/"tiếp tục", else Jev |
| correction | changes or rejects what was delivered | subtask | Jev, then Luna |
| answer | reply to the agent's question or proposal | subtask | rule: previous turn ended on "?" and a short prompt within 30 min |
| question | side question; stands alone, not a task | no_work | Jev, then Luna |
| no_work | scripted (/tmp, but not Claude's `/tmp/claude-*` scratchpad) or no billed work | no_work | rule |

- **Steer messages** (typed while the agent works) are `queued_command`
  attachments with no prompt id. The activity reader stores them in
  `flightdeck.turn.steer` on the running turn; resumed copies are skipped.
  The first backfill found 87 on 68 turns.
- **Parent**: the session's latest task by default; Luna may name another
  offered id. **Title**: Luna, once per task, Verb + Object + Context. A task
  labelled while codex is not signed in gets its title on a later pass.
- **Work tag**: one per task. `path_pattern` on a tag is checked first
  (Harness: `flight-deck.sh,nakivo-graph,/.claude`), then Jev/Luna. The Turns
  list shows the task's tags.
- **Metrics on a task**: follow_ups, corrections, answers, steers,
  `adjustments` = corrections + steers (averaged when grouped), and `model` =
  the main agent's model with the most output. Tasks > Adjustments per task
  graphs it by week and model.
- **Reader fixes**: a turn's start and prompt are written by the first read
  only; `ended_at` is capped at the next prompt's start, and `duration` is
  set by the rollup from the capped end.
- **Migration** 19.0.1.3.0: task and no_work keep their meaning; old
  subtasks have no intent until labelled again.
- **Sample before relabelling history**: five whole sessions (58 turns)
  labelled and queued as "Sample check". The user accepted it without a
  per-turn review (so the 85%/90% accuracy line was not measured), and all
  other turns were labelled again on 2026-09-24; the labels before that are
  in the session scratchpad (`turn_labels_backup_20260924.csv`).
- **Transactions**: with `commit=True` (the cron, batch runs) `_fd_label`
  commits after the engine calls and before writing. Writing on the snapshot
  taken before minutes of calls failed with a serialization error whenever a
  reader had updated a live turn meanwhile.
- **Parents Luna saw in another item**: Luna reads fifteen items at once; a
  parent id that is an earlier turn of the same session is accepted even
  when it was not in that item's own context. Jev's accept line (0.8/0.3) was calibrated on two
  choices and has not been measured on five.


## Session tasks, review queue, by-model page (2026-09-25)

- **Tasks tab on the logbook form**: the session's turns (`flightdeck.session.turn_ids`)
  as full-width kanban rows, styled like the automation rules list
  (`o_fd_turn_kanban`). Bold line: the task title, or the type for a turn
  without one; below it the prompt, cut at two lines. A turn with a parent
  (follow-up, correction or answer) is set in. Newest task first, each
  followed by its turns in time order (`default_order="task_id, started_at, id"`;
  the link sorts by the turn model's newest-first order): in plain time order
  454 of 1,069 continuing turns would sit under a side question or another
  task instead of their own.
- **Review queue**: the 159 turns where Jev said task and Luna said follow-up
  keep Luna's label and leave the queue (`label_detail.review_cleared`); 3 of
  them stay for an unoffered parent. 45 turns remain. The queue before this
  is in the session scratchpad (`review_queue_backup_20260925.csv`).
- **Tasks > Adjustments per task** is now a page (`flight_deck.model_stats`,
  `flightdeck.turn.fd_model_stats`, figures in `models/model_stats.py`):
  - A model and a baseline compared over the weeks both ran, per task:
    adjustments, corrections, follow-ups, steer messages. The ratio is
    model ÷ baseline. "Fewer"/"More" only when the two 95% ranges do not
    overlap.
  - Ranges: normal approximation, variance at least the mean, and up to 3/n
    for a run of zeros, so a few tasks that agree never look certain.
  - Chart: one measure per model and week. A model's week with fewer than
    5 tasks is a gap. A model's colour is its place in the order models were
    first used (among models with 5 tasks or more), so a range change never
    repaints one. The palette is checked with the dataviz validator; three
    slots are under 3:1 contrast, so the table names every coloured model.
    Seven of eight slots are taken; the next model to reach 5 tasks takes the
    last, and any model after that is in the table only. If a new model
    must always have a line, free the slot of the oldest model with no task
    in 13 weeks.
  - Table: every model in the range, with its first task ever.
  - The old graph and pivot are one click away (Pivot).
  - Defaults: the newest model with 10 tasks or more, against the busiest
    other model.

## Labels right after a turn (2026-09-25)

- **Why labels lagged**: the label cron waited until a session's whole file had
  its spend read. Main-file spend is read by the 20-minute scan, and a live
  session's file grows between two scans, so the cron labelled nothing while
  a person kept working (turn 1746 stayed unlabelled for 35 minutes, the
  passes at 04:01 and 04:16 found nothing).
- **Trigger**: the follow tick (every minute or sooner, live sessions only)
  queues `flightdeck.session._fd_label_live` when a turn of the session ended
  in the last 10 minutes and has no label (`identity_key` per session). No
  Claude Code hook: the follow tick already reads `end_turn` within a minute,
  with no API key and no change to `~/.claude/settings.json`.
- **The job**: reads the session's spend first, runs the rollup (own calls,
  which the "no billed work" rule reads), then labels the ended turns.
  Measured on turn 1746: queued to labelled in 10 s (Jev, then Luna for the
  title).
- **The cron** now queues its pass as a job (`_fd_label_pass`). With
  `channels = root:1` the two label jobs and the spend scans run one at a
  time. Its gate also takes a turn whose own lines were read
  (`spend_bytes >= byte_end`), not only a fully read file.
- Both label jobs have `allow_commit` on their job function: queue_job runs
  them on a cursor of their own, and `_fd_label` commits between its engine
  calls and its writes. `queue_job` is now in the manifest's depends (it was
  used through `with_delay` without it).
- **Seen live**: turn 1750 (session 2626) ended 04:37:08.9, its job was queued
  0.6 s later and the label written at 04:37:18. Turn 1749's first job hit a
  serialization error on the session row: a spend read that found nothing
  still wrote `spend_bytes`, and that write was held through the engine
  calls. queue_job retried it and a second job labelled the turn. The job
  now commits after the spend read and after the rollup, before any engine
  call. Two follow ticks a moment apart can both queue a job (the identity
  check sees neither uncommitted row); the second finds nothing to do.
- Not covered: a session's first turns before the 20-minute scan discovers
  its file (the cron labels those), and a turn interrupted without
  `end_turn` (it ends when the next prompt starts).

## Labels when the prompt arrives (2026-09-25, replaces "after a turn")

The user expects a turn labelled right after the prompt is submitted and read,
not when the turn ends.

- **Read**: already under a second. The file watcher wakes the follow cron on
  a write (turn 1751: prompt line 05:25:35.053, row created 05:25:35.650). The
  follow cron now also creates the row for a new transcript, from the file
  list it already builds, so a new session's first prompt is read the same
  way instead of at the next 20-minute scan.
- **Phase 1, `flightdeck.session._fd_label_live`** (priority 5): queued by the
  follow tick when a turn has no label, no Jev answer and was not tried yet
  (`label_open`), started or ended in the last 10 minutes. Rules and Jev
  only, before the spend read and the rollup. Jev p50 is 986 ms, so the
  label lands about 1.5–2.5 s after the prompt; a rule label (go on, answer)
  sooner. The turn is marked `label_open` ("Labelled while running"). The
  spend read and the rollup follow at once (~0.6 s), so a follow-up moves
  under its task in the Tasks tab; queue_job runs a smaller priority first.
- **Phase 2, `_fd_label_luna`** (priority 20): Luna for the turns Jev was
  unsure of, a new task (title), and a follow-up with nothing to follow in its
  session. Luna p50 is 24 s. Jev's kept answer is taken as if just asked
  (`classify` now accepts a kept clear answer the same way), so Luna judges
  a Jev-labelled task as in a full pass: when they disagree Luna's label is
  kept and the turn goes to review.
- **End check**: when a `label_open` turn ends, the same job reads spend, runs
  the rollup and makes a turn with no billed call `no_work "empty"` (the
  rule's reason). The rule itself now skips "empty" while a turn runs (a
  turn that never ended counts as over after 2 hours).
- **Measured on turn 1752** (running, queued by hand): phase 1 took 1.9 s
  including the spend read and rollup (Jev unsure, 0.60), Luna 9.3 s after it
  (correction of 1749).
- **Classify again** reads spend and runs the rollup first, and commits around
  the engine calls. Before, on a running turn it gave `no_work "empty"` (turn
  1751, clicked 05:31) because no billed call was stored yet; relabelled.
- **Limits**: one job at a time (`root:1`), so a phase-1 job queued while a
  Luna job runs waits for it (up to ~25 s); priority only orders jobs that
  have not started. The form does not refresh itself: a reload shows the
  label.
