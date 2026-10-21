# Turn Type Switch (Part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Type takes its new values (task / follow_up / ask / side / no_work), the existing labels move to them, and every reader switches to Feedback: rollup counts, model stats, the review queue, and the turn-analysis digest and triggers.

**Architecture:** The Type values live in three constants of `task_classifier.py` and one Selection in `task.py`; everything else reads them. A pre-migration script moves the stored values in the same upgrade that changes the Selection. The rollup counts Feedback labels on the task whose result they judge (the previous turn's task; a steer's own turn). Old `question` turns labelled by an engine are asked their Type again.

**Tech Stack:** Odoo 19 `flight_deck`, Odoo migration scripts, OWL (review queue, model stats page), pytest for the Odoo-free code.

**Spec:** `docs/superpowers/specs/2026-09-28-turn-feedback-labels-design.md`, step 3. Part A plan: `docs/superpowers/plans/2026-09-28-turn-feedback-labels.md`.

**Gate:** starts only after the label check passes (spec, "Checks before the consumers switch"). The label check is labelled by Opus as a reference, at the person's request (2026-09-28); the spec records that.

## Global Constraints

- Everything in Part A's Global Constraints (upgrade and restart procedure, redaction, no commits, exact-match edits in the other session's files, free method names).
- Version `19.0.1.6.0`; the pre-migration runs only on the upgrade from `19.0.1.5.0`.
- Type values and labels: `task` Task, `follow_up` Follow-up, `ask` Question about the work, `side` Side question, `no_work` No work. Kinds: task → task; follow_up, ask → subtask; side, no_work → no_work.
- Old values: `correction`, `answer` → `follow_up`; `question` → `side` for a person's label, else asked again. The old value is kept in `label_detail["old_intent"]`.
- A person's Type label keeps `label_source = human`.

## Review Focus

1. **No row keeps an old value** after the upgrade (`intent in ('correction','answer','question')` counts 0), and the web client shows no empty Type. Checked in Task 2.
2. **A task's counts come from the feedback on its results**, not from the feedback its first prompt gives the task before it. Checked in Task 3 with a two-task session.
3. **`ask` turns join their task with a parent**, never becoming tasks of their own for want of one. Checked in Task 4.
4. **The review queue keeps a person's Type while showing its Feedback to review**, and resolving one does not clear the other. Checked in Task 5.
5. **Model stats compare like with like**: every week is counted under the same definition of adjustment (all turns carry Feedback before the switch). Checked in Task 6.

---

### Task 1: New Type values in the classifier

**Files:** Modify `odoo/flight_deck/models/task_classifier.py` (docstring, `INTENTS`, `CONTINUES`, `INTENT_KIND`, `KIND_QUESTION`, `LLM_INSTRUCTIONS`, `rule_label`); `odoo/flight_deck/models/feedback_classifier.py` (`rule_feedback`). Test: `odoo/tests/test_type_values.py` (new).

**Interfaces:** Produces `INTENTS = ("task", "follow_up", "ask", "side", "no_work")`, `CONTINUES = ("follow_up", "ask")`, `INTENT_KIND` as in Global Constraints; `rule_label` returns `follow_up` with reason `reply to a question` for a short reply; `rule_feedback` keys `decision` on that reason.

