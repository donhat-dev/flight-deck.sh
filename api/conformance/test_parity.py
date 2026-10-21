"""Parity: `fd serve` and the FastAPI app answer the same.

One transcript fixture is written into a shared temp projects dir in three
passes — a prefix, an append that continues an open turn across 00:00 UTC,
then replayed/copied records. After each pass the Go ledger is updated by
`fd ingest` and the Python one by the app's own reingest (POST /api/reingest,
the production path), and both servers' /api/daily, /api/sessions and
/api/summary answers are compared. agent_hours*/agent_hours_sub must match
to 1e-9 AND equal the seconds the fixture was built to produce — equality
alone would not catch both sides counting zero.

Both servers see the same fake `rtk` on PATH ("Tokens saved: 1.2K" -> 1200 *
$5/MTok = $0.006) so rtk_savings must be equal and non-zero. Costs compare on
the default price table both sides ship (pricing.py's RATES ==
go/core/pricing/defaults.json); subagent and day costs must match to 1e-6.

Line shapes mirror python/tests/test_ingest_dedupe_activity.py, which the Go
tracker was ported against. Expected busy seconds per pass (idle cap 15 min —
TOKEN_AUDIT_AGENT_IDLE_CAP_MIN cleared on both sides, Go has no other source):

  pass 1  2026-10-20: main 1080 (turn p1 60+30+30; turn p2 900 cap +60),
          subagent a1 120; turn p3 left open at 23:58
  pass 2  +120 on 10-20 (23:58->23:59, 23:59->00:00), +120 on 10-21
          (00:00->00:01, 00:01->00:02); turn p3 closes
  pass 3  s1 replays (old timestamps) and s2's copied history add nothing;
          s2's own turn adds 60 on 10-21
"""
import json
import os
import signal
import stat
import subprocess
import time
import urllib.request

import pytest
import yaml
from fastapi.testclient import TestClient

import envpins
from conftest import (FD, SPEND_YAML, clean_env, free_port, run_fd,
                      wait_health)

_SPEND = yaml.safe_load(SPEND_YAML.read_text())

D1, D2 = "2026-10-20", "2026-10-21"
RTK_USD = 1200 * 5.0 / 1e6  # "Tokens saved: 1.2K" at the shared $5/MTok rate


def _prompt(uuid, ts, session="s1", prompt="p1", text="do the thing"):
    return {"type": "user", "uuid": uuid, "sessionId": session, "cwd": "/p",
            "timestamp": ts, "promptId": prompt, "turnOrigin": "human",
            "message": {"role": "user", "content": text}}


def _asst(uuid, ts, msg, out, session="s1", model="claude-opus-5", stop=None,
          req=None, agent=None):
    o = {"type": "assistant", "uuid": uuid, "sessionId": session, "cwd": "/p",
         "timestamp": ts, "requestId": req or f"r-{msg}",
         "message": {"id": msg, "model": model, "stop_reason": stop,
                     "content": [{"type": "text", "text": f"text {uuid}"}],
                     "usage": {"input_tokens": 3,
                               "cache_read_input_tokens": 200,
                               "output_tokens": out,
                               "service_tier": "standard",
                               "cache_creation": {
                                   "ephemeral_5m_input_tokens": 0,
                                   "ephemeral_1h_input_tokens": 50}}}}
    if agent:
        o["agentId"] = agent
        o["isSidechain"] = True
        o["promptId"] = "p1"
        o["attributionAgent"] = "researcher"
    return o


