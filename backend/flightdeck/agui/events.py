"""AG-UI event vocabulary + SSE framing.

This is an *internal* implementation of the AG-UI protocol's event schema — the
same event `type`s, field names and semantics (RUN_*, TEXT_MESSAGE_*,
TOOL_CALL_*, STATE_SNAPSHOT/DELTA, STEP_*, CUSTOM) — without depending on the
AG-UI SDK (still 0.x). Keeping the wire shape faithful means a real
`@ag-ui/client` could consume this stream, and we can later swap our adapter
for the SDK without changing the frontend contract.

Ref: https://docs.ag-ui.com/concepts/events — event names are SCREAMING_SNAKE,
timestamps are epoch milliseconds, and each event is delivered as one SSE
`data:` frame carrying the JSON object.
"""
import json
import time

# --- lifecycle ---------------------------------------------------------------
RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
RUN_ERROR = "RUN_ERROR"
STEP_STARTED = "STEP_STARTED"
STEP_FINISHED = "STEP_FINISHED"
# --- text messages -----------------------------------------------------------
TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
# --- tool calls --------------------------------------------------------------
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_END = "TOOL_CALL_END"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
# --- state sync --------------------------------------------------------------
STATE_SNAPSHOT = "STATE_SNAPSHOT"
STATE_DELTA = "STATE_DELTA"
# --- extension ---------------------------------------------------------------
CUSTOM = "CUSTOM"


def _now_ms() -> int:
    return int(time.time() * 1000)


def event(type_: str, **fields) -> dict:
    """Build one AG-UI event object (type + timestamp + fields), dropping any
    None-valued field so the wire stays clean."""
    out = {"type": type_, "timestamp": _now_ms()}
    out.update({k: v for k, v in fields.items() if v is not None})
    return out


def sse(evt: dict) -> str:
    """Frame one event as an SSE `data:` block."""
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


# --- convenience builders (spec field names) --------------------------------
def run_started(thread_id, run_id):
    return event(RUN_STARTED, threadId=thread_id, runId=run_id)


def run_finished(thread_id, run_id, result=None):
    return event(RUN_FINISHED, threadId=thread_id, runId=run_id, result=result)


def run_error(message, code=None):
    return event(RUN_ERROR, message=message, code=code)


def step_started(name):
    return event(STEP_STARTED, stepName=name)


def step_finished(name):
    return event(STEP_FINISHED, stepName=name)


def text_start(message_id, role="assistant"):
    return event(TEXT_MESSAGE_START, messageId=message_id, role=role)


def text_content(message_id, delta):
    return event(TEXT_MESSAGE_CONTENT, messageId=message_id, delta=delta)


def text_end(message_id):
    return event(TEXT_MESSAGE_END, messageId=message_id)


def tool_start(tool_call_id, tool_name, parent_message_id=None):
    return event(TOOL_CALL_START, toolCallId=tool_call_id,
                 toolCallName=tool_name, parentMessageId=parent_message_id)


def tool_args(tool_call_id, delta):
    return event(TOOL_CALL_ARGS, toolCallId=tool_call_id, delta=delta)


def tool_end(tool_call_id):
    return event(TOOL_CALL_END, toolCallId=tool_call_id)


def tool_result(tool_call_id, content, message_id=None, is_error=False):
    return event(TOOL_CALL_RESULT, toolCallId=tool_call_id,
                 messageId=message_id, content=content, isError=is_error or None)


def state_snapshot(snapshot):
    return event(STATE_SNAPSHOT, snapshot=snapshot)


def state_delta(patch):
    # `patch` is a list of RFC-6902 JSON Patch operations.
    return event(STATE_DELTA, delta=patch)


def custom(name, value):
    return event(CUSTOM, name=name, value=value)