- [ ] **Step 1: Failing tests** — `test_type_values.py`:
```python
from datetime import datetime, timedelta, timezone

from conftest import load

tc = load("task_classifier")
fb = load("feedback_classifier")
T0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def pair(prompt, final_before):
    prev = {"id": "p1", "rec": 1, "session": 1, "start": T0, "end": T0 + timedelta(minutes=2),
            "prompt": "Build the page", "final": final_before, "cwd": "/x", "branch": "",
            "calls": 3, "intent": "task"}
    t = {"id": "p2", "rec": 2, "session": 1, "start": T0 + timedelta(minutes=5),
         "end": T0 + timedelta(minutes=8), "prompt": prompt, "final": "", "cwd": "/x",
         "branch": "", "calls": 3, "intent": None}
    return t, {"prev": [prev], "tasks": [prev], "others": [], "gap_min": 3.0, "moved": False}


def test_the_type_values_are_the_specs():
    assert tc.INTENTS == ("task", "follow_up", "ask", "side", "no_work")
    assert tc.CONTINUES == ("follow_up", "ask")
    assert tc.INTENT_KIND == {"task": "task", "follow_up": "subtask", "ask": "subtask",
                              "side": "no_work", "no_work": "no_work"}
    assert set(tc.KIND_QUESTION["criteria"]) == {"task", "follow_up", "ask", "side"}


def test_a_short_reply_to_a_question_is_a_follow_up_and_a_decision():
    t, c = pair("yes", "Done. Shall I add the export?")
    assert tc.rule_label(t, c)["intent"] == "follow_up"
    assert fb.rule_feedback(t, c)["codes"] == ["decision"]


def test_luna_is_asked_for_the_new_values_only():
    enum = tc.llm_schema({})["properties"]["labels"]["items"]["properties"]["intent"]["enum"]
    assert enum == ["task", "follow_up", "ask", "side"]
    assert '"correction"' not in tc.LLM_INSTRUCTIONS and '"answer"' not in tc.LLM_INSTRUCTIONS
```
- [ ] **Step 2: Run, see them fail** — `cd odoo/tests && python3 -m pytest -q test_type_values.py` → 3 failed.
- [ ] **Step 3: Change the classifier.**
  - Docstring intents list: task; follow_up ("the same goal: the next step, more scope, a fix to what was delivered, a reply to the agent"); ask ("a question about the work in progress or its result: to understand, not to change yet"); side ("a question unrelated to the work; not a task"); no_work unchanged. "A follow-up or a question about the work continues a task."
  - Constants as in Interfaces.
  - `KIND_QUESTION["criteria"]`: keep `task`; `follow_up`: "Moves the goal of an earlier turn forward in any way: go on, the next step, more scope, a fix or change to what was delivered, or a reply to the agent's question or proposal"; `ask`: "A question about the work in progress or its result: the person wants to understand it, and nothing is to be changed yet"; `side`: "A question with no link to the work in the session, such as the date, the weather or a general fact".
  - `LLM_INSTRUCTIONS`: the same four definitions; `parent_id` "for follow_up and ask".
  - `rule_label`: the answer branch returns `{"intent": "follow_up", "confidence": 0.85, "reason": "reply to a question"}`.
  - `feedback_classifier.rule_feedback`: `if lab["reason"] == "reply to a question": return {"codes": ["decision"], ...}` instead of testing `lab["intent"] == "answer"`.
- [ ] **Step 4: Run all pure tests** — `python3 -m pytest -q` → all pass (18 + 3).

### Task 2: Stored values, fields and the pre-migration

**Files:** Create `odoo/flight_deck/migrations/19.0.1.6.0/pre-migrate.py`; modify `odoo/flight_deck/models/task.py` (`INTENTS`, `parent_id` help, the count fields), `__manifest__.py` (version). Create `odoo/migration/snapshot_task_costs.py`. Test: `odoo/tests/check_type_switch.py` (odoo shell).

