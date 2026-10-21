"""Quota burn rate: stored /usage readings, tick-to-tick spans, the poll loop's inputs.

Readings are written through `record_reading` exactly as the runtime does; the
ledger rows are plain Opus 5.5 messages, so each span's cost is checked against
`pricing.message_cost` rather than a hard-coded rate.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from flightdeck import burn, db, pricing, runtime, usage_poll

T0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
RESET_A = int((T0 + timedelta(hours=4)).timestamp())
ROW_COST = pricing.message_cost("claude-opus-5-5", 0, 0, 0, 0, 50_000)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "b.db"))
    yield c
    c.close()


def _reading(conn, minute, session, week=40.0, reset=RESET_A, fable=None, base=T0):
    rep = {"captured_at": (base + timedelta(minutes=minute)).timestamp(),
           "five_hour": {"used_percentage": session, "resets_at": reset},
           "seven_day": {"used_percentage": week, "resets_at": 1790650740},
           "weekly_models": ([{"label": "Fable", "used_percentage": fable, "resets_at": 1790650740}]
                             if fable is not None else [])}
    burn.record_reading(conn, rep)


def _message(conn, minute, n, session="s1", agent=None, base=T0):
    ts = (base + timedelta(minutes=minute, seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    conn.execute(
        "INSERT INTO messages (uuid, session_id, project, model, ts, input_tokens, cache_read, "
        "cache_create_5m, cache_create_1h, output_tokens, service_tier, agent_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"m{n}", session, "/p", "claude-opus-5-5", ts, 0, 0, 0, 0, 50_000, "standard", agent))
    conn.commit()


def test_record_reading_keeps_models_and_ignores_a_repeat(conn):
    _reading(conn, 0, 6.0, fable=43.0)
    _reading(conn, 0, 7.0, fable=44.0)
    rows = conn.execute("SELECT session_pct, week_pct, models, source FROM quota_readings").fetchall()
    assert len(rows) == 1
    assert rows[0]["session_pct"] == 6.0 and rows[0]["source"] == "poll"
    assert json.loads(rows[0]["models"]) == [{"label": "Fable", "pct": 43.0, "reset_at": 1790650740}]


def test_span_runs_tick_to_tick_and_carries_ledger_figures(conn):
    for minute in range(37):
        _reading(conn, minute, float(minute // 3))
        _message(conn, minute, minute, agent="sub1" if minute % 2 else None)
    out = burn.spans(conn, "session")
    assert out["readings"] == 37 and len(out["spans"]) == 1
    s = out["spans"][0]
    # First tick: 1% first shown at minute 3; the span closes at 11% (minute 33).
    assert (s["start"], s["end"], s["points"]) == ("2026-09-20T10:03:00.000Z",
                                                   "2026-09-20T10:33:00.000Z", 10.0)
    assert s["requests"] == 30 and s["sessions"] == 1
    assert s["cost"] == pytest.approx(30 * ROW_COST, abs=1e-3)
    assert s["cost_per_point"] == pytest.approx(3 * ROW_COST, abs=1e-3)
    assert s["points_per_hour"] == 20.0 and s["edge_gap_s"] == [60, 60]
    assert s["tokens"]["output"] == 30 * 50_000
    # Main agent and subagent alternate each minute: two rows apart = 120 s per gap.
    assert s["agent_minutes"] == pytest.approx(2 * 14 * 2, abs=0.1)
    assert s["subagent_share"] == 0.5
    assert s["flags"] == [] and out["summary"]["cost_per_point"] == s["cost_per_point"]


def test_a_drop_or_a_new_window_starts_a_new_segment(conn):
    pcts = [0, 3, 6, 9, 0, 4, 8, 12]            # drop at index 4: a limit reset
    for i, p in enumerate(pcts):
        _reading(conn, i, float(p))
    later = RESET_A + 5 * 3600                   # the next 5-hour window
    for i, p in enumerate([13, 20, 30]):
        _reading(conn, 20 + i, float(p), reset=later)
    spans = burn.spans(conn, "session", min_points=5)["spans"]
    assert [(s["start"][11:16], s["end"][11:16]) for s in spans] == [
        ("10:01", "10:03"), ("10:05", "10:07"), ("10:21", "10:22")]
    # 13 -> 20 -> 30 in the new window: the first reading of a window is not a
    # tick, so the span is 20 -> 30, not 13 -> 30.
    assert spans[2]["points"] == 10.0


def test_flags_coarse_edges_missing_ledger_and_limit_changes(conn):
    base = datetime(2026, 9, 14, 6, 50, tzinfo=timezone.utc)
    reset = int((base + timedelta(hours=4)).timestamp())
    for minute, p in [(0, 1), (1, 2), (12, 5), (13, 6)]:
        _reading(conn, minute, float(p), reset=reset, base=base)
    s = burn.spans(conn, "session", min_points=3)["spans"][0]
    assert s["edge_gap_s"] == [60, 660]
    assert s["flags"] == ["coarse_edge", "no_ledger", "limit_change"]
    assert "promotion ended" in s["limit_events"][0]
    assert burn.spans(conn, "session", min_points=3)["summary"]["clean_spans"] == 0


def test_model_bucket_is_its_own_meter(conn):
    for minute, f in enumerate([40, 40, 41, 42, 43]):
        _reading(conn, minute, 1.0, fable=float(f))
    out = burn.spans(conn, "fable")
    assert out["readings"] == 5
    assert [(s["start"][11:16], s["points"]) for s in out["spans"]] == [("10:02", 2.0)]


def test_poll_skips_mcp_servers_and_session_files(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="Current session: 6% used · resets Sep 24, "
                                                    "5:09pm (Asia/Ho_Chi_Minh)\n")
    monkeypatch.setattr(usage_poll.subprocess, "run", fake_run)
    assert usage_poll.poll(claude_bin="claude")["five_hour"]["used_percentage"] == 6.0
    assert seen["cmd"] == ["claude", "--strict-mcp-config", "--no-session-persistence",
                           "-p", "/usage"]


def test_poll_once_records_and_marks_a_moving_meter(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_AUDIT_LOCAL_REPORT_FILE", str(tmp_path / "rep.json"))
    rt = runtime.Runtime(SimpleNamespace(), {}, conn)
    assert not rt.is_active()
    reports = iter([6.0, 6.0, 7.0])

    def fake_poll(timeout=60):
        return {"captured_at": datetime.now(timezone.utc).timestamp() + len(seen),
                "five_hour": {"used_percentage": next(reports), "resets_at": RESET_A},
                "seven_day": None, "weekly_models": []}
    seen = []
    monkeypatch.setattr(usage_poll, "poll", fake_poll)
    for _ in range(2):
        seen.append(rt.poll_once())
    assert rt.meter_moved_at == 0.0            # same value twice: not active
    seen.append(rt.poll_once())
    assert rt.is_active() and seen == [True, True, True]
    assert conn.execute("SELECT count(*) n FROM quota_readings").fetchone()["n"] == 3


def test_weights_match_the_longest_prefix_and_skip_other_vendors():
    assert [burn.weight_of(m) for m in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                                        "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5",
                                        "gpt-5.6-sol", None)] == [1.0, 0.5, 0.5, 2.3, 0.75, 1.0,
                                                                  None, None]


def test_other_vendors_neither_count_nor_flag_a_span(conn):
    for minute in range(37):
        _reading(conn, minute, float(minute // 3))
        _message(conn, minute, minute)
    conn.execute(
        "INSERT INTO messages (uuid, session_id, project, model, ts, input_tokens, cache_read, "
        "cache_create_5m, cache_create_1h, output_tokens, service_tier) "
        "VALUES ('g1','s2','/p','gpt-5.6-sol','2026-09-20T10:10:00.000Z',1000,0,0,0,1000,'default')")
    conn.commit()
    s = burn.spans(conn, "session")["spans"][0]
    assert s["flags"] == [] and s["requests"] == 30 and s["sessions"] == 1


def _pair(conn, before, after, gap_min=60, same_window=False):
    t1 = T0.timestamp()
    t2 = t1 + gap_min * 60
    r2 = RESET_A if same_window else RESET_A + 5 * 3600
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return (t1, iso(t1), float(before), RESET_A), (t2, iso(t2), float(after), r2)


def test_step_across_a_reset_is_bounded_by_the_relative_formula(conn):
    k = ROW_COST                              # one ledger row = one point
    # Old window ran out after the 95% reading: 5 + 2 points of ledger use -> the
    # relative formula's (2 + 100) - 95 = 7.
    for n in range(7):
        _message(conn, 5 + n, f"x{n}")
    assert burn.step(conn, *_pair(conn, 95, 2), k) == (7, True)
    # Heavier ledger use than the old window could hold is capped at the same 7.
    for n in range(7, 12):
        _message(conn, 5 + n, f"x{n}")
    assert burn.step(conn, *_pair(conn, 95, 2), k) == (7, True)


def test_step_does_not_invent_use_when_a_window_just_expired(conn):
    # 30% when the window expired, 1% in the next one, one point of ledger use in
    # between: the relative formula alone would say 71.
    _message(conn, 10, "y1")
    assert burn.step(conn, *_pair(conn, 30, 1), ROW_COST) == (1.0, True)
    # Same window, rising: the plain difference.
    assert burn.step(conn, *_pair(conn, 30, 34, same_window=True), ROW_COST) == (4.0, False)


def test_stats_gives_tokens_per_point_and_current_burn_per_model(conn):
    now = T0 + timedelta(minutes=60)
    for minute in range(61):
        _reading(conn, minute, float(minute // 6))      # 10 points in the hour
    for minute in range(0, 60, 2):
        _message(conn, minute, f"o{minute}")              # 30 Opus 5.5 rows
    for uuid, model in (("f1", "claude-fable-5-1"), ("p1", "claude-opus-5")):
        conn.execute(
            "INSERT INTO messages (uuid, session_id, project, model, ts, input_tokens, cache_read, "
            "cache_create_5m, cache_create_1h, output_tokens, service_tier) "
            "VALUES (?,'s3','/p',?,'2026-09-20T10:30:00.000Z',0,0,0,0,50000,'standard')",
            (uuid, model))
    conn.commit()
    out = burn.stats(conn, since="2026-09-20", window_s=3600, now=now.timestamp())
    sess = out["meters"]["session"]
    assert sess["base"]["source"] == "calibrated" and sess["base"]["usd_per_point"] == 1.22
    assert sess["now"]["points"] == 10.0 and sess["now"]["per_hour"] == 10.0
    assert (sess["window_s"], out["meters"]["week"]["window_s"]) == (3600, 6 * 3600)
    # Only the picker's models are listed, used or not; Opus 5 still counts in the total.
    assert [x["model"] for x in out["models"]] == list(burn.LISTED_MODELS)
    assert out["models"][2]["mtok_per_point"]["session"] is None
    opus, fable = out["models"][0], out["models"][1]
    assert (opus["model"], opus["weight"], fable["model"], fable["weight"]) == (
        "claude-opus-5-5", 1.0, "claude-fable-5-1", 2.3)
    # 30 rows x 50k tokens over 30 x ROW_COST dollars, at $1.22 per point.
    assert opus["mtok_per_point"]["session"] == pytest.approx(50_000 / ROW_COST * 1.22 / 1e6, abs=1e-3)
    fable_cost = pricing.message_cost("claude-fable-5-1", 0, 0, 0, 0, 50_000)
    assert fable["now_per_hour"]["session"] == pytest.approx(2.3 * fable_cost / 1.22, abs=0.01)
    opus5_cost = pricing.message_cost("claude-opus-5", 0, 0, 0, 0, 50_000)
    assert sess["now"]["ledger_per_hour"] == pytest.approx(
        opus["now_per_hour"]["session"] + fable["now_per_hour"]["session"] + 0.5 * opus5_cost / 1.22,
        abs=0.02)
