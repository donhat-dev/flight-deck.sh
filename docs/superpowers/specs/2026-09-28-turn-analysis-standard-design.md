# Turn analysis standard

Date: 2026-09-28. Status: layers 0–2 built, layer 3 probed, layers 4–5 not started.
Probes: `scripts/turn_probe.py`, `scripts/turn_review_probe.py`.
The "next turn is a correction" trigger is replaced by the Feedback labels of
[turn labels: Type and Feedback](2026-09-28-turn-feedback-labels-design.md) (designed, not built).

## What it is for

1. **A ruler across model releases.** A new model arrives every two to four weeks. The same
   few numbers per turn — tool calls, avoidable errors, guard hits, loops, work-hours — have
   to stay comparable from one model to the next.
2. **Direction for tuning.** Which tools, rules, skills and hooks make the agent slower or
   make it fail, so a person can change them and see whether the change worked.

One rule follows from putting both in one standard: only the deterministic layers (0–2)
are the ruler. The analyzer in layer 3 is itself a model that changes every few weeks, so
its verdicts guide tuning and are compared over time only with the analyzer model and the
rubric version held fixed. Both are stored on every review.

## The layers

| Layer | What it holds | How it is made | Where it lives | What it is used for |
|---|---|---|---|---|
| 0 Integrity | Every turn has an end, a model, and its subagent files attached | Checks before any metric; a failing turn is repaired or left out | Flags on `flightdeck.turn` | Numbers that can be trusted |
| 1 Facts | Per call: tool, MCP server, real duration (call line → result line), outcome, exact repeat (whole input), main or subagent. Per turn: calls, work-hours, steers, interruptions, model | Read from the transcript when the turn ends, in the job that already labels it | `flightdeck.turn.step`, one row per call (revives or replaces `flightdeck.tool.call`); fields on the turn | The Gantt; the ruler |
| 2 Classes | Outcome family per call: avoidable, failed, guard, environment, expected, person; a sub-class under each | Rules on the result text, versioned | On the step row, with the rule version | Headline numbers: avoidable errors per call, guard hits per turn |
| 3 Review | Approach (direct, exploratory, detour), wasted steps, avoidable errors with their cause, tool misuse, what would have saved time. Each suggestion names its target: a CLAUDE.md rule, a skill, a hook rule, a tool, the prompt | A third agent reads a bounded digest of the turn; only triggered or sampled turns | `flightdeck.turn.review`, with rubric version, analyzer model and cost | Tuning direction |
| 4 Trend | By model, week and work type: calls p50/p90, avoidable per call, guard hits per turn, exact repeats, work-hours, adjustments per task, review scores | `model_stats.compare` over the weeks both models ran, as the by-model page does now | Computed | Watching a new release |
| 5 Loop | One improvement: its target, the change, the class it should reduce, that class before and after | A person decides; the numbers come from layers 1–2 | `flightdeck.improvement` | Knowing whether a fix worked |

**The families in layer 2.**
- **Avoidable:** the agent's own mistake. Bad tool input, an edit whose text is not in the
  file, a write before a read, a guessed path.
- **Failed:** a command that ran and failed. It can be a mistake, a script being iterated
  on, or an intended red test. Rules cannot tell these apart, so the family stays neutral
  and the review judges it.
- **Guard:** a hook, the auto-mode classifier, or a person said no.
- **Environment:** a timeout, a service or browser that was not there.
- **Expected:** "nothing matched" answers, such as `grep` with exit code 1.
- **Person:** the turn was interrupted.

**The review's input** is a digest, not the transcript. It holds:
- the prompt and its labels;
- one line per step: tool, the first 120 characters of the input, duration, outcome, and
  up to 300 characters of error text;
- subagent reports;
- the final reply, and the next human turn, which may be a correction.

Its output is structured JSON, so the reviews can be counted and grouped.

**When a turn is reviewed:**
- 3 or more avoidable errors;
- one exact call repeated 3 or more times;
- a correction follows the turn;
- more calls than the 90th percentile for its work type;
- about 5% of other turns at random, as a baseline;
- the first 20 tasks of each new model.

