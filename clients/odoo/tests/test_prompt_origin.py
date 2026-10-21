"""Which user lines start a turn, by the CLI's turnOrigin."""
from conftest import load

reader = load("activity_reader")


def line(origin, text="Reply with exactly: ok"):
    return {"type": "user", "turnOrigin": origin, "promptId": "p-1",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def test_typed_and_headless_prompts_start_a_turn():
    assert reader.is_human_prompt(line("human"))
    # A runner session or `claude -p`: since CLI 2.1.283 its prompts carry `sdk`.
    assert reader.is_human_prompt(line("sdk"))


def test_messages_inside_a_turn_do_not():
    for origin in ("peer", "task_notification", "scheduled", "system"):
        assert not reader.is_human_prompt(line(origin)), origin


def test_compaction_summary_does_not():
    summary = dict(line("sdk"), isCompactSummary=True)
    assert not reader.is_human_prompt(summary)