- [ ] **Step 0: Snapshot task costs before the upgrade** — `snapshot_task_costs.py` prints `SNAPSHOT {json}` of `{task id: [cost, turn_count]}` for `is_task` turns; saved to the evidence folder as `task-costs-before.json`.
- [ ] **Step 1: Failing check** — `check_type_switch.py`:
```python
T = env["flightdeck.turn"]
env.cr.execute("select count(*) from flightdeck_turn where intent in ('correction','answer','question')")
assert env.cr.fetchone()[0] == 0, "old Type values remain"
assert dict(T._fields["intent"].selection) == {"task": "Task", "follow_up": "Follow-up",
    "ask": "Question about the work", "side": "Side question", "no_work": "No work"}
env.cr.execute("select count(*) from flightdeck_turn where label_detail->>'old_intent' in ('correction','answer','question')")
assert env.cr.fetchone()[0] >= 500, "old values not kept"
env.cr.execute("""select count(*) from flightdeck_turn where label_source = 'human'
                  and label_detail->>'old_intent' = 'question' and intent <> 'side'""")
assert env.cr.fetchone()[0] == 0, "a person's side question moved"
for name in ("wrong_results", "revisions", "decisions", "pulls", "redirects", "rules_taught"):
    assert name in T._fields, name
assert "corrections" not in T._fields and "answers" not in T._fields
print("CHECKS PASSED")
env.cr.rollback()
```
Run before the change → `AssertionError: old Type values remain`.
- [ ] **Step 2: Pre-migration**:
```python
"""Move Type to the values of step 3 of the turn labels spec, before the Selection changes.

`correction` and `answer` become `follow_up`; a person's `question` (shown to them as
"Side question") becomes `side`; an engine's `question` becomes `side` for now and is
asked again after the upgrade (odoo/migration/retype_questions.py). The old value is
kept in label_detail.old_intent. A person's correction keeps its source, and its
Feedback goes to review.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE flightdeck_turn
           SET label_detail = coalesce(label_detail, '{}'::jsonb)
                              || jsonb_build_object('old_intent', intent,
                                                    'retype', intent = 'question' AND label_source IS DISTINCT FROM 'human'),
               intent = CASE WHEN intent IN ('correction', 'answer') THEN 'follow_up' ELSE 'side' END
         WHERE intent IN ('correction', 'answer', 'question')
    """)
    cr.execute("""
        UPDATE flightdeck_turn SET feedback_state = 'review'
         WHERE label_source = 'human' AND label_detail->>'old_intent' = 'correction'
           AND coalesce(feedback_source, '') NOT IN ('human', 'reference')
    """)
```
- [ ] **Step 3: Fields** in `task.py`: `INTENTS` as in Global Constraints; `parent_id` help "The task a follow-up or a question about the work continues."; replace `corrections` and `answers` with
```python
    wrong_results = fields.Integer("Agent wrong", readonly=True, aggregator="sum",
                                   help="Feedback labels of the Agent wrong group on this task's results.")
    revisions = fields.Integer(readonly=True, aggregator="sum",
                               help="Content edits, look and feel changes and added scope asked of this task.")
    decisions = fields.Integer(readonly=True, aggregator="sum",
                               help="Replies to the agent's questions and proposals in this task.")
    pulls = fields.Integer("Pulls", readonly=True, aggregator="sum",
                           help="Explanations and status the person asked for, and steps they did themselves.")
    redirects = fields.Integer(readonly=True, aggregator="sum")
    rules_taught = fields.Integer("Rules taught", readonly=True, aggregator="sum")
```
and `adjustments` help: "Agent wrong labels and redirects in this task: the times a person had to fix or redirect the agent. Averaged when grouped." Version `19.0.1.6.0`.
- [ ] **Step 4: Upgrade** (the rollup SQL still names the removed fields: Task 3 changes it in the same upgrade; do Tasks 2 and 3 before upgrading), run the check → `CHECKS PASSED`.

### Task 3: The rollup counts Feedback

**Files:** Modify `task.py` `_rollup` (the task-sum statement). Test: `check_type_switch.py` (append).

- [ ] **Step 1: Failing check** (append before the print):
```python
# A task's counts come from the feedback on its results.
env.cr.execute("""select t.id, t.wrong_results, t.revisions, t.decisions, t.pulls, t.redirects,
                         t.adjustments from flightdeck_turn t where t.is_task order by t.cost desc limit 50""")
rows = env.cr.fetchall()
assert any(r[1] or r[2] or r[3] for r in rows), "no task carries a feedback count"
env.cr.execute("""
  select count(*) from flightdeck_turn t
   where t.is_task and t.adjustments <> coalesce(t.wrong_results, 0) + coalesce(t.redirects, 0)""")
assert env.cr.fetchone()[0] == 0, "adjustments is not wrong_results + redirects"
# Attribution: a label counts on the task of the turn it judges.
env.cr.execute("""
  select t.id, p.task_id, t.task_id from flightdeck_turn t join flightdeck_turn p on p.id = t.previous_turn_id
   join flightdeck_turn_feedback_rel r on r.turn_id = t.id
   join flightdeck_feedback_kind k on k.id = r.kind_id and k.family = 'wrong'
   where p.task_id is not null and t.task_id is not null and p.task_id <> t.task_id limit 1""")
row = env.cr.fetchone()
if row:
    judged = T.browse(row[1])
    env.cr.execute("""select count(*) from flightdeck_turn_feedback_rel r join flightdeck_turn t on t.id = r.turn_id
      join flightdeck_turn p on p.id = t.previous_turn_id join flightdeck_feedback_kind k on k.id = r.kind_id
      where k.family = 'wrong' and p.task_id = %s""", [judged.id])
    from_turns = env.cr.fetchone()[0]
    env.cr.execute("""select count(*) from flightdeck_steer_feedback_rel r join flightdeck_turn_steer s on s.id = r.steer_id
      join flightdeck_turn t on t.id = s.turn_id join flightdeck_feedback_kind k on k.id = r.kind_id
      where k.family = 'wrong' and t.task_id = %s""", [judged.id])
    assert judged.wrong_results == from_turns + env.cr.fetchone()[0], "a label was counted on the wrong task"
```
- [ ] **Step 2: The rollup** — at the start of `_rollup`, `self._link_previous_turns()`. Replace the `s`/`v`/UPDATE statement's `co`, `an` parts: drop `count(*) FILTER (WHERE intent = 'correction')` and `'answer'`; add a CTE `f` with the labels on each task's results and `g` summing them:
```sql
f AS (
    SELECT p.task_id, k.family, k.code
      FROM flightdeck_turn_feedback_rel r
      JOIN flightdeck_turn t ON t.id = r.turn_id
      JOIN flightdeck_turn p ON p.id = t.previous_turn_id
      JOIN flightdeck_feedback_kind k ON k.id = r.kind_id
     WHERE p.task_id IS NOT NULL
    UNION ALL
    SELECT t.task_id, k.family, k.code
      FROM flightdeck_steer_feedback_rel r
      JOIN flightdeck_turn_steer s ON s.id = r.steer_id
      JOIN flightdeck_turn t ON t.id = s.turn_id
      JOIN flightdeck_feedback_kind k ON k.id = r.kind_id
     WHERE t.task_id IS NOT NULL),
g AS (
    SELECT task_id,
           count(*) FILTER (WHERE family = 'wrong') AS wr,
           count(*) FILTER (WHERE family = 'revise') AS rv,
           count(*) FILTER (WHERE code = 'decision') AS de,
           count(*) FILTER (WHERE code IN ('explain', 'status', 'handback')) AS pu,
           count(*) FILTER (WHERE code = 'redirect') AS rd,
           count(*) FILTER (WHERE code = 'teach') AS te
      FROM f GROUP BY task_id)
```
joined into `v` with `COALESCE(g.wr, 0)` etc.; `SET ... wrong_results = v.wr, revisions = v.rv, decisions = v.de, pulls = v.pu, redirects = v.rd, rules_taught = v.te, adjustments = v.wr + v.rd` and the matching `IS DISTINCT FROM` terms. Keep `follow_ups` and `steers`.
- [ ] **Step 3:** upgrade (with Task 2), run the whole check → `CHECKS PASSED`.

