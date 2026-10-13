# Turn labels: Type and Feedback

Date: 2026-09-28. Status: step 1 built (shadow); the label check waits for the person. Decided in chat the same day: the full label
set (not the compact one), and the cost of a task may change because questions about the
work now count under it.

Changes the intent labels of [cost per task](2026-09-23-cost-per-task-design.md#intent-labels-approved-in-chat-2026-09-23-built-the-same-day)
and the triggers of the [turn analysis standard](2026-09-28-turn-analysis-standard-design.md).

## Why

The intent label answers two questions at once:

1. Where does this prompt sit in the work: a new task, the same task, or no work? The cost
   rollup needs this.
2. What does the prompt say about the result the agent just delivered? Turn analysis needs
   this.

`correction` and `answer` are answers to question 2 filed under question 1, so a prompt
that does both gets one of them. 2092 answers three of the agent's questions and asks for
a new screen in the same message.

What the labels hold now (30 days, read by hand, not checked by anyone else):

- Of the 100 latest `correction` turns, about a third say the agent was wrong (broken
  result, convention, missing fact, wrong approach). About half are the normal editing of
  a draft or a screen ("rút ngắn 1–2 line", "fix height ở 80vh"). About an eighth add
  scope. The rest stop the agent, ask it to remember a rule, or are a probe's test prompt.
  Luna's instructions ask for this: "a change to a delivered detail is a correction".
- `follow_up` hides broken results (1016) and "why" questions about a result (930).
- `question` mixes off-topic questions ("thời tiết hôm nay") with research for the work
  itself (833); both count as no work.

So "next turn is a correction", the turn analysis trigger, is mostly noise, and the
broken results filed as follow-ups are missed.

## Two labels per prompt

### Type: where the prompt sits (for the rollup)

| Value | Meaning | Kind | Was |
|---|---|---|---|
| `task` | A goal no earlier turn asked for | task | task |
| `follow_up` | The same goal: next step, more scope, a fix, a reply to the agent | subtask | follow_up, correction, answer |
| `ask` | A question about the work in progress, asking to understand, not to change | subtask | part of question |
| `side` | A question unrelated to the work | no_work | part of question |
| `no_work` | A scripted call, a probe's prompt, or a turn with no billed call | no_work | no_work |

`parent_id` stays as it is: a `follow_up` or `ask` continues a task.

What the engines read for the values that change:
- `follow_up`: moves the same goal forward in any way, including a fix to what was
  delivered and a reply to the agent's question or proposal.
- `ask`: a question about the work in progress or its result; the person wants to
  understand, and nothing is to be changed yet.
- `side`: a question with no link to the work in the session (the date, the weather, a
  general fact).

### Feedback: what the prompt says about the last result (for analysis)

One or more labels per prompt, or none. A prompt's Feedback is about the result of the
turn before it in the session. A steer message's Feedback is about the turn it arrived in.
The criterion is the text the engines read. Each label says where a repeated case gets
fixed.

**Agent wrong** (`wrong`)

| Code | Name | Criterion | Where to fix |
|---|---|---|---|
| `defect` | Defect | It says the delivered result does not work or behaves wrongly. | verification: a check before reporting done |
| `convention` | Convention miss | It fixes something a written rule, a convention or an earlier instruction of the person already covered: naming, wording, style, process. | instructions, skill, lint |
| `context` | Missing context | It states a fact the agent did not know or got wrong (how the business works, who does what, how a system behaves), and the change follows from that fact. | knowledge: DEFINITIONS.MD, facts docs |
| `approach` | Approach change | It rejects the way the agent solved it and names another way, without giving a new fact: revert, or do it differently. | planning: options before building |
| `check` | Check request | It asks the agent to test or verify work it reported as done. | verification |

**Revision** (`revise`): normal iteration, a case only when it repeats

| Code | Name | Criterion | Where to fix |
|---|---|---|---|
| `edit` | Content edit | It changes the wording, length or structure of a text the agent wrote, whose content was right. | writing rules |
| `look` | Look and feel | It changes how a screen or picture looks: layout, size, colour, order. | design profile |
| `scope` | Added scope | It adds a requirement or a next step to the same work, without saying the delivered part is wrong. | prompt, if it was knowable at the start |

**Control** (`control`): what the person had to do to follow and steer the work

| Code | Name | Criterion | Where to fix |
|---|---|---|---|
| `decision` | Decision | It answers a question the agent asked, approves what it proposed, or picks one of its options. | fewer or clearer questions |
| `explain` | Explanation request | It asks why, how or what about the agent's result, to understand it. | how replies explain |
| `status` | Status request | It asks what state the work is in or what has been done. | FlightDeck views |
| `redirect` | Redirect | It stops, pauses or switches the agent's work, or limits what it may do. | orchestration |
| `handback` | Person's action | It reports something the person did or saw outside the agent: restarted, deployed, it is live. | harness: what the agent could not do itself |
| `teach` | Standing rule | It asks the agent to remember a rule, or to add it to memory, a skill or the instructions. | check the rule landed in the right place |

No label means the prompt moves on without a word on the result.

For a steer, the engines read the turn's prompt, the agent's messages up to the moment
the steer was sent, and the steer as the new prompt.

## Model and fields

```text
flightdeck.feedback.kind   new; seeded from XML (noupdate) so the person can edit criteria
  code          Char     unique
  name          Char
  group         Selection  wrong / revise / control
  description   Text     the criterion the engines read
  fix_layer     Char     "Where to fix"
  sequence, color, active

flightdeck.turn   (inherit, models/task.py)
  intent             Selection  task / follow_up / ask / side / no_work   (values change)
  feedback_ids       Many2many flightdeck.feedback.kind   "Feedback"
  feedback_main_id   Many2one  flightdeck.feedback.kind   "Main feedback"
  feedback_source    Selection rule / jev / llm / human
  feedback_detail    Json      probabilities, margin, engine, reason
  previous_turn_id   Many2one  flightdeck.turn: the session's turn before, whose result the
                               feedback is about (stored by the feedback pass)
  feedback_state     Selection todo / done / review
  feedback_gold      Boolean   in the label check
  feedback_no_fit    Boolean   the person found no label that fits (label check only)
  -- rollup counts on a task (turns and steers of the task) --
  wrong_results      Integer   Agent wrong labels
  revisions          Integer   Revision labels
  decisions          Integer   decision
  pulls              Integer   explain + status + handback
  redirects          Integer   redirect
  rules_taught       Integer   teach
  adjustments        Integer   wrong_results + redirects   (was corrections + steers)
  -- removed: corrections, answers --

flightdeck.turn.steer   (inherit)
  feedback_ids, feedback_main_id, feedback_source, feedback_detail   as on the turn
```

Why a model for the labels and not a Selection: the person tunes a criterion in the UI
without a code change, as work tags already allow (`work_question()` builds Jev's question
from tag descriptions).

While Feedback runs in shadow, its review flag is its own (`feedback_state = review`), so
the Type review queue is unchanged. The step 3 switch moves it into `needs_review`, one
queue for both. Engine calls gain a `question` (Type or Feedback), so each label's figures
stay apart on the analytics pages.

## How a prompt is labelled

Inside the existing "Label new turns" flow; each engine stays its own step.

1. **Rules.** Type rules as today, plus `no_work` for a probe's prompt. Feedback by rule:
   `decision` from the existing answer rule (a short reply right after the agent asked).
   No keyword rules for `teach`, `status` or `handback`: Jev was clear on all three in the
   probe below.
2. **Jev.** One call per prompt with three choice questions: Type, Feedback, and the work
   tag. Feedback is one choice among the 14 labels and none. The top label is the main one
   when clear (top ≥ 0.8, margin ≥ 0.3), and Jev then gives that one label only: the
   answers of a choice add up to 1, so a clear top leaves no room for a second. A prompt
   that says two things spreads its answer (2088 below), is not clear, and goes to Luna.
3. **Luna** for what Jev was not sure of, and so for every prompt with more than one
   label. The item gains the previous turn's final message (Jev reads it already). The
   output gains `feedback`, a list of codes with the main one first, and
   `feedback_confidence`.
4. **Person.** The review queue shows the Type buttons and the Feedback labels as chips.
   The person's choice wins and is never overwritten.

Every engine call is a `flightdeck.label.run`, as now. The Jev and LLM analytics pages
gain the Feedback question, including the custom-text probe.

### Probe done while writing this (8 prompts picked by hand, 2026-09-28)

- 14 separate yes/no questions in one call: 0.5 s, about 1,500 tokens in and 400 out.
  They over-fire: `scope` and `redirect` came out "yes" on almost every prompt.
- One choice question, on a short state (previous prompt, previous reply, new prompt):
  the expected label was on top for 7 of 8, clear for 5.
- The same question on the state Jev reads in production (`state_text`: three previous
  turns, other sessions, 3.0–4.2k characters) and with the criteria above, which were
  sharpened after the first run: clear for 5 of 8, and all 5 clear answers were right.
  The 3 unsure ones would go to Luna: 2088 (`convention` .49, `teach` .34; the prompt
  changes a skill rule, so the expected label itself is arguable), 1845 "chọn c" (`none`
  .45, `decision` .37), and 1728 (`approach` .54, `context` .35, up from .19). A new fact
  and a new way are still close; the gold set decides whether they stay two labels.
- Scripts in the scratchpad (`feedback_probe*.py`, `fb3_shell.py`), not kept.

## Who reads the labels

- **Rollup.** Kind follows Type; the counts above replace corrections and answers. `ask`
  turns now count under their task, so task costs change (accepted).
- **Model stats (layer 4).** Metrics per task become wrong results, revisions, decisions,
  pulls, redirects and adjustments. The ruler changes meaning, so it compares weeks only
  after the backfill below has relabelled all of them.
- **Turn analysis (layer 3).** "Next turn is a correction" becomes "the next prompt, or a
  steer, has an Agent wrong label". Revision labels feed trends only. Control labels are
  new measures of the person's effort per task. The digest carries the next prompt's
  labels and up to 1,500 characters of it (400 now).
- **Views.** Turn list badges and filters by label and by group; a pivot of turns by
  label; the review queue; the Orchestrator shows the new Type names.

## Moving the existing labels

- Type: `correction` and `answer` become `follow_up`; source and parent stay. `question`
  is asked again (Type only), 217 turns. The old value is kept in
  `label_detail["old_intent"]`.
- Feedback: only the Feedback question is asked for every labelled turn and every steer,
  about 1,840 turns (Jev, 8 at a time, a few minutes). If about 3 in 8 are unsure, as in
  the probe, Luna gets about 700, in about 47 calls of 15. Type is asked again only for
  the 217 `question` turns.
- The rollup runs again. A report lists the tasks whose cost changed and by how much.
- A person's old label stays the person's: `correction` → `follow_up` keeps source
  `human`, and its Feedback goes to the review queue.

## Checks before the consumers switch

1. **Gold set.** 60 recent prompts are drawn and frozen before the backfill, which skips
   them. The person labels their Type and Feedback with no engine's Feedback in view; Type
   still shows the old label. The engines then label them for the comparison.
2. **Pass.** At most 10% of the gold set has no label that fits. Jev's clear answers match
   the person's main label in at least 80%, and the group in at least 90%. Two labels
   confused with each other in over 30% of their cases, and fixed in the same place, are
   merged; `defect`/`check` and `edit`/`look` are the likely pairs.
3. **Shadow.** Feedback is stored and shown but nothing reads it until the gold set
   passes. Then Type changes, and the rollup, model stats and turn analysis switch in one
   upgrade.

## Order

Plan for step 1 and the tooling of step 2: `docs/superpowers/plans/2026-09-28-turn-feedback-labels.md`.


1. Feedback in shadow: the new model, fields, the Jev and Luna questions, display. Type
   unchanged. The gold set is drawn, then the backfill runs on everything else.
2. The person labels the gold set; the criteria are tuned until it passes.
3. The Type change, the migration and the switch of every reader.

Another session is editing `models/task.py`, `models/task_classifier.py`,
`models/label_run.py` and `views/task_views.xml` (the label flow's readability). Step 1
touches all four, so it starts after that work lands.

## As built: step 1 (2026-09-28)

Feedback runs in shadow; Type and every reader are unchanged.

- Code: `models/feedback_classifier.py` (the stages, free of Odoo), `models/turn_feedback.py`
  (the label model, fields on turns and steers, the "Label feedback" flow, the label check),
  `data/feedback_data.xml` (the 14 labels, a cron every 10 minutes that queues the job),
  `views/feedback_views.xml` (Tasks > Label check, Tasks > Feedback labels, a Feedback column
  and filters on Turns, a Type/Feedback switch on the Jev and LLM analytics pages).
  `task_classifier.codex_exec()` is shared by Type and Feedback.
- Checks: `odoo/tests/` (13 pytest checks of the stages; odoo-shell checks of the pass and
  of the analytics pages; a Playwright check of the screens).
- Label check: 60 turns, a seeded random sample of the ended turns of the last 30 days (16
  days, 18 sessions, 4 projects; 22 follow-up, 19 task, 9 question, 7 correction, 3 answer),
  waiting for the person. The first draw, the 60 most recent turns, was one day of this
  feature's own work and was replaced before anyone labelled it. In Tasks > Label check the
  person ticks the labels (or none) and presses Save my label; Compare with the engines
  queues a job that asks Jev and Luna again every time; Show the figures gives the pass
  figures. A person who ticks several labels and no main one counts as agreeing with an
  engine that names any of them.
- A prompt whose turn made no billed call is asked like any other (the rule first stored
  it as saying nothing). Jev's unclear answer is kept on the record, so a pass with Luna
  away does not ask Jev again.
- Backfill, 6 passes, about 50 minutes: 1,791 turns and 89 steers labelled, 4 to review.
  Luna 1,244, Jev 360, rules 187. Jev was clear on 388 of 1,694 calls (23%), far below the
  probe's 5 of 8, so Luna does most of the work (13.9 s per batch call). 250 turns (14%)
  carry more than one label.
- Main labels: scope 668, none 316, explain 231, decision 131, context 81, edit 76, look 58,
  approach 41, check 40, convention 37, defect 35, redirect 34, status 29, teach 10,
  handback 4.
- Against the old intent: of 221 `correction` turns, 75 are Agent wrong, 124 Revision, 21
  Control, which matches the hand reading above. 101 `follow_up` turns are Agent wrong,
  the broken results the old label hid.
- To settle in the label check: 226 turns that Type calls a new task got a Revision label,
  mostly `scope`. A new task is not added scope, so the `scope` criterion may need "the
  same work" made sharper.

## As built: steps 2 and 3 (2026-09-29)

Plan: `docs/superpowers/plans/2026-09-28-turn-type-switch.md`; ledger in
`.superpowers/sdd/2026-09-28-turn-type-switch/`.

- Label check: labelled by Opus as a reference at the person's request (source
  `reference`, never shown as a person's). Round 1 failed (Jev group 82%); the `scope`,
  `explain` and none criteria were tightened with general rules. Round 2: no fit 0%, Jev
  clear on 20: main 90%, group 90%; Jev, else Luna, main 62%. Tuned on the same 60 turns.
- Feedback relabelled with the tuned criteria before the switch: 1,886 turns, 636 with
  no label. Main labels: scope 486, explain 205, decision 161, edit 81, context 58.
- Type moved in the 19.0.1.6.0 pre-migration: `correction` → `follow_up` 234 (1 a
  person's), `answer` → `follow_up` 83, `question` → `side` 223 (1 a person's). The old
  value stays in `label_detail["old_intent"]` and survives later relabels.
- The 222 engine `question` turns were asked again: `ask` 131, `follow_up` 52, `task` 21,
  `side` 18. Two `ask` turns have no parent (1207, 1338); both are the first turn of their
  session, and the rollup makes each a task of its own, which the plan meant to avoid.
- Rollup: a prompt's Feedback counts on the previous turn's task, a steer's on its own
  turn's. `adjustments` = Agent wrong + redirects; revisions, decisions, pulls and rules
  taught are counted apart. `corrections` and `answers` are gone.
- Task costs, before and after (the diff also holds about an hour of live work): 490 tasks,
  $7,992.63 → 508 tasks, $8,346.26; 151 tasks changed, $481.59 moved. Old side questions
  that are now `ask` count under their task. 7 old tasks (1119–1121, 1232, 1234, 1316,
  1339) joined an earlier task: the side question before them is now an `ask` that
  continues the work.
- Readers switched: model stats and the task screens (Agent wrong, Revisions, Decisions,
  Pulls), one review queue for Type and Feedback, the turn-analysis digest (the next
  prompt's Feedback and 1,500 characters) and its triggers (`wrong next`).
- Label check Type figure: engines match the reference Type on 71% of 59. Misses:
  follow_up read as task 7, ask as follow_up 5, ask as task 3, no_work as follow_up 2.
  No pass line was set for Type. Only the 9 former `question` turns among them were asked
  again; for the other 50 the engines' Type is the old label mapped by the migration, so
  the figure measures the mapping more than the new criteria.

## Not doing

- Changing rules, skills or hooks from the labels. That is layer 5.
- Feedback about a turn older than the previous one: it is filed on the previous turn;
  the analyzer reads the digest.
- A separate yes/no question per label (see the probe).

## Open

- How a probe's prompt is recognised. The test prompt 1615 ran in
  `~/.flightdeck/runner-home`, where real work may run later, so a directory rule is
  unsafe. Proposal: the probe code marks the sessions it starts.
