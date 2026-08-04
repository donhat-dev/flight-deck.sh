"""Numbers must not move when the aggregation moves into SQL.

`summary`/`daily`/`by_model`/`sessions` each pulled every message row into Python
and called `pricing.message_cost` once per row — 129,578 calls per metric per
range, four ranges per snapshot. One `build_snapshot` took 4.1s and the debounce
is 2.0s, so an active session left FlightDeck rebuilding continuously.

The fix aggregates in SQL at the (day, model, session) grain: 129,578 rows ->
628, a 206x cut in pricing calls, one query serving all four metrics and all four
ranges. That is only sound because a rate is uniform inside one (model, UTC day)
cell: `pricing.rate_for` picks a promotion by `ts < until` where `until` is a
DATE, so no boundary can fall mid-day. `test_the_promo_boundary_is_a_day_boundary`
below pins that assumption — if a future promotion carried a time, the grain
would silently misprice and this is the test that would fail.

Every expected value here was captured from the pre-refactor implementation, so
the suite is a genuine before/after lock rather than a restatement of the new
code. The fixture is built to exercise what could break: a promo boundary, a
session spanning two days, an unpriced model, `<synthetic>` (counted by `daily`
but excluded by `by_model`), and an empty `ts`.
"""
import sqlite3

import pytest

from flightdeck import db, metrics, pricing

ROWS = [
    # uuid, session, project, model, ts, input, cache_read, c5m, c1h, output, tier
    ("u1", "s1", "projA", "claude-sonnet-5",  "2026-08-31T10:00:00Z", 1000, 5000, 200,  0, 300, "std"),
    ("u2", "s1", "projA", "claude-sonnet-5",  "2026-08-31T11:00:00Z",  500, 2000, 100,  0, 150, "std"),
    ("u3", "s1", "projA", "claude-sonnet-5",  "2026-09-01T09:00:00Z",  700, 3000,   0, 50, 200, "std"),
    ("u4", "s2", "projB", "claude-opus-5",    "2026-09-01T12:00:00Z", 2000, 8000, 400,  0, 900, "std"),
    ("u5", "s2", "projB", "claude-haiku-4-5", "2026-09-01T13:00:00Z",  100,  400,   0,  0,  50, "std"),
    ("u6", "s3", "projC", "model-from-mars",  "2026-09-02T08:00:00Z",  111,  222,  33,  0,  44, "std"),
    ("u7", "s3", "projC", "<synthetic>",      "2026-09-02T08:30:00Z",   10,   20,   0,  0,   5, "std"),
    ("u8", "s4", "projD", "claude-opus-5",    "",                       60,   70,   0,  0,  80, "std"),
]


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr(db, "_URL", None)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db._SCHEMA)
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", ROWS)
    c.execute("INSERT INTO session_meta VALUES ('s2','Named session','custom')")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def approx(d, **skip):
    return {k: (v if isinstance(v, (str, bool, type(None), list)) else pytest.approx(v))
            for k, v in d.items()}


# --------------------------------------------------------- the pricing invariant

def test_the_promo_boundary_is_a_day_boundary():
    """The grain's licence to exist.

    Pricing a whole (model, day) cell at one rate is exact only while every
    promotion ends on a date. A promotion carrying a time of day would split a
    cell and the rollup would misprice half of it with no error anywhere.
    """
    for prefix, until, _rate in pricing.PROMOTIONS:
        assert len(until) == 10 and until.count("-") == 2, (
            f"promotion for {prefix} ends at {until!r}, which is not a bare date — "
            "the (day, model) grain can no longer price a cell uniformly")


def test_the_boundary_really_changes_the_rate(conn):
    # Anti-vacuity for the fixture: if both days priced the same, every test
    # below would pass without ever exercising a rate change.
    before = pricing.rate_for("claude-sonnet-5", "2026-08-31T23:59:59Z")
    after = pricing.rate_for("claude-sonnet-5", "2026-09-01T00:00:00Z")
    assert before == (2.0, 10.0) and after == (3.0, 15.0)


# ------------------------------------------------------------------------ daily

def test_daily_matches_the_pre_refactor_numbers(conn):
    got = metrics.daily(conn)
    assert got == [
        # The empty ts lands in a "" bucket, kept for parity: dropping it would
        # silently lose the row's cost from the daily chart's total.
        {"date": "",           "cost": pytest.approx(0.002335), "input": 60,   "output": 80,   "cache_read": 70},
        {"date": "2026-08-31", "cost": pytest.approx(0.00965),  "input": 1500, "output": 450,  "cache_read": 7000},
        {"date": "2026-09-01", "cost": pytest.approx(0.04569),  "input": 2800, "output": 1150, "cache_read": 11400},
        {"date": "2026-09-02", "cost": pytest.approx(0.0),      "input": 121,  "output": 49,   "cache_read": 242},
    ]