### Task 4: Ask the old engine questions their Type again

**Files:** Create `odoo/migration/retype_questions.py`. Test: in the script's own asserts.

- [ ] **Step 1:** the script:
```python
"""Ask Jev and Luna the Type of the old `question` turns an engine had labelled.

Run once after the 19.0.1.6.0 upgrade. They were moved to `side` for the upgrade;
the label pass now picks `ask` or `side`, and `ask` turns get a parent, like any
follow-up. Prints the split and checks that no ask turn is left without a parent.
"""
Turn = env["flightdeck.turn"]
todo = Turn.search([("label_source", "!=", "human")]).filtered(
    lambda t: (t.label_detail or {}).get("retype"))
print("to ask again:", len(todo))
if todo:
    Turn._label_turns(todo, force=True, commit=True)
    for t in todo.with_context(fd_label_engine=True):
        t.label_detail = dict(t.label_detail or {}, retype=False)
    env.cr.commit()
split = {}
for t in todo:
    split[t.intent] = split.get(t.intent, 0) + 1
print("now:", split)
orphans = Turn.search_count([("intent", "=", "ask"), ("parent_id", "=", False)])
print("ask without a parent:", orphans)
```
Expected: `now:` shows `ask` and `side` (and perhaps others); `ask without a parent` 0 for turns that have an earlier turn in their session.
- [ ] **Step 2:** write `task-costs-after.json` with the snapshot script and a one-line summary: tasks whose cost changed, total moved.

### Task 5: One review queue for Type and Feedback

**Files:** Modify `task.py` `fd_task_panel`; `turn_feedback.py` (`fd_set_feedback`); `static/src/components/task_panel/task_panel.js`, `.xml`. Test: `check_type_switch.py` (queue part) and a Playwright screenshot.

- [ ] **Step 1: Failing check** (append):
```python
t = T.search([("feedback_state", "=", "review")], limit=1) or T.search([], limit=1)
t.with_context(fd_label_engine=True).write({"feedback_state": "review", "label_source": "human", "needs_review": False})
panel = T.fd_task_panel([])
card = next((c for c in panel["review"] if c["id"] == t.id), None)
assert card and card["feedback_review"], "a turn with Feedback to review is missing from the queue"
assert {k["code"] for k in panel["feedback_kinds"]} >= {"defect", "scope"}
T.fd_set_feedback(t.id, ["defect"])
assert t.feedback_source == "human" and t.feedback_state == "done" and t.label_source == "human"
```
- [ ] **Step 2: Server side.** Queue domain `["|", "&", ("needs_review", "=", True), ("label_source", "!=", "human"), ("feedback_state", "=", "review")]` for the list and the count. Each card adds `"feedback": t.feedback_ids.mapped("code")`, `"feedback_review": t.feedback_state == "review"`, `"type_review": t.needs_review and t.label_source != "human"`, `"feedback_reason": (t.feedback_detail or {}).get("reason") or ""`. The payload adds `"feedback_kinds": [{"code", "name", "family"} ...]` of active kinds. `fd_set_feedback(turn_id, codes)` in `turn_feedback.py`: writes `feedback_ids` (main = first code) through the person's path, returns True.
- [ ] **Step 3: The component.** `LABELS`: task, follow-up, question about the work (`ask`), side question (`side`), no work. On a card with `feedback_review`, a row of chips (one per kind, pressed = in `card.feedback`), toggled locally, and a "Save feedback" button calling `fd_set_feedback`. Type buttons show only when `type_review`; otherwise the card says "Type: <label>, set".
- [ ] **Step 4:** upgrade, check, screenshot `review-queue.png`.

