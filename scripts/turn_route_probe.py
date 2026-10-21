"""Probe: Jev picks which analyzer reviews each turn.

For each turn:
1. Odoo builds the turn's digest (`flightdeck.turn._analysis_digest`), which is
   bounded and has secrets masked.
2. A short routing state is cut from that digest: the header, the calls that
   failed, were blocked or repeated, the head of the reply and the next thing
   the person said. Only this state goes to Jev.
3. Jev answers one choice question: luna, sol or opus. A clear answer
   (top >= 0.8 and margin >= 0.3) is kept; otherwise the stronger of the top
   two is taken, so doubt costs money rather than a weak review.
4. A fixed rule on the same numbers is recorded beside Jev's pick, to see
   whether Jev adds anything a rule does not.

The reviews themselves run separately, with the rubric and schema of
turn_review_probe.py. Nothing is stored in Flightdoo.

Usage: TYPESAFE_ENV_FILE=~/.config/jev-browser/env \
       python3 scripts/turn_route_probe.py OUT_DIR TURN_ID...
"""
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("turn_review_probe", os.path.join(HERE, "turn_review_probe.py"))
_saved, sys.argv = sys.argv, sys.argv[:1]
review_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_probe)
sys.argv = _saved

# Same service, model and gate as models/task_classifier.py; copied rather than
# imported because that module lives inside the Odoo package.
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
JEV_ACCEPT_TOP1 = 0.8
JEV_ACCEPT_MARGIN = 0.3
JEV_TIMEOUT = 60

# Cheapest first; doubt moves a turn to the right.
STRENGTH = ["luna", "sol", "opus"]
ANALYZERS = {"luna": "gpt-6-luna", "sol": "gpt-5.6-sol", "opus": "claude-opus-5-5"}

ROUTE_QUESTION = {
    "type": "choice",
    "instructions": "Which analyzer should review this turn? Pick the cheapest one that "
                    "can judge what this turn needs judged.",
    "criteria": {
        "luna": "A routine turn: few or no failed, blocked or repeated calls, the reply "
                "reports the work done, and the next person turn moves on to more work. "
                "A fast summary is enough.",
        "sol": "A turn with failed commands, repeats or many calls, where each failure "
               "must be judged a mistake, an iteration or the environment, and the "
               "next person turn does not push back on the result.",
        "opus": "A turn whose approach must be judged: the next person turn corrects or "
                "rejects the result, a guard or the person blocked calls, or the steps "
                "wander from what the prompt asked.",
    },
}

NUMBERS = re.compile(
    r"Numbers: (?P<calls>\d+) calls \((?P<sub>\d+) by subagents\) · (?P<avoidable>\d+) avoidable "
    r"errors · (?P<failed>\d+) failed commands · (?P<guard>\d+) blocked by a guard · "
    r"(?P<environment>\d+) environment · (?P<repeats>\d+) exact repeats \(most of one call: "
    r"(?P<max_repeat>\d+)\)")
NEXT = re.compile(r"^Next person turn: (\S+) · feedback ([^—]*?) — ", re.M)
# The Feedback labels of the Agent wrong group: the next prompt says the result was wrong.
WRONG = {"defect", "convention", "context", "approach", "check"}
UNUSUAL_LINES = 15


def jev_key():
    """TYPESAFE_API_KEY, or the same variable read from TYPESAFE_ENV_FILE."""
    key = os.environ.get("TYPESAFE_API_KEY")
    path = os.environ.get("TYPESAFE_ENV_FILE")
    if key or not path:
        return key
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            for line in fh:
                name, _, value = line.strip().removeprefix("export ").partition("=")
                if name == "TYPESAFE_API_KEY":
                    return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def spread(probs):
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, p1 = ranked[0]
    p2 = ranked[1][1] if len(ranked) > 1 else 0.0
    return top, p1, p1 - p2, ranked


