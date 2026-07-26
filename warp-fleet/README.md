# warp-fleet

Drive a fleet of **Warp terminal panes** from an external orchestrator (Claude
Code in VS Code), exposed as a stdio **FastMCP** server — same shape as the
Missions MCP. Where Missions *tracks* agent sessions, warp-fleet **spawns** them:
each pane is a Warp terminal running an agent CLI (`claude`/`codex`) in WSL.

## Why this exists

Warp has **no inbound "run this command in a pane" API**. `warpctrl` / local
control is ~95% stubbed, off by default, and inert on Windows. But four
mechanisms compose into a working control channel — all confirmed empirically on
this machine (Windows 11 + WSL Ubuntu bash):

| Channel | How | Proven |
|---|---|---|
| **SPAWN** | write a launch-config YAML (`data/launch_configurations/*.yaml`), N panes each with `commands` | ✅ |
| **TRIGGER** | `warp.exe "warp://launch/<name>"` — the launcher forwards the URI over the `WarpStable_URI_CHANNEL` named pipe and exits | ✅ |
| **OBSERVE** | read `%LOCALAPPDATA%\warp\Warp\data\warp.sqlite` — `blocks` (stylized_output, exit_code, completed_ts) join `terminal_panes` on the session UUID; written live per block | ✅ |
| **INJECT** | inversion: each worker pane runs a dispatcher loop keyed by its own `$WARP_TERMINAL_SESSION_UUID`, polling a per-pane command file on a shared `/mnt/c` path | ✅ |

The linchpin: the `$WARP_TERMINAL_SESSION_UUID` a pane's shell sees **equals**
`blocks.pane_leaf_uuid` in the DB — so an in-pane script and the orchestrator
share one key. (Warp deliberately does not expose a pane's PTY or an inject API;
this routes around that by cooperation, not exploitation.)

## Layout

```
warp_fleet.py     core: WarpPaths, Task, Fleet (spawn/observe/inject) — pure stdlib
fleet_mcp.py      FastMCP server exposing fleet_* tools
smoke_inject.py   standalone proof of the inject path (opens a real Warp window)
```

## Two pane modes

- **task** — pane runs its `command` once; read the result block via `fleet_observe`.
  Best for fan-out: N one-shot `claude -p "<task>"` jobs, collect results.
- **worker** (default) — pane runs a dispatcher loop; drive it with `fleet_inject`,
  read `fleet_result`, end with `fleet_stop`. Best for reuse / multi-step control.

Injected lines and `command` are **bash** (the pane shell is WSL): e.g.
`claude -p "summarize ./src"`, `cd /mnt/c/Users/Admin/proj && codex exec "..."`.

## MCP tools

`fleet_spawn(tasks, mode?, split?)` · `fleet_observe(run_id?, since?)` ·
`fleet_inject(pane_uuid, command)` · `fleet_result(pane_uuid)` ·
`fleet_stop(pane_uuid)` · `fleet_focus_url(pane_uuid)` · `fleet_cleanup(run_id)`

`fleet_spawn` returns a `mapping` of task index → pane UUID; every other tool
takes that UUID.

## Register with Claude Code / Warp

Both read `.mcp.json`. Add (Windows python, cwd = this dir so `warp_fleet` imports):

```json
{
  "mcpServers": {
    "warp-fleet": {
      "command": "python",
      "args": ["C:\\Users\\Admin\\Documents\\flight-deck.sh\\warp-fleet\\fleet_mcp.py"]
    }
  }
}
```

Requires `pip install fastmcp` in that Python. The agent CLIs (`claude`, `codex`)
must be installed **inside WSL** (claude is at `/home/dev/.local/bin/claude`) and
authenticated there once (`claude` → `/login`, or set `ANTHROPIC_API_KEY`).

## Config file bus

`%USERPROFILE%\.warp-fleet\` (Windows) = `/mnt/c/Users/<you>/.warp-fleet/` (WSL).
Per pane: `<uuid>.cmd` (orchestrator → pane), `<uuid>.out` / `<uuid>.exit`
(pane → orchestrator).

## Channel

Defaults to the stable **Warp** channel (scheme `warp`). For a self-built OSS
binary, construct `Fleet(WarpPaths(channel="WarpOss", scheme="warposs"))`.