### Task 6: Model stats and the task screens read the new counts

**Files:** `models/model_stats.py` (`METRICS`), `static/src/components/model_stats/model_stats_action.js` (`MEASURES`, `TILES`), `.xml` (table columns and the help text), `views/task_views.xml` (task list, turn list decoration, form groups, search filters, adjustments pivot and help). Test: `check_type_switch.py` (`fd_model_stats` returns every metric) and a Playwright screenshot.

- [ ] `METRICS = ("adjustments", "wrong_results", "revisions", "decisions", "pulls", "follow_ups", "steers")`. `MEASURES`: Adjustments, Agent wrong, Revisions, Decisions, Pulls, Follow-ups, Steer messages. `TILES = ["adjustments", "wrong_results", "revisions", "pulls"]`. Table columns follow `MEASURES` after the ratio. Help text: "An adjustment is a Feedback label of the Agent wrong group, or a redirect, on a task's results: a time a person had to fix or redirect the agent. Revisions and decisions are the normal work of steering and are shown apart."
- [ ] Views: task list `corrections`/`answers` → `wrong_results`, `revisions`, `decisions` (optional hide), `pulls` (optional hide); turn list decoration `intent == 'correction'` → removed, `intent == 'question'` → `intent == 'side'`; form: `parent_id invisible="intent not in ('follow_up', 'ask')"`, task group counts as the list; search: filters `intent_correction`, `intent_answer` removed, `intent_question` → `intent_ask` "Questions about the work" and `intent_side` "Side questions"; adjustments pivot measure `corrections` → `wrong_results`; action help as the page's.
- [ ] Check: `set(T.fd_model_stats()["models"][0]["metrics"]) == set(METRICS)` when a model exists.

### Task 7: Turn analysis reads Feedback

**Files:** `models/turn_step.py` (`_analysis_digest`), `models/step_reader.py` (`digest`), `scripts/turn_review_probe.py` (`PICK_SQL`), `scripts/turn_route_probe.py` (`rule_route`). Test: pure test for `step_reader.digest`'s last line.

- [ ] Test (`odoo/tests/test_type_values.py`): `step_reader.digest(meta, [], "reply", {"intent": "follow_up", "feedback": ["defect"], "prompt": "x" * 2000})` ends with a line starting `Next person turn: follow_up · feedback defect — ` and holding 1,500 characters of the prompt.
- [ ] `digest`: `lines.append("Next person turn: %s · feedback %s — %s" % (intent, ", ".join(feedback) or "none", one_line(prompt, NEXT)))` with `NEXT = 1500`. `_analysis_digest`: `next_turn` gains `"feedback": after.feedback_ids.mapped("code")`.
- [ ] `PICK_SQL`: the `correction next` trigger becomes `wrong next`: the next turn has a label of family `wrong` (join `flightdeck_turn_feedback_rel`/`flightdeck_feedback_kind`). `turn_route_probe.rule_route`: `next_intent == "correction"` → the digest's next line names a label of the wrong group (`defect`, `convention`, `context`, `approach`, `check`).

### Task 8: The label check measures Type too, and the spec is updated

- [ ] `_label_check_report` adds "Type: engines match N%" comparing `intent` (engine-labelled turns) with `check_intent` on the label-check turns.
- [ ] Spec: "As built: step 3" with the migration counts, the retype split, the cost changes, and the rulings.

## Not doing

- Merging the Feedback question into the Type Jev call (spec "How a prompt is labelled", step 2). Ruling in the ledger: two calls stay, because the two pipelines are labelled at different moments (Type at submit, Feedback by its own job) and the other session is changing the Type flow; one extra Jev call per turn (about 0.4 s) is the cost.