def _tool_result(uuid, ts, session="s1", prompt="p1"):
    return {"type": "user", "uuid": uuid, "sessionId": session,
            "timestamp": ts, "promptId": prompt,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def _write(path, objs, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as fh:
        for o in objs:
            fh.write(json.dumps(o) + "\n")


# --- the fixture, per pass ---------------------------------------------------

# s1's first stretch: two closed turns, then p3 left open at 23:58.
S1_PASS1 = [
    _prompt("h1", f"{D1}T10:00:00Z"),
    _asst("a1", f"{D1}T10:01:00Z", "M1", 10),
    _tool_result("r1", f"{D1}T10:01:30Z"),
    _asst("a2", f"{D1}T10:02:00Z", "M2", 20, stop="end_turn"),
    _prompt("h2", f"{D1}T10:10:00Z", prompt="p2", text="again"),
    _asst("a3", f"{D1}T10:30:00Z", "M3", 30),   # 20-min gap -> idle cap (900 s)
    _asst("a4", f"{D1}T10:31:00Z", "M4", 40, stop="end_turn"),
    _prompt("h3", f"{D1}T23:58:00Z", prompt="p3", text="late shift"),
    {"type": "custom-title", "sessionId": "s1", "customTitle": "Parity session"},
]

# One subagent on p1: dispatch line, then two billed responses.
AGENT_A1 = [
    {"type": "user", "uuid": "d1", "sessionId": "s1", "cwd": "/p",
     "timestamp": f"{D1}T10:00:10Z", "promptId": "p1",
     "message": {"role": "user", "content": "map the auth flow"}},
    _asst("x1", f"{D1}T10:01:10Z", "MA1", 11, model="claude-sonnet-4-5",
          agent="a1"),
    _asst("x2", f"{D1}T10:02:10Z", "MA2", 12, model="claude-sonnet-4-5",
          stop="end_turn", agent="a1"),
]

# s1's open turn continues across 00:00 UTC and closes.
S1_PASS2 = [
    _asst("a5", f"{D1}T23:59:00Z", "M5", 50),
    _asst("a6", f"{D2}T00:01:00Z", "M6", 60),
    _asst("a7", f"{D2}T00:02:00Z", "M7", 70, stop="end_turn"),
]

# s1 replays its own earlier lines (same uuids, old timestamps); s2 is a
# resume: copied history under new uuids/session, then a real turn of its own.
S1_PASS3 = [_prompt("h1", f"{D1}T10:00:00Z"),
            _asst("a1", f"{D1}T10:01:00Z", "M1", 10),
            _asst("a2", f"{D1}T10:02:00Z", "M2", 20, stop="end_turn")]
S2 = [
    _prompt("h1-copy", f"{D1}T10:00:00Z", session="s2"),
    _asst("a1-copy", f"{D1}T10:01:00Z", "M1", 10, session="s2", req="r-M1"),
    _asst("a2-copy", f"{D1}T10:02:00Z", "M2", 20, session="s2", req="r-M2",
          stop="end_turn"),
    _prompt("h4", f"{D2}T12:00:00Z", session="s2", prompt="p4",
            text="pick it up"),
    _asst("a8", f"{D2}T12:01:00Z", "M8", 80, session="s2", stop="end_turn"),
]

# Expected busy seconds (main, sub) per UTC day after each pass.
EXPECTED = [
    {D1: (1080.0, 120.0)},
    {D1: (1200.0, 120.0), D2: (120.0, 0.0)},
    {D1: (1200.0, 120.0), D2: (180.0, 0.0)},
]


# --- helpers -----------------------------------------------------------------

def _fake_rtk(tmp_path):
    """An `rtk` whose `gain` prints a fixed counter; both servers must read it."""
    d = tmp_path / "fakebin"
    d.mkdir()
    rtk = d / "rtk"
    rtk.write_text("#!/bin/sh\n"
                   'if [ "$1" = gain ]; then\n'
                   '  printf "Total commands: 7\\nTokens saved: 1.2K\\n"\n'
                   "else\n  exit 1\nfi\n")
    rtk.chmod(rtk.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return d


def _fd_env(bindir):
    """fd's scrubbed env plus the fake rtk; the idle-cap override is dropped so
    both sides run the default 15 min (Go has no config fallback)."""
    env = clean_env()
    env.pop("TOKEN_AUDIT_AGENT_IDLE_CAP_MIN", None)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    return env


def _python_client(tmp_path, monkeypatch, projects, db_path, bindir):
    """The app under the envpins hermetic env, pointed at the shared projects
    dir and its own ledger, with the fake rtk on PATH (rtk_savings_usd runs
    `rtk gain` in-process on each reingest)."""
    for key in envpins.CLEAR:
        monkeypatch.delenv(key, raising=False)
    for key, val in envpins.env_map(tmp_path).items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv("TOKEN_AUDIT_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("TOKEN_AUDIT_DB_PATH", str(db_path))
    monkeypatch.setenv("TOKEN_AUDIT_WATCH", "0")
    monkeypatch.setenv("TOKEN_AUDIT_REINGEST_INTERVAL", "0")
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "subscription_monthly_usd = 200.0\n"
        f'projects_dir = "{projects.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))
    from flightdeck import db
    monkeypatch.setattr(db, "_URL", None, raising=False)
    monkeypatch.setattr(db, "_POOL", None, raising=False)
    from flightdeck import server
    return TestClient(server.create_app())


def _get(url, path):
    with urllib.request.urlopen(url + path, timeout=5) as r:
        return json.loads(r.read())


def _assert_eq(go_val, py_val, path, tol=0.0):
    """Field-wise equality: floats within tol, everything else exact."""
    if isinstance(py_val, float) or isinstance(go_val, float):
        assert go_val == pytest.approx(py_val, abs=tol), (
            f"{path}: go={go_val} python={py_val}")
    else:
        assert go_val == py_val, f"{path}: go={go_val!r} python={py_val!r}"


def _cmp_row(go_row, py_row, path, float_tol):
    assert set(go_row) == set(py_row), (
        f"{path}: keys differ "
        f"go-only={sorted(set(go_row) - set(py_row))} "
        f"python-only={sorted(set(py_row) - set(go_row))}")
    for k in py_row:
        _assert_eq(go_row[k], py_row[k], f"{path}.{k}", float_tol)


def _cmp_daily(go_rows, py_rows, label, expected):
    go_by, py_by = ({r["date"]: r for r in rows} for rows in (go_rows, py_rows))
    assert set(go_by) == set(py_by) == set(expected), (
        f"{label}: days go={sorted(go_by)} python={sorted(py_by)}")
    for day, (main_s, sub_s) in expected.items():
        g, p = go_by[day], py_by[day]
        _cmp_row(g, p, f"{label} daily[{day}]", float_tol=1e-9)
        # Non-zero by construction, not just equal across servers.
        want_hours = (main_s + sub_s) / 3600
        for side, row in (("go", g), ("python", p)):
            assert row["agent_hours"] == pytest.approx(want_hours, abs=1e-9), (
                f"{label} {side} daily[{day}].agent_hours "
                f"{row['agent_hours']} != {want_hours}")
            assert row["agent_hours_sub"] == pytest.approx(sub_s / 3600,
                                                         abs=1e-9)
            assert row["agent_hours_est"] == 0.0


def _cmp_sessions(go_rows, py_rows, label):
    go_by = {r["session_id"]: r for r in go_rows}
    py_by = {r["session_id"]: r for r in py_rows}
    assert set(go_by) == set(py_by), (
        f"{label}: sessions go={sorted(go_by)} python={sorted(py_by)}")
    for sid in py_by:
        g, p = go_by[sid], py_by[sid]
        _cmp_row({k: v for k, v in g.items() if k != "subagents"},
                {k: v for k, v in p.items() if k != "subagents"},
                f"{label} sessions[{sid}]", float_tol=1e-9)
        g_subs = sorted(g["subagents"], key=lambda s: s["agent_id"])
        p_subs = sorted(p["subagents"], key=lambda s: s["agent_id"])
        assert [s["agent_id"] for s in g_subs] == \
               [s["agent_id"] for s in p_subs], \
               f"{label} sessions[{sid}].subagents"
        for i, (gs, ps) in enumerate(zip(g_subs, p_subs)):
            _cmp_row({k: v for k, v in gs.items() if k != "cost"},
                     {k: v for k, v in ps.items() if k != "cost"},
                     f"{label} sessions[{sid}].subagents[{i}]", float_tol=0)
            _assert_eq(gs["cost"], ps["cost"],
                       f"{label} sessions[{sid}].subagents[{i}].cost", 1e-6)


# --- the test ------------------------------------------------------------------

def test_go_and_python_serve_the_same_numbers(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    s1 = projects / "-p" / "s1.jsonl"
    agent = projects / "-p" / "s1" / "subagents" / "agent-a1.jsonl"
    s2 = projects / "-p" / "s2.jsonl"
    bindir = _fake_rtk(tmp_path)

    go_db = tmp_path / "ledger.db"
    env = _fd_env(bindir)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    client = _python_client(tmp_path / "app", monkeypatch, projects,
                            tmp_path / "audit.db", bindir)

    # Pass 1 lands before either server first reads the tree: `fd serve`
    # sweeps at startup and the app's lifespan runs one reingest.
    _write(s1, S1_PASS1)
    _write(agent, AGENT_A1)
    proc = subprocess.Popen(
        [str(FD), "serve", "--db", str(go_db), "--projects", str(projects),
         "--addr", f"127.0.0.1:{port}", "--subscription-usd", "200"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_health(url, proc)
        with client:
            def compare(label, expected):
                go_daily = _get(url, "/api/daily")
                _cmp_daily(go_daily, client.get("/api/daily").json(),
                           label, expected)
                _cmp_sessions(_get(url, "/api/sessions"),
                              client.get("/api/sessions").json(), label)
                # The verified numbers, for the run log.
                print(f"[parity {label}] " + ", ".join(
                    f"{r['date']} agent_hours={r['agent_hours']:.4f} "
                    f"sub={r['agent_hours_sub']:.4f}"
                    for r in go_daily))

            # Pass 1: both already ingested the prefix at startup.
            run_fd("ingest", "--db", str(go_db), "--projects", str(projects),
                   env=env)
            compare("pass-1 prefix", EXPECTED[0])
            # Pass 2: continue the open turn across midnight UTC.
            _write(s1, S1_PASS2, mode="a")
            run_fd("ingest", "--db", str(go_db), "--projects", str(projects),
                   env=env)
            r = client.post("/api/reingest")
            assert r.status_code == 200, r.text[:300]
            compare("pass-2 open turn across midnight", EXPECTED[1])
            # Pass 3: replays and a resumed session's copied history.
            _write(s1, S1_PASS3, mode="a")
            _write(s2, S2)
            run_fd("ingest", "--db", str(go_db), "--projects", str(projects),
                   env=env)
            r = client.post("/api/reingest")
            assert r.status_code == 200, r.text[:300]
            compare("pass-3 replayed/copied records", EXPECTED[2])

            # The fake rtk on PATH: both report the same non-zero savings,
            # measured on their own ingest ticks.
            deadline = time.time() + 15
            go_sum = _get(url, "/api/summary")
            while go_sum["rtk_savings"] == 0.0 and time.time() < deadline:
                time.sleep(0.2)
                go_sum = _get(url, "/api/summary")
            py_sum = client.get("/api/summary").json()
            assert go_sum["rtk_savings"] == pytest.approx(RTK_USD) \
                == py_sum["rtk_savings"], (
                    f"rtk_savings: go={go_sum['rtk_savings']} "
                    f"python={py_sum['rtk_savings']} want {RTK_USD}")
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