def test_daily_prices_the_two_sides_of_the_boundary_differently(conn):
    by_day = {d["date"]: d for d in metrics.daily(conn)}
    # 08-31: 1500 in + 7000 read + 300 write at the PROMO $2/$10.
    # Same shape on 09-01 would cost more; the two must not be equal per token.
    aug = by_day["2026-08-31"]["cost"] / (1500 + 7000 + 300 + 450)
    assert aug == pytest.approx(0.00965 / 9250)


def test_daily_since_filters_without_changing_the_kept_rows(conn):
    assert metrics.daily(conn, since="2026-09-01") == [
        {"date": "2026-09-01", "cost": pytest.approx(0.04569), "input": 2800, "output": 1150, "cache_read": 11400},
        {"date": "2026-09-02", "cost": pytest.approx(0.0),     "input": 121,  "output": 49,   "cache_read": 242},
    ]


# --------------------------------------------------------------------- by_model

def test_by_model_matches_the_pre_refactor_numbers(conn):
    got = metrics.by_model(conn)
    assert [m["model"] for m in got] == [
        "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "model-from-mars",
    ], "ordered by cost descending"
    assert got[0] == {"model": "claude-opus-5", "input": 2060, "input_raw": 10530,
                      "output": 980, "cache_read": 8070,
                      "cost": pytest.approx(0.041335), "priced": True}
    assert got[1] == {"model": "claude-sonnet-5", "input": 2200, "input_raw": 12550,
                      "output": 650, "cache_read": 10000,
                      "cost": pytest.approx(0.01595), "priced": True}
    assert got[3] == {"model": "model-from-mars", "input": 111, "input_raw": 366,
                      "output": 44, "cache_read": 222,
                      "cost": pytest.approx(0.0), "priced": False}


def test_by_model_excludes_synthetic_but_daily_counts_it(conn):
    assert "<synthetic>" not in {m["model"] for m in metrics.by_model(conn)}
    # daily's 09-02 bucket is mars (111/44/222) PLUS synthetic (10/5/20).
    d = {x["date"]: x for x in metrics.daily(conn)}["2026-09-02"]
    assert (d["input"], d["output"], d["cache_read"]) == (121, 49, 242)


def test_by_model_sums_one_model_across_the_rate_boundary(conn):
    # sonnet-5 spans both rates. A single blended number here would hide the
    # split, so the total is checked against the two day cells that make it up.
    per_day = {x["date"]: x["cost"] for x in metrics.daily(conn)}
    sonnet = [m for m in metrics.by_model(conn) if m["model"] == "claude-sonnet-5"][0]
    # 08-31 is sonnet only; 09-01 is sonnet + opus + haiku.
    assert sonnet["cost"] == pytest.approx(per_day["2026-08-31"] + 0.00630)


# ---------------------------------------------------------------------- summary

def test_summary_matches_the_pre_refactor_numbers(conn):
    got = metrics.summary(conn, 200.0, 1.5)
    assert got == approx({
        "total_cost": 0.057675,
        "cache_savings": 0.057375,
        "saved_vs_subscription": -13.084008778234088,
        "rtk_savings": 1.5,
        "cache_hit_rate": 0.7804471137804471,
        "input_tokens": 4481,
        "input_tokens_raw": 23976,
        "output_tokens": 1729,
        "session_count": 4,
        "unknown_model_tokens": 445,
        "message_count": 8,
        "total_context_tokens": 23976,
        "avg_context_per_turn": 2997.0,
    })


def test_summary_since_matches_the_pre_refactor_numbers(conn):
    got = metrics.summary(conn, 200.0, 1.5, since="2026-09-01")
    assert got["total_cost"] == pytest.approx(0.04569)
    assert got["message_count"] == 5
    assert got["session_count"] == 3
    assert got["unknown_model_tokens"] == 445
    assert got["saved_vs_subscription"] == pytest.approx(-6.525151889117044)