## Order

1. Build layers 0–2 (done). Point the Gantt at them: real durations, errors drawn as errors, and
   repeats keyed on the whole input.
2. Run layer 3 as a pilot on about 30 turns. Compare its suggestions with what a person would
   change.
3. Build layer 4 on the existing by-model page. Layer 5 waits until there are fixes to follow.

**What would shrink the pilot:** if layers 1–2 already explain most of the friction (for
example, the guard hits come from one habit that one rule fixes), the review is needed only
for the failed-command family.

## Built (2026-09-28)

- `models/step_reader.py` (no Odoo): reads a turn's byte range and the subagent files its
  Agent calls name (`toolUseResult.agentId` → `<session>/subagents/agent-<id>.jsonl`, nested
  up to 3 levels), pairs each call with its result, and sorts errors into classes and
  families (`RULES_VERSION = 2`). It also builds the review digest, with passwords, tokens,
  keys in share links and long key-like strings masked.
- `models/turn_step.py`: `flightdeck.turn.step`, one row per call; turn fields
  (tool calls, by subagents, avoidable, failed, blocked, environment, exact repeats, most
  repeats of one call, tool seconds, interruptions, `steps_state`). Cron "read tool calls of
  ended turns" every 5 minutes; a turn with a background agent is read again 30 minutes
  after it ends. A turn with no recorded end gets the next turn's start as its end.
- Tasks > Tool calls: pivot model × family, list, weekly graph. Turn form: the numbers in
  "This turn" and a Tool calls tab.
- Gantt: repeats keyed on the whole input; each bar's solid part is the tool running, the
  rest is thinking or waiting; failed calls are outlined red, blocked ones amber; the head
  counts errors, blocked calls and exact repeats.

Known limits:
- The Gantt finds a call's tool time and family through the transcript line's
  `tool_use_id`, which is the line's first call only; parallel calls on one line get neither.
- Digest step numbers start at 1; `flightdeck.turn.step.sequence` starts at 0.
- A turn whose transcript is gone (`no_file`) is not tried again.
- Rules v3 (same day): the turn's own model is stored in `turn_model` ("Answered by");
  `model` on a turn is the task's, which the rollup sets on task rows only.
- Rules v2 (same day): the CLI wraps every tool error in `<tool_use_error>`, which v1 read as
  bad input and so never counted edit misses or writes before a read. v2 sorts by the text
  inside; all turns were read again. Family totals did not change.

Backfill: 1,540 turns read, 55,498 calls (12,816 by subagents), 198 turns whose transcript
is gone. Work turns by model, calls p50/p90 and rates per call:

| Model | Turns | Calls p50 / p90 | Avoidable | Failed | Blocked | Exact repeats | By subagents |
|---|---|---|---|---|---|---|---|
| Opus 5 | 746 | 11 / 67 | 0.79% | 1.16% | 0.84% | 1.46% | 20% |
| Fable 5.1 | 132 | 14 / 104 | 0.86% | 1.41% | 0.93% | 2.31% | 43% |
| Opus 5.5 | 116 | 11 / 97 | 0.42% | 1.25% | 0.78% | 1.53% | 22% |
| Opus 4.8 | 106 | 11 / 46 | 0.39% | 0.86% | 0.47% | 4.41% | 35% |

Not controlled for task mix or period; layer 4 compares over shared weeks by work type.

## Layer 3 probe (2026-09-28)

`scripts/turn_review_probe.py` picked 9 turns from the last 21 days by the triggers above,
took each turn's digest (`flightdeck.turn._analysis_digest`), and had Codex review it once:
`codex exec -m gpt-6-luna`, low effort, read-only, rubric v1, strict JSON schema. Nothing is
stored in Flightdoo. Digests and reviews: `devtools_mcp_trace/flightdeck-turn-analysis-2026-09-28/`.

