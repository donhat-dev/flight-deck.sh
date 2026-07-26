"""warp_fleet — drive a fleet of Warp terminal panes from an external orchestrator.

This is the control layer proven out during research: Warp exposes no inbound
command API, but four mechanisms compose into a working channel, all confirmed
empirically on Windows + WSL:

  SPAWN    write a launch-config YAML: N panes, each running a command
  TRIGGER  warp.exe "warp://launch/<name>"  (the launcher forwards + exits)
  OBSERVE  read %LOCALAPPDATA%\\warp\\Warp\\data\\warp.sqlite (blocks + terminal_panes),
           written live per completed block (exit_code, output, timestamps)
  INJECT   inversion: each pane runs a dispatcher loop keyed by its own
           $WARP_TERMINAL_SESSION_UUID, polling a per-pane command file the
           orchestrator writes on a shared /mnt/c path.

The pane's shell here is WSL bash, so agent commands (claude/codex) run in WSL.
Pure stdlib — no pip install needed to run the core.

Paths assume the stable "Warp" channel by default; pass channel="WarpOss" for a
self-built OSS binary.
"""
from __future__ import annotations

import os
import re
import subprocess
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

# --- ANSI / OSC stripping for stylized_output --------------------------------
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[\]P][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[()][B0]")


def _clean(b: bytes | None) -> str:
    return _ANSI.sub(b"", b or b"").replace(b"\r\n", b"\n").decode("utf-8", "replace").strip()


# --- environment / path resolution -------------------------------------------
def _win_userprofile() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


@dataclass
class WarpPaths:
    """Resolves the on-disk locations the control channel touches."""
    channel: str = "Warp"          # "Warp" (stable) | "WarpOss" | "WarpPreview" ...
    scheme: str = "warp"           # matches channel: warp | warposs | warppreview
    roaming: Path = field(default_factory=lambda: Path(os.environ["APPDATA"]))
    local: Path = field(default_factory=lambda: Path(os.environ["LOCALAPPDATA"]))

    @property
    def launch_dir(self) -> Path:
        return self.roaming / "warp" / self.channel / "data" / "launch_configurations"

    @property
    def db_path(self) -> Path:
        return self.local / "warp" / self.channel / "data" / "warp.sqlite"

    @property
    def warp_exe(self) -> Path:
        return self.local / "Programs" / "Warp" / "warp.exe"

    # File bus lives under the Windows user profile, which WSL sees at /mnt/c/...
    @property
    def bus_dir_win(self) -> Path:
        return _win_userprofile() / ".warp-fleet"

    def bus_dir_wsl(self) -> str:
        # C:\Users\Admin\.warp-fleet -> /mnt/c/Users/Admin/.warp-fleet
        p = self.bus_dir_win
        drive = p.drive.rstrip(":").lower()
        rest = str(p)[len(p.drive):].replace("\\", "/")
        return f"/mnt/{drive}{rest}"


# --- fleet planning ----------------------------------------------------------
@dataclass
class Task:
    idx: int
    title: str
    command: str                   # bash command line run in the WSL pane
    cwd_win: str                   # Windows path; Warp maps C:\ -> /mnt/c/ in WSL
    mode: str = "task"             # "task" (run once) | "worker" (dispatcher loop)


def _yaml_escape(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _marker(run_id: str, idx: int) -> str:
    return f"FLEET::{run_id}::{idx}"


def _pane_commands(run_id: str, t: Task, bus: str) -> list[str]:
    """The bash command list a pane runs: a correlation marker first, then the
    task command (task mode) or a dispatcher loop (worker mode)."""
    mark_cmd = f'echo "{_marker(run_id, t.idx)} uuid=$WARP_TERMINAL_SESSION_UUID"'
    if t.mode == "worker":
        # Dispatcher loop: poll <uuid>.cmd, run it, capture to <uuid>.out/.exit.
        loop = (
            f'mkdir -p "{bus}"; U="$WARP_TERMINAL_SESSION_UUID"; '
            f'F="{bus}/$U.cmd"; '
            f'while true; do if [ -f "$F" ]; then '
            f'C="$(cat "$F")"; rm -f "$F"; '
            f'if [ "$C" = "__FLEET_STOP__" ]; then break; fi; '
            f'bash -lc "$C" > "{bus}/$U.out" 2>&1; echo $? > "{bus}/$U.exit"; '
            f'fi; sleep 0.5; done'
        )
        return [mark_cmd, loop]
    return [mark_cmd, t.command]


def _emit_leaf(t: Task, cmds: list[str], indent: int, as_list_item: bool) -> str:
    """Render one pane leaf (cwd + commands) at the given indent. When
    as_list_item, the first line carries a '- ' dash (for use under `panes:`)."""
    pad = " " * indent
    head = f"{pad}- cwd: {_yaml_escape(t.cwd_win)}" if as_list_item \
        else f"{pad}cwd: {_yaml_escape(t.cwd_win)}"
    field_pad = " " * (indent + 2) if as_list_item else pad
    lines = [head, f"{field_pad}commands:"]
    lines += [f"{field_pad}  - exec: {_yaml_escape(c)}" for c in cmds]
    return "\n".join(lines)


def render_launch_config(run_id: str, tasks: list[Task], paths: WarpPaths,
                         split: str = "horizontal") -> str:
    """Emit a Warp launch-config YAML. Each pane echoes a correlation marker
    (carrying its own session UUID) first, so the orchestrator can map
    task index -> pane_leaf_uuid by scanning blocks afterwards.

    Single task -> `layout:` is a leaf map. Multiple -> a split branch with a
    `panes:` list. (The launch-config schema is untagged: leaf = {cwd, commands},
    branch = {split_direction, panes}.)"""
    bus = paths.bus_dir_wsl()
    if len(tasks) == 1:
        t = tasks[0]
        leaf = _emit_leaf(t, _pane_commands(run_id, t, bus), indent=10, as_list_item=False)
        layout = "        layout:\n" + leaf
    else:
        pane_blocks = [
            _emit_leaf(t, _pane_commands(run_id, t, bus), indent=12, as_list_item=True)
            for t in tasks
        ]
        layout = (
            "        layout:\n"
            f"          split_direction: {split}\n"
            "          panes:\n" + "\n".join(pane_blocks)
        )

    return (
        "# warp-fleet launch config (generated). Safe to delete.\n"
        f"# run_id={run_id}\n"
        "---\n"
        f"name: {run_id}\n"
        "windows:\n"
        "  - tabs:\n"
        "      - title: fleet\n"
        f"{layout}\n"
    )


# --- the orchestrator --------------------------------------------------------
class Fleet:
    def __init__(self, paths: WarpPaths | None = None):
        self.paths = paths or WarpPaths()

    # OBSERVE -----------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        uri = "file:" + self.paths.db_path.as_posix() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5)

    def max_block_id(self) -> int:
        c = self._db()
        try:
            return c.execute("select coalesce(max(id),0) from blocks").fetchone()[0]
        finally:
            c.close()

    def blocks_since(self, since_id: int) -> list[dict]:
        c = self._db()
        try:
            rows = c.execute(
                "select b.id, lower(hex(b.pane_leaf_uuid)) pane, b.exit_code, "
                "b.did_execute, b.completed_ts, b.stylized_command, b.stylized_output, "
                "t.cwd "
                "from blocks b left join terminal_panes t "
                "  on lower(hex(t.uuid)) = lower(hex(b.pane_leaf_uuid)) "
                "where b.id > ? order by b.id",
                (since_id,),
            ).fetchall()
        finally:
            c.close()
        out = []
        for r in rows:
            out.append({
                "id": r[0], "pane": r[1], "exit_code": r[2],
                "did_execute": bool(r[3]), "completed": r[4] is not None,
                "completed_ts": r[4], "command": _clean(r[5]),
                "output": _clean(r[6]), "cwd": r[7],
            })
        return out

    # SPAWN + TRIGGER ---------------------------------------------------------
    def spawn(self, run_id: str, tasks: list[Task], split: str = "horizontal") -> dict:
        self.paths.launch_dir.mkdir(parents=True, exist_ok=True)
        self.paths.bus_dir_win.mkdir(parents=True, exist_ok=True)
        yaml = render_launch_config(run_id, tasks, self.paths, split=split)
        cfg = self.paths.launch_dir / f"{run_id}.yaml"
        cfg.write_text(yaml, encoding="utf-8")
        baseline = self.max_block_id()
        self._trigger(run_id)
        return {"run_id": run_id, "config": str(cfg), "baseline_block_id": baseline,
                "tasks": len(tasks)}

    def _trigger(self, run_id: str) -> None:
        url = f"{self.paths.scheme}://launch/{run_id}"
        subprocess.run([str(self.paths.warp_exe), url], check=False)

    # CORRELATE task index -> pane uuid via the markers -----------------------
    def correlate(self, run_id: str, since_id: int, n_tasks: int,
                  timeout: float = 30.0) -> dict[int, str]:
        pat = re.compile(rf"FLEET::{re.escape(run_id)}::(\d+) uuid=([0-9a-fA-F]+)")
        found: dict[int, str] = {}
        deadline = time.time() + timeout
        while time.time() < deadline and len(found) < n_tasks:
            for b in self.blocks_since(since_id):
                m = pat.search(b["command"]) or pat.search(b["output"])
                if m:
                    found[int(m.group(1))] = m.group(2).lower()
            if len(found) < n_tasks:
                time.sleep(0.5)
        return found

    # INJECT (worker-mode panes) ---------------------------------------------
    def inject(self, pane_uuid: str, command: str) -> None:
        # Clear any prior result FIRST so read_worker_result only returns once
        # the worker writes a fresh .exit (which it writes last, after .out).
        u = pane_uuid.lower()
        for ext in ("out", "exit"):
            p = self.paths.bus_dir_win / f"{u}.{ext}"
            if p.exists():
                p.unlink()
        (self.paths.bus_dir_win / f"{u}.cmd").write_text(command, encoding="utf-8")

    def read_worker_result(self, pane_uuid: str) -> dict | None:
        u = pane_uuid.lower()
        out = self.paths.bus_dir_win / f"{u}.out"
        exit_f = self.paths.bus_dir_win / f"{u}.exit"
        if not exit_f.exists():
            return None
        return {
            "output": out.read_text(encoding="utf-8", errors="replace") if out.exists() else "",
            "exit_code": int(exit_f.read_text().strip() or "-1"),
        }

    def stop_worker(self, pane_uuid: str) -> None:
        self.inject(pane_uuid, "__FLEET_STOP__")

    # WAIT for task-mode result blocks ---------------------------------------
    def wait_results(self, since_id: int, pane_uuids: list[str],
                     timeout: float = 300.0) -> dict[str, dict]:
        want = {u.lower() for u in pane_uuids}
        got: dict[str, dict] = {}
        deadline = time.time() + timeout
        while time.time() < deadline and set(got) < want:
            for b in self.blocks_since(since_id):
                if b["pane"] in want and b["completed"] and not b["command"].startswith("echo \"FLEET::"):
                    # keep the latest completed block per pane
                    got[b["pane"]] = b
            if set(got) < want:
                time.sleep(1.0)
        return got

    def cleanup(self, run_id: str) -> None:
        cfg = self.paths.launch_dir / f"{run_id}.yaml"
        if cfg.exists():
            cfg.unlink()
