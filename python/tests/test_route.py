import json

from flightdeck import route


def _line(role, content, uuid, ts):
    return json.dumps({
        "type": role,
        "uuid": uuid,
        "sessionId": "route-session",
        "cwd": "/project",
        "timestamp": ts,
        "message": {"role": role, "content": content},
    })


def _user(text, n):
    return _line("user", text, f"u{n}", f"2026-07-02T00:{n:02d}:00Z")


def _assistant(blocks, n):
    return _line("assistant", blocks, f"a{n}", f"2026-07-02T00:{n:02d}:30Z")


def _result(tool_id, n, error=False):
    return _line("user", [{
        "type": "tool_result", "tool_use_id": tool_id,
        "content": "failed" if error else "ok", "is_error": error,
    }], f"r{n}", f"2026-07-02T00:{n:02d}:45Z")


def _write(tmp_path, lines):
    path = tmp_path / "route-session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_build_route_filters_runtime_wrappers_and_classifies_lanes(tmp_path):
    path = _write(tmp_path, [
        _user("<local-command-stdout>Set model</local-command-stdout>", 0),
        _user("Inspect the existing implementation", 1),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Read",
                     "input": {"file_path": "/project/app.py"}}], 2),
        _result("t1", 3),
        _user("Implement the change", 4),
        _assistant([{"type": "tool_use", "id": "t2", "name": "Edit",
                     "input": {"file_path": "/project/app.py"}}], 5),
        _result("t2", 6),
        _user("Run the tests", 7),
        _assistant([{"type": "tool_use", "id": "t3", "name": "Bash",
                     "input": {"command": "pytest -q"}}], 8),
        _result("t3", 9),
    ])

    data = route.build_route(str(path), max_waypoints=12)

    assert data["source"] == {
        "turn_count": 10,
        "instruction_count": 3,
        "clearance_count": 3,
        "atomic_segment_count": 4,  # includes pre-instruction session setup
        "tool_count": 3,
        "error_count": 0,
    }
    assert [wp["lane"] for wp in data["waypoints"]] == [
        "brief", "research", "build", "verify",
    ]
    assert data["waypoints"][0]["label"] == "Session setup"


def test_build_route_compacts_to_budget_without_losing_telemetry(tmp_path):
    lines = []
    for i in range(12):
        base = i * 3
        lines.extend([
            _user(f"Change component {i}", base),
            _assistant([{"type": "tool_use", "id": f"t{i}", "name": "Edit",
                         "input": {"file_path": f"/project/{i}.py"}}], base + 1),
            _result(f"t{i}", base + 2, error=i == 6),
        ])
    path = _write(tmp_path, lines)

    data = route.build_route(str(path), max_waypoints=4)

    assert data["projection"]["waypoint_count"] == 4
    assert data["projection"]["compression_ratio"] == 9.0
    assert sum(wp["turn_count"] for wp in data["waypoints"]) == 36
    assert sum(wp["tool_count"] for wp in data["waypoints"]) == 12
    assert sum(wp["error_count"] for wp in data["waypoints"]) == 1
    assert any(wp["error_count"] for wp in data["waypoints"])


def test_load_session_route_adds_session_id_and_clamps_budget(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    path = project / "route-session.jsonl"
    path.write_text(_user("Do the work", 1) + "\n")

    data = route.load_session_route(str(tmp_path), "route-session", max_waypoints=1)

    assert data["session_id"] == "route-session"
    assert data["projection"]["max_waypoints"] == route.MIN_WAYPOINTS
    assert route.load_session_route(str(tmp_path), "missing") is None


def test_compaction_summary_is_not_a_human_clearance(tmp_path):
    path = _write(tmp_path, [
        _user("Do the first task", 1),
        _assistant([{"type": "text", "text": "Done"}], 2),
        _user("This session is being continued from a previous conversation that ran out of context.", 3),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Read",
                     "input": {"file_path": "/project/app.py"}}], 4),
        _result("t1", 5),
    ])

    data = route.build_route(str(path), max_waypoints=12)

    assert data["source"]["clearance_count"] == 1
    assert data["source"]["atomic_segment_count"] == 1
    assert data["waypoints"][0]["instruction_count"] == 1