| Turn | Trigger | Approach | The suggestion worth acting on |
|---|---|---|---|
| 2061 | blocked ≥ 2 | exploratory | claude_md: a classifier or hook denial is final for that action; do not reach the same effect another way |
| 2062 | avoidable ≥ 3 | exploratory | tool: give a subagent the file list of the skill it reads; it guessed three reference paths |
| 1977 | repeat ≥ 3 | detour | prompt: a multi-agent evaluation harness was built that the skill did not need |
| 1062 | repeat ≥ 3 | exploratory | skill: browser tests refresh the snapshot after a failed click and retry with the new uid |
| 899 | random | detour | tool: look at a table's columns before ad-hoc SQL (a query asked for a column that does not exist) |
| 203 | avoidable ≥ 3 | exploratory | tool: a benchmark runner that runs one state at a time with a short summary |
| 1899 | calls > p90 | exploratory | tool: a browser helper that waits for a panel before clicking its tabs (confidence 0.43) |
| 2064 | correction next | direct | none: the correction was about the recommendation, not the tools |
| 2070 | correction next | direct | none: the next turn only fixed naming |

What the probe says about layer 3:
- **It adds what rules cannot.** Of the 11 failed commands it judged, 5 were iteration, 4
  environment, 2 mistakes. It named detours (2 of 9) and tied suggestions to steps.
- **The digest is too thin in two places.** 6 of 9 reviews say the final reply was cut or
  steps were left out; confidence drops to 0.43 on the largest turn. Digest v2: the whole
  final reply up to about 4,000 characters, and the two steps around each failure kept.
- **Rubric v1 lets "none" fill a slot** (6 of 15 suggestions). v2: an empty list when there
  is nothing to change.
- **It can mislabel a guard.** On 2062 it called a blocked read-only command "environment";
  the rules had it right as guard. Families stay with the rules; the review judges only
  the failed ones.
- **Cost and speed.** 23–42 s per turn (mean 31 s), about 28,000 tokens each. Astra
  (`gpt-6-astra`, low and medium effort) did not finish a review of the same digest within
  10 minutes, twice, so it is not usable through `codex exec` for this yet.

### Same input, two analyzers: turn 2075

Input: rubric v1 (1,840 characters) and the digest (12,380: header, numbers, 61 step lines,
final reply cut at 1,500, next person turn). Output: the same JSON schema, both valid.
Files: `devtools_mcp_trace/flightdeck-turn-analysis-2026-09-28/turn-2075-opus-vs-luna/`.

| | Opus (subagent, `opus`) | Luna (`gpt-6-luna`, low) |
|---|---|---|
| Time, tokens | 83 s, about 65,000 | 40 s, about 17,000 |
| Approach | direct: followed the agreed plan | detour: "broadened the scope" (it could not see the earlier agreement) |
| Step 41 (connection reset) | mistake: no wait for Odoo after the restart; step 42 added it | environment |
| Time went to | model; 70 s waiting on the person at step 2; a 2.2-min gap before the summary | mixed; cannot tell thinking from waiting |
| Tool use | edits through Python heredocs, not Edit: no reviewable diff; reads through sed, not Read | edits through heredocs are hard to review |
| Suggestions | skill fix the next turn asked for; an upgrade-and-wait script; an AGENTS.md line on Edit vs scripts | the same skill fix; "state the repositories in the prompt"; a readiness check |

Opus reads the timeline more closely and gives more concrete targets; Luna is half the time
and a quarter of the tokens. Neither can know that another session restarted the shared
container during the turn: the digest has no events from outside the turn.

### Choosing the analyzer with Jev

`scripts/turn_route_probe.py` cuts a routing state (about 1,000–3,600 characters) from each
digest: the header, the failed, blocked and repeated calls, the head of the reply and the
next person turn. Jev (`jev-1.13.0`) answers one choice, luna / sol / opus. A clear answer
(top ≥ 0.8, margin ≥ 0.3) is kept; otherwise the stronger of the top two is taken. A fixed
rule runs beside it: guard hit or next turn a correction → opus; any failure, repeat ≥ 3 or
≥ 80 calls → sol; else luna. Luna ran on all nine turns as the baseline, Sol on the turns
where Jev's top was sol. Files: `devtools_mcp_trace/flightdeck-turn-analysis-2026-09-28/jev-routing/`.

