"""End-to-end: spawn a worker pane, run the REAL claude CLI inside it via inject,
read results back through the file bus. Proves orchestrator -> Warp pane -> agent."""
import sys
import time

from warp_fleet import Fleet, Task

RUN = "fleet-e2e-claude"
CWD = r"C:\Users\Admin"
CLAUDE = "/home/dev/.local/bin/claude"


def inject_wait(fleet, pane, cmd, timeout=120):
    fleet.inject(pane, cmd)
    for _ in range(int(timeout * 2)):
        res = fleet.read_worker_result(pane)
        if res is not None:
            return res
        time.sleep(0.5)
    return None


def main():
    fleet = Fleet()
    tasks = [Task(idx=0, title="claude-worker", command="", cwd_win=CWD, mode="worker")]
    info = fleet.spawn(RUN, tasks)
    print("spawned:", info["run_id"], "baseline", info["baseline_block_id"])
    mapping = fleet.correlate(RUN, info["baseline_block_id"], 1, timeout=30)
    if 0 not in mapping:
        print("FAIL correlate"); return 1
    pane = mapping[0]
    print("pane:", pane)

    print("\n[1] claude --version (no auth needed) ...")
    res = inject_wait(fleet, pane, f'"{CLAUDE}" --version', timeout=60)
    print("  exit:", res and res["exit_code"], "| out:", (res or {}).get("output", "").strip()[:200])

    print("\n[2] claude -p (real agent turn; reveals auth state) ...")
    res = inject_wait(fleet, pane,
                      f'"{CLAUDE}" -p "reply with exactly one word: PONG" 2>&1', timeout=120)
    out = (res or {}).get("output", "")
    print("  exit:", res and res["exit_code"])
    print("  out:", out.strip()[:500])
    authed = res and res["exit_code"] == 0 and "PONG" in out.upper()
    print("\nAGENT AUTHED + RESPONDING:", bool(authed))

    fleet.stop_worker(pane)
    print("\n(worker stopped; run_id", RUN, "- config left in place for inspection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
