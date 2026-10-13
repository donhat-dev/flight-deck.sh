import json

from conftest import load

tr = load("transcript_reader")

ORIGIN = {"kind": "peer", "from": "uds:/run/user/1000/cc-socks/42.sock", "msg_id": "m-1",
          "name": "projects-ce", "fromMode": "prompting", "body": "Rebased onto 266b862."}
WRAPPED = ('Another Claude session sent a message:\n<cross-session-message from="uds:/run/user/1000/'
           'cc-socks/42.sock" from-name="projects-ce" from-mode="prompting">\nRebased onto 266b862.\n'
           '</cross-session-message>')


def line(obj):
    return (json.dumps(obj) + "\n").encode()


def idle_message():
    return {"type": "user", "uuid": "u-1", "sessionId": "s-1", "timestamp": "2026-09-29T13:39:19.165Z",
            "isMeta": True, "message": {"role": "user", "content": WRAPPED}, "origin": ORIGIN}


def queued_message():
    return {"type": "attachment", "uuid": "u-2", "sessionId": "s-1", "timestamp": "2026-09-29T12:21:12Z",
            "attachment": {"type": "queued_command", "prompt": WRAPPED, "origin": dict(ORIGIN, msg_id="m-2")}}


def send_call():
    return {"type": "assistant", "uuid": "u-3", "timestamp": "2026-09-29T12:34:10Z",
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "id": "toolu_1", "name": "SendMessage",
                "input": {"to": "projects-ce", "summary": "M5 touch list", "message": "The list is below."}}]}}


def send_answer():
    return {"type": "user", "uuid": "u-4", "timestamp": "2026-09-29T12:34:19Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                      "content": [{"type": "text", "text": "{}"}]}]},
            "toolUseResult": {"success": True, "msg_id": "m-9"}}


def hand_back():
    """A subagent's result coming back to its parent: `kind: peer` without an id."""
    return {"type": "user", "uuid": "u-5", "isMeta": True,
            "message": {"role": "user", "content": "[Subagent hand-back] The text below is the final reply."},
            "origin": {"kind": "peer", "body": "[Subagent hand-back] The text below is the final reply."}}


def test_a_message_that_starts_a_turn_is_a_peer_row_with_its_body():
    row, results = tr.parse_line(line(idle_message()), 7)
    assert results == {}
    assert (row["role"], row["text"], row["peer_name"], row["peer_mode"], row["peer_msg_id"]) == \
        ("peer", "Rebased onto 266b862.", "projects-ce", "turn", "m-1")
    assert row["peer_from"] == ORIGIN["from"] and row["seq"] == 7 and row["uuid"] == "u-1"


def test_a_message_queued_into_a_running_turn_is_read_too():
    row, _results = tr.parse_line(line(queued_message()), 1)
    assert (row["role"], row["peer_mode"], row["peer_msg_id"]) == ("peer", "queued", "m-2")


def test_a_subagent_hand_back_is_not_a_peer_message():
    row, _results = tr.parse_line(line(hand_back()), 1)
    assert row["role"] == "user", "it stays the user line it always was"
    assert tr.peer_origin(hand_back()) is None


def test_other_attachments_still_give_no_row():
    row, _results = tr.parse_line(line({"type": "attachment", "uuid": "u-6",
                                        "attachment": {"type": "queued_command", "prompt": "hi"}}), 1)
    assert row is None


def test_the_scan_finds_both_sides_and_the_first_timestamp(tmp_path):
    path = tmp_path / "s.jsonl"
    tools = {"type": "attachment", "attachment": {"type": "deferred_tools_delta",
                                                  "addedNames": ["SendMessage"], "note": "msg_id"}}
    first = {"type": "system", "timestamp": "2026-09-23T02:42:54Z"}
    body = b"".join(line(o) for o in (first, tools, send_call(), send_answer(), queued_message(), hand_back()))
    path.write_bytes(body + b'{"type": "user", "unfinished')
    events, consumed, first_ts = tr.scan_peer_messages(str(path), 0)
    assert consumed == len(body), "the line still being written is left for the next pass"
    assert first_ts == "2026-09-23T02:42:54+00:00"
    assert [e["kind"] for e in events] == ["send", "sent", "received"]
    send, sent, received = events
    assert (send["tool_use_id"], send["to"], send["summary"], send["body"]) == \
        ("toolu_1", "projects-ce", "M5 touch list", "The list is below.")
    assert (sent["tool_use_id"], sent["msg_id"]) == ("toolu_1", "m-9")
    assert (received["msg_id"], received["name"], received["mode"]) == ("m-2", "projects-ce", "queued")


def test_a_send_answered_without_an_id_is_reported_once(tmp_path):
    """SendMessage to the session's own subagent: answered, but with no message id."""
    to_agent = send_call()
    to_agent["message"]["content"][0]["id"] = "toolu_2"
    resumed = {"type": "user", "uuid": "u-7", "timestamp": "2026-09-29T12:34:20Z",
               "message": {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "toolu_2",
                    "content": '{"success":true,"message":"Resuming agent ae7eb31"}'},
                   {"type": "tool_result", "tool_use_id": "toolu_other", "content": "unrelated"}]}}
    refused = {"type": "user", "uuid": "u-8", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_3", "is_error": True,
         "content": "Not a message a person confirmed."}]}}
    path = tmp_path / "s.jsonl"
    path.write_bytes(line(to_agent) + line(resumed) + line(refused))
    events, _consumed, _first = tr.scan_peer_messages(str(path), 0, pending=["toolu_3"])
    assert [(e["kind"], e["tool_use_id"]) for e in events] == \
        [("send", "toolu_2"), ("answered", "toolu_2"), ("answered", "toolu_3")]
    assert (events[1]["error"], events[2]["error"]) == (False, True)
    assert events[2]["text"] == "Not a message a person confirmed."


def test_the_scan_resumes_and_stops_at_its_limit(tmp_path):
    path = tmp_path / "s.jsonl"
    head = line({"type": "system", "timestamp": "2026-09-23T02:42:54Z"})
    path.write_bytes(head + line(send_call()) + line(send_answer()))
    events, consumed, first_ts = tr.scan_peer_messages(str(path), len(head), limit=1)
    assert first_ts is None, "only a read from the start knows where the file starts"
    assert [e["kind"] for e in events] == ["send"]
    events, _consumed, _first = tr.scan_peer_messages(str(path), len(head) + consumed)
    assert events == [], "an answer to a call no pass has seen is not a send"
    events, _consumed, _first = tr.scan_peer_messages(str(path), len(head) + consumed,
                                                      pending=["toolu_1"])
    assert [e["kind"] for e in events] == ["sent"]
