import json
import time
import pytest
from flightdeck import quota, usage_poll


@pytest.fixture(autouse=True)
def _isolate_quota_env(monkeypatch):
    # quota.read() consults three env-configured files. Tests set the ones they
    # need; clear the container-poll one so an ambient value (e.g. when the suite
    # runs inside the Docker image) can't leak a real report into the fixtures.
    monkeypatch.delenv("TOKEN_AUDIT_LOCAL_REPORT_FILE", raising=False)

SAMPLE = """You are currently using your subscription to power your Claude Code usage

Current session: 34% used · resets Jul 6, 2:20pm (Asia/Ho_Chi_Minh)
Current week (all models): 46% used · resets Jul 7, 10am (Asia/Ho_Chi_Minh)
Current week (Fable): 32% used · resets Jul 7, 10am (Asia/Ho_Chi_Minh)

What's contributing to your limits usage?
Approximate, based on local sessions on this machine.

Last 24h · 15 requests · 3 sessions
  98% of your usage was at >150k context

Last 7d · 5007 requests · 36 sessions
  85% of your usage was at >150k context
  63% of your usage came from subagent-heavy sessions
"""


def test_parse_usage_windows_and_buckets():
    rep = usage_poll.parse_usage(SAMPLE)
    assert rep["five_hour"]["used_percentage"] == 34.0
    assert rep["seven_day"]["used_percentage"] == 46.0
    assert rep["five_hour"]["resets_at"] is not None  # parsed with zoneinfo
    (fable,) = rep["weekly_models"]
    assert fable["label"] == "Fable" and fable["used_percentage"] == 32.0
    assert rep["insights"]["last_7d"]["lines"][0].startswith("85%")
    assert len(rep["insights"]["last_24h"]["lines"]) == 1


def test_parse_usage_garbage_returns_empty():
    rep = usage_poll.parse_usage("error: something went wrong")
    assert rep["five_hour"] is None and rep["seven_day"] is None
    assert rep["weekly_models"] == []


def test_quota_merges_freshest_and_fable(tmp_path, monkeypatch):
    ql = tmp_path / "usage-quota.json"    # statusline: older
    rp = tmp_path / "usage-report.json"   # poll: fresher + fable
    now = int(time.time())
    ql.write_text(json.dumps({
        "five_hour": {"used_percentage": 13, "resets_at": 1},
        "seven_day": {"used_percentage": 29, "resets_at": 2},
        "captured_at": now - 600,
    }))
    rp.write_text(json.dumps({
        "five_hour": {"used_percentage": 34, "resets_at": 3, "resets_text": "x"},
        "seven_day": {"used_percentage": 46, "resets_at": 4, "resets_text": "y"},
        "weekly_models": [{"label": "Fable", "used_percentage": 32, "resets_at": 4}],
        "insights": {"last_7d": {"header": "h", "lines": ["a"]}},
        "captured_at": now - 60,
    }))
    monkeypatch.setenv("TOKEN_AUDIT_QUOTA_FILE", str(ql))
    monkeypatch.setenv("TOKEN_AUDIT_REPORT_FILE", str(rp))
    q = quota.read()
    assert q["available"] is True
    assert q["five_hour"]["used_percentage"] == 34   # fresher report wins
    assert q["weekly_models"][0]["label"] == "Fable"
    assert q["sources"]["statusline_age"] >= 590
    assert q["age_seconds"] < 120                    # freshest overall


def test_quota_statusline_fresher_wins(tmp_path, monkeypatch):
    ql = tmp_path / "usage-quota.json"
    rp = tmp_path / "usage-report.json"
    now = int(time.time())
    ql.write_text(json.dumps({
        "five_hour": {"used_percentage": 40, "resets_at": 9},
        "captured_at": now - 10,
    }))
    rp.write_text(json.dumps({
        "five_hour": {"used_percentage": 34, "resets_at": 3},
        "weekly_models": [],
        "captured_at": now - 500,
    }))
    monkeypatch.setenv("TOKEN_AUDIT_QUOTA_FILE", str(ql))
    monkeypatch.setenv("TOKEN_AUDIT_REPORT_FILE", str(rp))
    assert quota.read()["five_hour"]["used_percentage"] == 40


def test_quota_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_AUDIT_QUOTA_FILE", str(tmp_path / "a.json"))
    monkeypatch.setenv("TOKEN_AUDIT_REPORT_FILE", str(tmp_path / "b.json"))
    q = quota.read()
    assert q["available"] is False and q["age_seconds"] is None