def test_the_empty_ts_is_excluded_from_the_subscription_span(conn):
    """The trap the grain walks into.

    The row loop skipped a falsy `ts` when tracking min/max, but SQL `min(ts)`
    returns '' because '' sorts below any timestamp — which would stretch the
    span to an unparseable date and zero out `saved_vs_subscription`. A rollup
    must use `min(NULLIF(ts,''))`.
    """
    got = metrics.summary(conn, 200.0, 0.0)
    # Span runs 2026-08-31T10:00 -> 2026-09-02T08:30, and `timedelta.days`
    # TRUNCATES: 46.5 hours is `.days == 1`, so `span_days` is 2, not 3. Spelled
    # out because it is the kind of off-by-one a rollup keyed on calendar dates
    # would "fix" by accident, changing every subscription figure.
    assert got["saved_vs_subscription"] == pytest.approx(
        0.057675 - 200.0 * (2 / 30.4375))


def test_session_count_is_distinct_not_a_sum_of_days(conn):
    # s1 spans 08-31 and 09-01. Summing per-day distinct counts would say 5.
    assert metrics.summary(conn, 200.0)["session_count"] == 4


# --------------------------------------------------------------------- sessions

def test_sessions_match_the_pre_refactor_numbers(conn):
    got = metrics.sessions(conn, 100, 0)
    assert [s["session_id"] for s in got] == ["s3", "s2", "s1", "s4"], \
        "ordered by last_ts descending; the empty-ts session sorts last"

    s2 = [s for s in got if s["session_id"] == "s2"][0]
    assert s2 == approx({
        "session_id": "s2", "project": "projB", "title": "Named session",
        "models": ["claude-haiku-4-5", "claude-opus-5"],
        "first_ts": "2026-09-01T12:00:00Z", "last_ts": "2026-09-01T13:00:00Z",
        "turns": 2, "input": 2100, "output": 950, "cache_read": 8400,
        "cache_create": 400, "cost": 0.03939, "context": 10900,
        "input_raw": 10900, "avg_context": 5450.0,
        "cache_ratio": 0.7706422018348624,
    })

    s1 = [s for s in got if s["session_id"] == "s1"][0]
    # Spans both rate days: first_ts from 08-31, last_ts from 09-01, and the cost
    # must be the sum of the two differently-priced parts, not 3 turns at one rate.
    assert s1["first_ts"] == "2026-08-31T10:00:00Z"
    assert s1["last_ts"] == "2026-09-01T09:00:00Z"
    assert s1["turns"] == 3
    assert s1["cost"] == pytest.approx(0.01595)
    assert s1["models"] == ["claude-sonnet-5"]


def test_sessions_keeps_synthetic_in_a_sessions_own_models(conn):
    # by_model drops <synthetic>; a session's model list does not, because the
    # turn still happened inside that session.
    s3 = [s for s in metrics.sessions(conn, 100, 0) if s["session_id"] == "s3"][0]
    assert s3["models"] == ["<synthetic>", "model-from-mars"]
    assert s3["turns"] == 2


def test_sessions_limit_and_offset_still_page(conn):
    assert [s["session_id"] for s in metrics.sessions(conn, 2, 0)] == ["s3", "s2"]
    assert [s["session_id"] for s in metrics.sessions(conn, 2, 2)] == ["s1", "s4"]


def test_sessions_since_drops_sessions_with_no_rows_in_range(conn):
    got = metrics.sessions(conn, 100, 0, since="2026-09-01")
    assert [s["session_id"] for s in got] == ["s3", "s2", "s1"]
    # s1's numbers must be the IN-RANGE slice only (u3), not the whole session.
    s1 = [s for s in got if s["session_id"] == "s1"][0]
    assert s1["turns"] == 1
    assert s1["first_ts"] == "2026-09-01T09:00:00Z"


def test_a_session_with_no_title_row_reports_none(conn):
    s1 = [s for s in metrics.sessions(conn, 100, 0) if s["session_id"] == "s1"][0]
    assert s1["title"] is None


# ------------------------------------------------------------------ consistency

def test_the_metrics_agree_with_each_other(conn):
    """Cross-check: three functions built on one grain must not disagree.

    The old code computed each independently from the same rows, so a divergence
    here means the grain lost or double-counted something.
    """
    s = metrics.summary(conn, 200.0)
    assert sum(d["cost"] for d in metrics.daily(conn)) == pytest.approx(s["total_cost"])
    assert sum(d["input"] for d in metrics.daily(conn)) == s["input_tokens"]
    assert sum(d["output"] for d in metrics.daily(conn)) == s["output_tokens"]
    # by_model excludes <synthetic>, so it is short by exactly that row.
    synth_cost = 0.0   # <synthetic> is unpriced
    assert sum(m["cost"] for m in metrics.by_model(conn)) == pytest.approx(
        s["total_cost"] - synth_cost)
    assert sum(x["turns"] for x in metrics.sessions(conn, 100, 0)) == s["message_count"]