| Turn | Shape | Jev luna / sol / opus | Pick | Rule | What the pick added over the cheaper review |
|---|---|---|---|---|---|
| 2089 | 9 calls, clean | .95 / 0 / .05 | luna | luna | — |
| 2085 | 46 calls, clean | .99 / 0 / .01 | luna | luna | — |
| 2092 | 1 failed | .06 / .88 / .06 | sol | sol | a test that resets its accounts, which the next turn asked for |
| 2068 | 1 failed | .04 / .73 / .23 | opus (gate) | sol | little: same verdict as Sol |
| 2083 | 99 calls, guard | .01 / .55 / .44 | opus (gate) | opus | two runs cut at the 600 s tool cap; a rule false positive (`file_missing` on a log search); Sol instead found a scope detour |
| 2088 | no errors, correction next | .32 / 0 / .68 | opus (gate) | opus | why the correction came: a removed rule left nothing in its place |
| 2087 | 198 calls, correction next | 0 / .02 / .98 | opus | opus | the layout the next turn corrected was never specified |
| 2062 | 3 avoidable, guard | .14 / .35 / .51 | opus (gate) | opus | the `dcg` block kept as a guard; Luna again called it environment |
| 2075 | 1 failed, correction next | .06 / .77 / .17 | opus (gate) | opus | step 41 a mistake (no readiness wait; earlier Opus run); Sol and Luna said environment |

What it shows:
- **This question is rule-shaped.** The criteria restate the rule in prose, and every
  signal they name is already a number or a label in the digest, so Jev matching the rule
  was the expected result: its 4 clear answers all match. Jev adds nothing here.
- **The Opus spend comes from the doubt policy, not from Jev.** "Stronger of the top two"
  sent all 5 doubtful turns to Opus. With the rule as the tiebreaker instead, 2068 goes to
  Sol, where Opus bought nothing.
- **Where Jev's top differed from the rule:** on 2075 the rule found more (Jev read the
  next turn as a skill-change request; only Opus, in the earlier run, called step 41 a
  mistake). On 2083 Jev was a coin flip and both picks paid, differently: Sol called the
  NUL fix a scope detour, Opus found the tool-cap cuts and the `file_missing` false
  positive. 2083 is the reviewing session's own turn, so that one is judged by the person.
- **Sol beats Luna at about the same time** (30–54 s against 20–46 s; 4 turns only): no
  "none" suggestions (Luna: 3), and it was the only one to call 2083's side fix a scope
  detour. It still called 2075's step 41 environment. Luna gave 5/5/5 to two turns the
  person corrected next.
- **Opus is 83–174 s.** Token counts do not compare: Codex reported 6.8k to 58k for Luna
  on digests of similar size, and the Opus subagent's 62–77k includes about 45k of its own
  prompt, which carries the workspace rules; Codex runs in an empty directory. Part of
  Opus's edge is that context: Luna suggested not writing `MEMORY.md`, which the workspace
  requires. Rubric v2 should give every analyzer the same standing rules.
- **Scores do not separate turns** (3–5 everywhere); the findings do.

Next: route by the rule, with Sol as the default for failures and Opus for corrections and
guard hits; skip clean turns. Jev would earn a place only on a question the numbers cannot
answer, such as whether the next turn rejects the result or asks for new work, and only
after a larger sample shows the rule getting that wrong.

## What the probe found (75 days, main agent only)

- 1,589 turns and 91,763 calls read. 60 turns had no end; the probe ends them at the next
  turn.
- The Gantt's red rows are not errors: they mark a repeat, keyed on the first 80 characters
  of the input. On the turn in question, all 27 red rows were different calls with a common
  prefix; there were no exact repeats.
- Errors in work turns: failed commands 523, avoidable 297, guard 296 (mostly `dcg` blocks),
  environment 102, expected 60.
