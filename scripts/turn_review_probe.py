"""Probe of layer 3: a Codex agent reviews a few turns from their digests.

1. Picks turns in Flightdoo by the review triggers of the turn analysis
   standard (docs/superpowers/specs/2026-09-28-turn-analysis-standard-design.md).
2. Asks Odoo for each turn's digest (`flightdeck.turn._analysis_digest`), which
   is bounded and has secrets masked.
3. Runs `codex exec` once per turn with the v1 rubric and a strict output
   schema, read-only and ephemeral.

Writes digests and reviews to OUT, one pair per turn. Nothing is stored in
Flightdoo.

Usage: python3 scripts/turn_review_probe.py [model] [effort]
"""
import json
import os
import subprocess
import sys
import tempfile
import time

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-6-astra"
EFFORT = sys.argv[2] if len(sys.argv) > 2 else "medium"
RUBRIC_VERSION = 1
OUT = os.environ.get("TURN_REVIEW_OUT") or os.path.join(tempfile.gettempdir(), "turn_review")
os.makedirs(OUT, exist_ok=True)

RUBRIC = """You review one turn of a coding agent (Claude Code) for the person who tunes
its tools and working rules. The goal is an agent that decides faster and calls
tools more correctly, and a person who can see and control what it does quickly.

You get a digest of the turn: the prompt, its numbers, one line per tool call
(n | minutes since start | agent | tool | seconds the tool ran | outcome | input | error),
the final reply, and the next thing the person said. Judge only what the digest
shows and cite step numbers. Do not invent steps. When the digest leaves steps
out, say so rather than guess.

Outcome families, already sorted by rules:
- avoidable: the agent's own mistake (bad tool input, edit text not in the file,
  write before read, a guessed path that does not exist, a file too large).
- failed: a command that ran and failed. You decide which it was: a mistake, a
  script being iterated on, an intended red test, or the environment.
- guard: a hook, the auto-mode classifier or a person said no.
- environment: a timeout, a service or browser that was not there.
- expected: "nothing matched" answers.
A step's seconds are the tool running; the rest of the time between steps is the
model thinking or writing, or waiting.

Suggestion targets:
- claude_md: a line in the workspace or project instructions.
- skill: a packaged procedure the agent loads for a kind of task.
- hook: a PreToolUse guard rule, or the message it gives when it blocks.
- tool: a script, CLI, MCP server or its defaults.
- prompt: how the person asked.
- none: nothing worth changing.

Give at most three suggestions, the ones that would save the most time or the
most errors in future turns like this one. Name the concrete change.

Work from the digest alone: do not run commands or open files. Write every text
field in English, in plain sentences."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["approach", "time", "failed_commands", "avoidable", "wasted", "tool_use",
                 "suggestions", "for_the_person", "scores", "confidence"],
    "properties": {
        "approach": {
            "type": "object", "additionalProperties": False, "required": ["kind", "summary"],
            "properties": {
                "kind": {"type": "string", "enum": ["direct", "exploratory", "detour", "stalled"]},
                "summary": {"type": "string"},
            },
        },
        "time": {
            "type": "object", "additionalProperties": False, "required": ["went_to", "note"],
            "properties": {
                "went_to": {"type": "string", "enum": ["tools", "model", "waiting", "mixed"]},
                "note": {"type": "string"},
            },
        },
        "failed_commands": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["step", "verdict", "why"],
            "properties": {
                "step": {"type": "integer"},
                "verdict": {"type": "string", "enum": ["mistake", "iteration", "intended", "environment"]},
                "why": {"type": "string"},
            }}},
        "avoidable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["step", "cause"],
            "properties": {"step": {"type": "integer"}, "cause": {"type": "string"}}}},
        "wasted": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["steps", "why"],
            "properties": {"steps": {"type": "array", "items": {"type": "integer"}},
                           "why": {"type": "string"}}}},
        "tool_use": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["steps", "issue", "better"],
            "properties": {"steps": {"type": "array", "items": {"type": "integer"}},
                           "issue": {"type": "string"}, "better": {"type": "string"}}}},
        "suggestions": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["target", "change", "steps", "expected_effect"],
            "properties": {
                "target": {"type": "string", "enum": ["claude_md", "skill", "hook", "tool", "prompt", "none"]},
                "change": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "integer"}},
                "expected_effect": {"type": "string"},
            }}},
        "for_the_person": {"type": "string"},
        "scores": {
            "type": "object", "additionalProperties": False,
            "required": ["efficiency", "tool_fit", "error_handling"],
            "properties": {k: {"type": "integer", "minimum": 1, "maximum": 5}
                           for k in ("efficiency", "tool_fit", "error_handling")},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

# One or two turns per trigger, from the last three weeks, main sessions only.
PICK_SQL = """
with t as (
  select t.id, t.started_at, t.intent, t.model, t.tool_calls, t.avoidable_errors, t.failed_calls,
         t.guard_hits, t.max_repeat,
         lead(t.id) over (partition by t.session_ref_id order by t.started_at) as next_id
    from flightdeck_turn t
   where t.steps_state = 'done' and t.tool_calls >= 5
     and t.started_at > now() - interval '21 days'
     and t.intent in ('task', 'follow_up', 'ask')
), w as (
  select distinct r.turn_id from flightdeck_turn_feedback_rel r
    join flightdeck_feedback_kind k on k.id = r.kind_id and k.family = 'wrong'
), p as (select percentile_disc(0.9) within group (order by tool_calls) p90 from t)
(select 'avoidable>=3', id from t where avoidable_errors >= 3 order by started_at desc limit 2)
union all (select 'repeat>=3', id from t where max_repeat >= 3 order by started_at desc limit 2)
union all (select 'wrong next', id from t join w on w.turn_id = t.next_id order by started_at desc limit 2)
union all (select 'calls>p90', id from t, p where tool_calls > p.p90 order by started_at desc limit 1)
union all (select 'guard>=2', id from t where guard_hits >= 2 order by started_at desc limit 1)
union all (select 'random', id from (select id from t order by md5(id::text) limit 1) r)
"""


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", "pgcore-17", "sh", "-c",
         'psql -U "$POSTGRES_USER" -d flightdeck_odoo -At -F "|" -c "%s"' % sql.replace('"', '\\"')],
        capture_output=True, text=True, check=True).stdout
    return [line.split("|") for line in out.splitlines() if line]


def digests(ids):
    script = ("import json\nfor t in env['flightdeck.turn'].browse(%r).exists():\n"
              "    print('DIGEST ' + json.dumps({'id': t.id, 'digest': t._analysis_digest()}))\n" % ids)
    out = subprocess.run(
        ["docker", "exec", "-i", "flightdeck-odoo19", "/entrypoint.sh", "odoo", "shell", "-c",
         "/etc/odoo/odoo.conf", "-d", "flightdeck_odoo", "--no-http", "--log-level=error"],
        input=script, capture_output=True, text=True, check=True).stdout
    return {d["id"]: d["digest"] for d in (json.loads(line[7:]) for line in out.splitlines()
                                            if line.startswith("DIGEST "))}


def review(digest):
    with tempfile.TemporaryDirectory(prefix="fd-review-") as tmp:
        schema, out = os.path.join(tmp, "schema.json"), os.path.join(tmp, "out.json")
        with open(schema, "w") as fh:
            json.dump(SCHEMA, fh)
        started = time.monotonic()
        done = subprocess.run(
            ["codex", "exec", "-m", MODEL, "-c", 'model_reasoning_effort="%s"' % EFFORT,
             "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
             "--output-schema", schema, "-o", out, "-"],
            input=RUBRIC + "\n\n--- digest ---\n" + digest, text=True, cwd=tmp,
            capture_output=True, timeout=600)
        seconds = time.monotonic() - started
        if not os.path.exists(out):
            return None, seconds, (done.stderr or done.stdout)[-600:]
        with open(out) as fh:
            return json.load(fh), seconds, None


def main():
    picked = {}
    for trigger, tid in psql(" ".join(PICK_SQL.split())):
        picked.setdefault(int(tid), []).append(trigger)
    texts = digests(list(picked))
    print(f"model {MODEL}, effort {EFFORT}, rubric v{RUBRIC_VERSION}: {len(texts)} turns")
    for tid, triggers in picked.items():
        text = texts.get(tid)
        if not text:
            print(tid, "no digest")
            continue
        with open(os.path.join(OUT, f"{tid}.digest.txt"), "w") as fh:
            fh.write(text)
        result, seconds, error = review(text)
        record = {"turn": tid, "triggers": triggers, "model": MODEL, "effort": EFFORT,
                  "rubric_version": RUBRIC_VERSION, "seconds": round(seconds, 1),
                  "digest_chars": len(text), "review": result, "error": error}
        with open(os.path.join(OUT, f"{tid}.review.json"), "w") as fh:
            json.dump(record, fh, indent=1, ensure_ascii=False)
        head = (result or {}).get("approach", {})
        print(tid, ",".join(triggers), f"{seconds:.0f}s", f"{len(text)} chars",
              head.get("kind"), "|", (head.get("summary") or error or "")[:110])


if __name__ == "__main__":
    main()
