from flightdeck.quota import pace

WEEK = 7 * 86400


def test_pace_on_pace_lands_exactly_at_reset():
    # 3.5 days in, half the window elapsed, half used: right on pace.
    start = 1_000_000
    now = start + WEEK / 2
    resets_at = start + WEEK
    p = pace({"used_percentage": 50, "resets_at": resets_at}, now, length_s=WEEK)
    assert p["elapsed_pct"] == 50.0
    assert p["ratio"] == 1.0
    assert p["runs_out_before_reset"] is False  # exactly at reset, not before it
    assert round(p["per_day_left"], 4) == round(50 / 3.5, 4)


def test_pace_ahead_of_pace_runs_out_before_reset():
    start = 1_000_000
    elapsed = 25.5 * 3600
    now = start + elapsed
    resets_at = start + WEEK
    used = 29.0
    p = pace({"used_percentage": used, "resets_at": resets_at}, now, length_s=WEEK)

    expected_elapsed_pct = elapsed / WEEK * 100
    expected_ratio = used / expected_elapsed_pct
    expected_runs_out_at = start + elapsed * 100 / used

    assert p["elapsed_pct"] == expected_elapsed_pct
    assert p["ratio"] == expected_ratio
    assert p["runs_out_at"] == expected_runs_out_at
    assert p["runs_out_at"] < resets_at
    assert p["runs_out_before_reset"] is True


def test_pace_missing_data_returns_none():
    assert pace(None, 1_000_000) is None
    assert pace({"used_percentage": 10}, 1_000_000) is None          # no resets_at
    assert pace({"resets_at": 2_000_000}, 1_000_000) is None         # no used_percentage


def test_pace_first_hour_returns_none():
    start = 1_000_000
    resets_at = start + WEEK
    now = start + 3599  # just under an hour in
    assert pace({"used_percentage": 1, "resets_at": resets_at}, now, length_s=WEEK) is None


def test_pace_zero_used_has_no_runs_out_at():
    start = 1_000_000
    resets_at = start + WEEK
    now = start + WEEK / 2
    p = pace({"used_percentage": 0, "resets_at": resets_at}, now, length_s=WEEK)
    assert p["ratio"] == 0.0
    assert p["runs_out_at"] is None
    assert p["runs_out_before_reset"] is False
