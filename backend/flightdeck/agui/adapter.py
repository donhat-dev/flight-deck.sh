"""Adapt Claude Code data into an AG-UI event stream.

Two producers, both plain generators of AG-UI event dicts (the route frames
them as SSE and paces them):

- `stream_replay(...)` turns a real session transcript (transcript.build_
  transcript output) into lifecycle / text / tool-call / state events. This is
  the "adapter between Claude Code and the UI" the Radar note calls for — the
  CLI's session log rendered through the AG-UI vocabulary.
- `stream_demo(...)` is a short scripted run that exercises the part a plain
  transcript can't show: a human-in-the-loop **interrupt**. It pauses before a
  destructive tool and waits for an approve / reject / edit decision.
"""
import json
import uuid

from flightdeck.agui import events as E

# Tools whose input names a file — surfaced into the shared run state so the UI
# can show a live "files touched" list built from STATE_DELTA patches.
_FILE_TOOLS = {"Write", "Edit", "NotebookEdit", "Read"}


def _chunks(text, size=28):
    """Split text into word-ish chunks so TEXT_MESSAGE_CONTENT streams like
    tokens rather than arriving whole."""
    words = text.split(" ")
    buf = ""
    for w in words:
        buf = w if not buf else f"{buf} {w}"
        if len(buf) >= size:
            yield buf
            buf = ""
    if buf:
        yield buf


def _short_tool_summary(name, inp):
    """A one-line human summary of a tool call for the activity feed."""
    if not isinstance(inp, dict):
        return name
    for key in ("command", "file_path", "path", "query", "pattern", "url", "skill"):
        if key in inp and isinstance(inp[key], str):
            v = inp[key]
            return f"{name}: {v[:80]}"
    return name


def stream_replay(tx, thread_id, run_id):
    """Yield AG-UI events for a real session transcript."""
    yield E.run_started(thread_id, run_id)

    # Shared run state, kept in sync with the UI via STATE_SNAPSHOT + patches.
    state = {
        "source": "replay",
        "session": tx.get("title") or "(untitled session)",
        "phase": "running",
        "turnsSeen": 0,
        "messages": 0,
        "toolCalls": 0,
        "filesTouched": [],
    }
    yield E.state_snapshot(state)

    turns = tx.get("turns") or []
    for i, turn in enumerate(turns):
        if turn.get("is_meta"):
            continue
        role = turn.get("role", "assistant")
        step = f"turn {i + 1} · {role}"
        yield E.step_started(step)
        state["turnsSeen"] += 1
        yield E.state_delta([{"op": "replace", "path": "/turnsSeen",
                              "value": state["turnsSeen"]}])

        for block in turn.get("blocks") or []:
            bt = block.get("type")
            if bt == "thinking":
                yield E.custom("reasoning", {"text": block.get("text", "")[:600]})
            elif bt == "text":
                txt = block.get("text", "")
                if not txt.strip():
                    continue
                mid = uuid.uuid4().hex[:12]
                yield E.text_start(mid, role=role)
                for c in _chunks(txt):
                    yield E.text_content(mid, c + " ")
                yield E.text_end(mid)
                state["messages"] += 1
                yield E.state_delta([{"op": "replace", "path": "/messages",
                                      "value": state["messages"]}])
            elif bt == "tool_use":
                tcid = block.get("id") or uuid.uuid4().hex[:12]
                name = block.get("name", "tool")
                inp = block.get("input") or {}
                yield E.tool_start(tcid, name)
                # Stream the args as JSON in a couple of chunks.
                args_json = json.dumps(inp, ensure_ascii=False)
                for c in _chunks(args_json, 64):
                    yield E.tool_args(tcid, c)
                yield E.tool_end(tcid)
                yield E.custom("activity", {"text": _short_tool_summary(name, inp)})
                state["toolCalls"] += 1
                patches = [{"op": "replace", "path": "/toolCalls",
                            "value": state["toolCalls"]}]
                if name in _FILE_TOOLS and isinstance(inp.get("file_path"), str):
                    fp = inp["file_path"]
                    if fp not in state["filesTouched"]:
                        state["filesTouched"].append(fp)
                        patches.append({"op": "add", "path": "/filesTouched/-",
                                        "value": fp})
                yield E.state_delta(patches)
            elif bt == "tool_result":
                yield E.tool_result(
                    block.get("tool_use_id") or "unknown",
                    block.get("content", ""),
                    is_error=bool(block.get("is_error")))

        yield E.step_finished(step)

    yield E.state_delta([{"op": "replace", "path": "/phase", "value": "done"}])
    yield E.run_finished(thread_id, run_id,
                         result={"reason": "complete",
                                 "turns": state["turnsSeen"]})


