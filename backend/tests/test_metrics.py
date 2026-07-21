import pytest

from flightdeck import db, metrics


def _seed(conn):
    rows = [
        ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-01T00:00:00Z",
         1_000_000, 1_000_000, 0, 0, 0, "standard"),
        ("u2", "s2", "/p", "gpt-4o", "2026-07-02T00:00:00Z",
         500, 0, 0, 0, 500, "standard"),
    ]
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def test_summary_metrics(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    _seed(conn)
    s = metrics.summary(conn, subscription_monthly_usd=200.0, rtk_savings_usd=1.5)
    # u1 cost: 1M input*5/1e6 + 1M read*0.1*5/1e6 = 5 + 0.5 = 5.5 ; u2 unknown -> excluded
    assert round(s["total_cost"], 4) == 5.5
    assert round(s["cache_savings"], 4) == 4.5   # 1M*0.9*5/1e6
    assert s["session_count"] == 2
    assert s["unknown_model_tokens"] == 1000      # u2 input+output
    assert s["rtk_savings"] == 1.5
    # cache hit rate = read / (read + create + input) = 1M / (1M + 0 + 1_000_500)
    assert 0.49 < s["cache_hit_rate"] < 0.5
    # context = input + cache_read + cache_create across all rows
    assert s["message_count"] == 2
    assert s["total_context_tokens"] == 1_000_000 + 1_000_000 + 500  # u1 in+read + u2 in
    assert s["avg_context_per_turn"] == pytest.approx((2_000_500) / 2)


def test_session_context_metrics(tmp_path):
    conn = db.connect(str(tmp_path / "sc.db"))
    rows = [
        # one session, 2 turns: input 1000, cache_read 9000, cache_create 500 total
        ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-01T00:00:00Z",
         500, 4000, 250, 0, 100, "standard"),
        ("u2", "s1", "/p", "claude-opus-4-8", "2026-07-01T01:00:00Z",
         500, 5000, 250, 0, 100, "standard"),
    ]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    (s,) = metrics.sessions(conn)
    assert s["turns"] == 2
    # context = input(1000) + cache_read(9000) + cache_create(500) = 10500
    assert s["context"] == 10_500
    assert s["avg_context"] == pytest.approx(10_500 / 2)
    assert s["cache_ratio"] == pytest.approx(9000 / 10_500)


def test_daily_ordered(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    _seed(conn)
    d = metrics.daily(conn)
    assert [x["date"] for x in d] == ["2026-07-01", "2026-07-02"]


def test_since_filter(tmp_path):
    conn = db.connect(str(tmp_path / "since.db"))
    _seed(conn)  # u1 on 2026-07-01 (opus), u2 on 2026-07-02 (gpt-4o, unpriced)
    # since = 2026-07-02 keeps only u2's day
    assert [x["date"] for x in metrics.daily(conn, since="2026-07-02")] == ["2026-07-02"]
    s = metrics.summary(conn, subscription_monthly_usd=0.0, since="2026-07-02")
    assert s["session_count"] == 1          # only s2
    assert s["message_count"] == 1
    assert s["total_cost"] == 0.0           # u2 is unpriced
    # by_model within window only sees the unpriced model
    assert [m["model"] for m in metrics.by_model(conn, since="2026-07-02")] == ["gpt-4o"]
    # sessions within window
    assert [x["session_id"] for x in metrics.sessions(conn, since="2026-07-02")] == ["s2"]


def test_saved_vs_subscription_prorated(tmp_path):
    conn = db.connect(str(tmp_path / "t2.db"))
    rows = [
        ("u1", "s1", "/p", "claude-opus-4-8", "2026-07-01T00:00:00Z",
         1000, 0, 0, 0, 1000, "standard"),
        ("u2", "s1", "/p", "claude-opus-4-8", "2026-07-02T00:00:00Z",
         1000, 0, 0, 0, 1000, "standard"),
    ]
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    s = metrics.summary(conn, subscription_monthly_usd=300.0)
    expected_subscription_cost = 300.0 * (2 / 30.4375)
    assert s["saved_vs_subscription"] == pytest.approx(
        s["total_cost"] - expected_subscription_cost)
