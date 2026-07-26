"""FastMCP server exposing the Warp fleet control channel to an orchestrator
agent (Claude Code in VS Code).

Same shape as flightdeck.mcp_server (the Missions MCP): a stdio FastMCP process
with snake_case tools. Where Missions tracks agent sessions, this SPAWNS them —
each fleet pane is a Warp terminal running an agent CLI (claude/codex) in WSL.
The two compose: a spawned pane's $WARP_TERMINAL_SESSION_UUID is the same
session key the Missions board can hold.

Run (stdio):  python -m fleet_mcp   (or: python fleet_mcp.py)

Tools:
  fleet_spawn(tasks, mode?, split?)  -> spawn N panes, return run_id + task->pane map
  fleet_observe(run_id?, since?)     -> completed blocks (exit_code, output) from the DB
  fleet_inject(pane_uuid, command)   -> run a command in a worker-mode pane
  fleet_result(pane_uuid)            -> read a worker pane's last output+exit
  fleet_stop(pane_uuid)              -> end a worker pane's dispatcher loop
  fleet_focus_url(pane_uuid)         -> warp://session/<uuid> to focus that pane
  fleet_cleanup(run_id)              -> delete the generated launch config
"""
from __future__ import annotations

import os
import sys
import time
import uuid as _uuid
from typing import List, Optional

# Make `warp_fleet` importable no matter what cwd the MCP host launches us from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP

from warp_fleet import Fleet, Task

mcp = FastMCP("warp-fleet")
_fleet = Fleet()
# run_id -> {"baseline": int, "mapping": {idx: uuid}, "mode": str}
_runs: dict[str, dict] = {}


def _new_run_id() -> str:
    return "fleet-" + _uuid.uuid4().hex[:8]


@mcp.tool()
def fleet_spawn(tasks: List[dict], mode: str = "worker", split: str = "horizontal") -> dict:
    """Spawn a Warp window with one pane per task and return the task->pane map.

    tasks: list of {"title": str, "command": str, "cwd": str(Windows path)}.
    mode: "worker" (default) makes each pane a dispatcher loop you drive with
      fleet_inject — best for orchestration and reuse. "task" runs each pane's
      command once and you read the result from fleet_observe.
    Each pane runs WSL bash, so `command`/injected lines are bash (e.g.
      'claude -p "summarize this repo"').
    Returns {run_id, baseline_block_id, mapping:{idx:pane_uuid}, tasks:[...]}.
    """
    run_id = _new_run_id()
    tlist = [
        Task(idx=i, title=t.get("title", f"task-{i}"),
             command=t.get("command", ""), cwd_win=t.get("cwd", r"C:\Users\Admin"),
             mode=mode)
        for i, t in enumerate(tasks)
    ]
    info = _fleet.spawn(run_id, tlist, split=split)
    mapping = _fleet.correlate(run_id, info["baseline_block_id"], len(tlist), timeout=30)
    _runs[run_id] = {"baseline": info["baseline_block_id"], "mapping": mapping, "mode": mode}
    return {
        "run_id": run_id,
        "baseline_block_id": info["baseline_block_id"],
        "mapping": {str(k): v for k, v in mapping.items()},
        "tasks": [{"idx": t.idx, "title": t.title, "pane_uuid": mapping.get(t.idx)}
                  for t in tlist],
        "unmapped": [t.idx for t in tlist if t.idx not in mapping],
    }


@mcp.tool()
def fleet_observe(run_id: Optional[str] = None, since: Optional[int] = None) -> dict:
    """Read completed command blocks from Warp's SQLite store (the OBSERVE channel).

    Pass run_id to scope from that run's baseline (recommended), or since=<block_id>
    for an explicit cursor. Returns non-marker blocks with pane_uuid, exit_code,
    completed flag, command and ANSI-stripped output. Poll this to watch agents work.
    """
    if since is None:
        since = _runs.get(run_id, {}).get("baseline", 0) if run_id else 0
    blocks = [b for b in _fleet.blocks_since(since)
              if not b["command"].startswith('echo "FLEET::')]
    return {"since": since, "count": len(blocks), "blocks": blocks}


@mcp.tool()
def fleet_inject(pane_uuid: str, command: str) -> dict:
    """Run a bash command in a worker-mode pane and wait briefly for its result.

    This is the INJECT channel (inversion): the command file is written to the
    shared bus; the pane's dispatcher loop runs it and captures output+exit.
    Returns {output, exit_code} if it finished within ~20s, else {pending:true}.
    """
    _fleet.inject(pane_uuid, command)
    for _ in range(40):
        res = _fleet.read_worker_result(pane_uuid)
        if res is not None:
            return {"pane_uuid": pane_uuid, **res}
        time.sleep(0.5)
    return {"pane_uuid": pane_uuid, "pending": True}


@mcp.tool()
def fleet_result(pane_uuid: str) -> dict:
    """Read a worker pane's most recent captured output+exit_code, if any."""
    res = _fleet.read_worker_result(pane_uuid)
    return res or {"pane_uuid": pane_uuid, "pending": True}


@mcp.tool()
def fleet_stop(pane_uuid: str) -> dict:
    """End a worker pane's dispatcher loop (returns the pane to a normal prompt)."""
    _fleet.stop_worker(pane_uuid)
    return {"pane_uuid": pane_uuid, "stopped": True}


@mcp.tool()
def fleet_focus_url(pane_uuid: str) -> dict:
    """Return warp://session/<uuid> — opening it focuses that pane in Warp."""
    return {"pane_uuid": pane_uuid,
            "focus_url": f"{_fleet.paths.scheme}://session/{pane_uuid.lower()}"}


@mcp.tool()
def fleet_cleanup(run_id: str) -> dict:
    """Delete the generated launch-config file for a run."""
    _fleet.cleanup(run_id)
    _runs.pop(run_id, None)
    return {"run_id": run_id, "cleaned": True}


if __name__ == "__main__":
    mcp.run()