def jev_call(state, key):
    body = json.dumps({"state": state, "model": JEV_MODEL,
                       "questions": {"analyzer": ROUTE_QUESTION}}).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
        "Authorization": "Bearer %s" % key, "Content-Type": "application/json"})
    started = time.monotonic()
    raw, error = None, None
    try:
        with urllib.request.urlopen(req, timeout=JEV_TIMEOUT) as r:
            raw = json.loads(r.read())
    except urllib.error.HTTPError as e:
        error = "HTTP %s: %s" % (e.code, e.read()[:300].decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        error = str(e)[:300]
    return raw, error, int((time.monotonic() - started) * 1000)


def routing_state(digest):
    """The digest's header, its unusual steps, the reply head and the next turn."""
    lines = digest.splitlines()
    header = lines[:4]
    steps = [l for l in lines if re.match(r"^\d+ \| ", l)]
    unusual = [l for l in steps if l.split(" | ")[5] != "ok"]
    # An input can hold " | " itself (a shell pipe), so a line is cut, not split.
    shown = [line[:360] for line in unusual[:UNUSUAL_LINES]]
    if len(unusual) > UNUSUAL_LINES:
        shown.append("… %s more unusual steps …" % (len(unusual) - UNUSUAL_LINES))
    reply = next((l for l in lines if l.startswith("Final reply: ")), "Final reply: ")[:300]
    nxt = next((l for l in lines if l.startswith("Next person turn: ")),
               "Next person turn: none yet")[:300]
    header[1] = header[1][:500]
    return "\n".join(
        ["One turn of a coding agent (Claude Code), about to be reviewed by an analyzer."]
        + header + ["", "Unusual steps (n | t+min | agent | tool | seconds | outcome | input | error):"]
        + (shown or ["none"]) + ["", reply, nxt])


def rule_route(digest):
    """A fixed rule on the digest's numbers, for comparison with Jev."""
    m = NUMBERS.search(digest)
    n = {k: int(v) for k, v in m.groupdict().items()} if m else {}
    nxt = NEXT.search(digest)
    next_intent = nxt.group(1) if nxt else None
    wrong_next = bool(nxt and WRONG & set(nxt.group(2).split(", ")))
    if n.get("guard") or wrong_next:
        return "opus", n, next_intent
    if n.get("failed") or n.get("avoidable") or n.get("environment") or n.get("max_repeat", 0) >= 3 \
            or n.get("calls", 0) >= 80:
        return "sol", n, next_intent
    return "luna", n, next_intent


def main():
    out, ids = sys.argv[1], [int(a) for a in sys.argv[2:]]
    os.makedirs(out, exist_ok=True)
    key = jev_key()
    if not key:
        sys.exit("no TypeSafe key: set TYPESAFE_API_KEY or TYPESAFE_ENV_FILE")
    texts = review_probe.digests(ids)
    for tid in ids:
        digest = texts.get(tid)
        if not digest:
            print(tid, "no digest")
            continue
        with open(os.path.join(out, "%s.digest.txt" % tid), "w") as fh:
            fh.write(digest)
        state = routing_state(digest)
        raw, error, latency = jev_call(state, key)
        probs = (((raw or {}).get("answers") or {}).get("analyzer") or {}).get("probabilities") or {}
        record = {"turn": tid, "jev_model": JEV_MODEL, "question": ROUTE_QUESTION, "state": state,
                  "state_chars": len(state), "digest_chars": len(digest), "latency_ms": latency,
                  "raw": raw, "error": error}
        rule, numbers, next_intent = rule_route(digest)
        record.update(rule=rule, numbers=numbers, next_intent=next_intent)
        if probs:
            top, p1, margin, ranked = spread(probs)
            clear = p1 >= JEV_ACCEPT_TOP1 and margin >= JEV_ACCEPT_MARGIN
            pick = top if clear else max(ranked[0][0], ranked[1][0], key=STRENGTH.index)
            record.update(top=top, p1=round(p1, 3), margin=round(margin, 3), clear=clear,
                          pick=pick, escalated=pick != top, analyzer=ANALYZERS[pick])
        with open(os.path.join(out, "%s.route.json" % tid), "w") as fh:
            json.dump(record, fh, indent=1, ensure_ascii=False)
        print(tid, "jev", record.get("top"), record.get("p1"), record.get("margin"),
              "clear" if record.get("clear") else "gate", "->", record.get("pick"),
              "| rule", rule, "| next", next_intent, "|", len(state), "chars", latency, "ms",
              error or "")


if __name__ == "__main__":
    main()
