# Turn label definitions (working copy, 2026-09-29)

The text the engines read, copied from the code and the database so it can be edited in one
place. Edit the English definitions directly; the notes under each section are what the code
does around them. After editing, the changes go back to `task_classifier.py` (Type),
`flightdeck.feedback.kind` records (Feedback) and `feedback_classifier.py` (rules).

Status: the Type texts below are the ones in the code since 2026-09-29 evening. The running
server still has the earlier ones until its next restart.

## Type: where the prompt sits (one value per turn and per steer message)

Proposed 2026-09-29 from the person's rulings (1–8 below the tables). Not in the code yet.

Question to Jev: "What does the NEW PROMPT do, given the session's topic and the earlier work
listed in the state?"

| Value | Label | Rollup kind | Definition |
|---|---|---|---|
| `task` | Task | task | Starts a piece of work no earlier turn asked for, that could be its own ticket: a feature, fix, document or piece of research. A question is a task only when it opens a subject apart from the task before it, even inside the session's topic (for example, "which scopes remain" about the whole migration, asked after work on another task). |
| `follow_up` | Follow-up | subtask | Directs the agent on the task before it, the way a message typed while it works would: go on, do the next step, change or fix what it delivered, add to it, or approve or pick one of its proposals. It asks for work, not only for an answer. |
| `ask` | Question about the work | subtask | Asks a question whose answer is for the person to read, about the task before it, the session's topic or what the agent delivered, and asks for no change. In research and design work too. |
| `mixed` | Change and question | subtask | **New.** Asks for a change or more work and, in the same prompt, a question whose answer is for the person to read: "update the ADR, and explain why not X". |
| `side` | Side question | no_work | A question with no link to the session's topic or to the task before it: the date, a general fact, another project. |
| `no_work` | No work | no_work | Set by rule only (below). |

Around the definitions:
- `follow_up`, `ask` and `mixed` continue a task: they get a parent and their cost counts
  under it. `side` and `no_work` count under no task.
- The engines are given the session's title as its topic, beside the turns before it.
- Steer messages get a Type too, from `follow_up`, `ask`, `mixed` and `side`. A steer has no
  cost of its own (it is part of its turn), so its Type is for analysis only.
- Only a `task` gets a title and a work tag.
- One source: Jev's criteria and Luna's instructions are both written from this table.

Rules, tried before the engines:
- cwd under `/tmp` (not Claude's scratchpad) → `no_work`, "automated".
- The turn ended with no billed call → `no_work`, "empty".
- First turn of a session → no rule; the engines decide.
- The prompt only says to go on (`tiếp`, `continue`, `go on`…) → `follow_up`, "go on".
- The agent's last message ended on "?", and the reply is one line of at most 60 characters,
  within 30 minutes → `follow_up`, "reply to a question" (and Feedback `decision`).

Type and Feedback made to agree (engine labels only; a person's Type is never changed, a
reference Feedback never feeds it):
- `follow_up` with Feedback only `explain` / `status` → `ask`.
- `follow_up` with `explain` / `status` and a label asking for a change (Agent wrong,
  Revision, `redirect`) → `mixed`.

Rulings behind this (2026-09-29): 1 an answer for the reader with no change is `ask`;
2 a new value for change-and-question, and `follow_up` narrowed toward steer-like
directions; 3 a question about a proposal is not a reply to it; 4 `explain` covers the same
questions as `ask`; 5 task or ask by the session's topic and the task before it; 6 `side`
only with no link to the topic or the task before it; 7 steers get a Type; 8 one source.

## Feedback: what the prompt says about the result just before it (any number, main first)

Question to Jev: "What does the NEW PROMPT say about the agent's result just before it?"

| Group | Code | Label | Definition |
|---|---|---|---|
| Agent wrong | `defect` | Defect | It says the delivered result does not work or behaves wrongly. |
| Agent wrong | `convention` | Convention miss | It fixes something a written rule, a convention or an earlier instruction of the person already covered: naming, wording, style, process. |
| Agent wrong | `context` | Missing context | It states a fact the agent did not know or got wrong (how the business works, who does what, how a system behaves), and the change follows from that fact. |
| Agent wrong | `approach` | Approach change | It rejects the way the agent solved it and names another way, without giving a new fact: revert, or do it differently. |
| Agent wrong | `check` | Check request | It asks the agent to test or verify work it reported as done. |
| Revision | `edit` | Content edit | It changes the wording, length or structure of a text the agent wrote, whose content was right. |
| Revision | `look` | Look and feel | It changes how a screen or picture looks: layout, size, colour, order. |
| Revision | `scope` | Added scope | It adds a requirement or a next step to the work the agent just delivered, without saying the delivered part is wrong. A new piece of work, even in the same session or project, is not added scope. |
| Control | `decision` | Decision | It answers a question the agent asked, approves what it proposed, or picks one of its options. |
| Control | `explain` | Explanation request | **Proposed:** It asks a question whose answer is for the person to read, about what the agent delivered, the task before it or the session's topic: why, how, what. It asks for no change. (Was: only about the result the agent just delivered.) |
| Control | `status` | Status request | It asks what state the work is in or what has been done. |
| Control | `redirect` | Redirect | It stops, pauses or switches the agent's work, or limits what it may do. |
| Control | `handback` | Person's action | It reports something the person did or saw outside the agent: restarted, deployed, it is live. |
| Control | `teach` | Standing rule | It asks the agent to remember a rule, or to add it to memory, a skill or the instructions. |
| — | none | (no label) | It moves on without a word on the result: to a new piece of work, or a question about something else. |

What the counts use:
- Adjustments = Agent wrong + `redirect`. Revisions = the Revision group. Pulls = `explain`,
  `status`, `handback`. Decisions = `decision`. Rules taught = `teach`.
- A prompt's labels count on the task of the turn before it; a steer's on its own turn's task.

Luna's extra instruction: "Most prompts carry one label or none: a label is for what the
prompt says about the result just before it, so a prompt that starts other work, or asks
about something else, carries none."