# --- scripted human-in-the-loop demo ----------------------------------------
# A tiny fixed run that ends in an interrupt: the agent proposes a destructive
# command and the UI must approve / reject / edit before it continues. The
# `interrupt` marker is a CUSTOM event so a stock AG-UI client still parses the
# stream; the frontend keys its approval card off it.
_DEMO_TOOL_ID = "demo-bash-1"
_DEMO_CMD = "rm -rf build/ dist/ && npm run clean"


def stream_demo_intro(thread_id, run_id):
    """Part 1 of the demo: run up to the approval gate, then stop. The route
    emits the interrupted RUN_FINISHED after this generator ends."""
    yield E.run_started(thread_id, run_id)
    state = {
        "source": "demo",
        "phase": "running",
        "step": "plan",
        "approved": None,
        "toolCalls": 0,
    }
    yield E.state_snapshot(state)

    yield E.step_started("plan")
    mid = uuid.uuid4().hex[:12]
    yield E.text_start(mid)
    for c in _chunks("I'll clean the build outputs before rebuilding. This "
                     "removes generated folders, so it needs your approval.", 30):
        yield E.text_content(mid, c + " ")
    yield E.text_end(mid)
    yield E.step_finished("plan")

    # Propose the destructive tool (START + ARGS) but DO NOT end/execute it —
    # execution waits behind the interrupt.
    yield E.step_started("await-approval")
    yield E.tool_start(_DEMO_TOOL_ID, "Bash")
    yield E.tool_args(_DEMO_TOOL_ID, json.dumps({"command": _DEMO_CMD}))
    yield E.state_delta([{"op": "replace", "path": "/step", "value": "await-approval"}])
    # Human-in-the-loop gate. The route turns this into an interrupted finish.
    yield E.custom("interrupt", {
        "toolCallId": _DEMO_TOOL_ID,
        "toolName": "Bash",
        "reason": "destructive command requires approval",
        "command": _DEMO_CMD,
        "options": ["approve", "reject", "edit"],
    })


def stream_demo_resume(thread_id, run_id, decision, edited_command=None):
    """Part 2 of the demo: continue after a decision. `decision` is one of
    approve / reject / edit (edit carries `edited_command`)."""
    yield E.run_started(thread_id, run_id)
    yield E.custom("activity", {"text": f"decision received: {decision}"})

    if decision == "reject":
        yield E.tool_end(_DEMO_TOOL_ID)
        yield E.tool_result(_DEMO_TOOL_ID,
                            "Skipped by reviewer — command not executed.",
                            is_error=False)
        mid = uuid.uuid4().hex[:12]
        yield E.text_start(mid)
        for c in _chunks("Understood — I skipped the cleanup and left the "
                         "build outputs in place.", 30):
            yield E.text_content(mid, c + " ")
        yield E.text_end(mid)
        yield E.state_delta([{"op": "replace", "path": "/approved", "value": False}])
        yield E.run_finished(thread_id, run_id, result={"reason": "rejected"})
        return

    command = edited_command if (decision == "edit" and edited_command) else _DEMO_CMD
    if decision == "edit":
        yield E.custom("activity", {"text": f"command edited to: {command}"})
        # Replace (not append) the tool args so the card shows what actually
        # ran — TOOL_CALL_ARGS is append-only, so use an explicit replace signal.
        yield E.custom("args_replace", {
            "toolCallId": _DEMO_TOOL_ID,
            "args": json.dumps({"command": command})})

    yield E.tool_end(_DEMO_TOOL_ID)
    yield E.tool_result(
        _DEMO_TOOL_ID,
        f"$ {command}\nremoved 'build/'\nremoved 'dist/'\n> clean: done",
        is_error=False)
    yield E.state_delta([
        {"op": "replace", "path": "/approved", "value": True},
        {"op": "replace", "path": "/toolCalls", "value": 1},
        {"op": "replace", "path": "/step", "value": "rebuild"},
    ])
    yield E.step_finished("await-approval")

    yield E.step_started("rebuild")
    mid = uuid.uuid4().hex[:12]
    yield E.text_start(mid)
    for c in _chunks("Cleanup done. Rebuilding now — outputs regenerated "
                     "cleanly.", 30):
        yield E.text_content(mid, c + " ")
    yield E.text_end(mid)
    yield E.step_finished("rebuild")
    yield E.state_delta([{"op": "replace", "path": "/phase", "value": "done"}])
    yield E.run_finished(thread_id, run_id, result={"reason": "complete"})
