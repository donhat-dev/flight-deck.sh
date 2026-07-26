"""Smoke test: spawn a 2-pane worker fleet, correlate, INJECT a command into
pane 0 via the file bus, and read the result back. Proves the inversion path.
Opens a real Warp window. Run with Windows python."""
import sys
import time

from warp_fleet import Fleet, Task

RUN = "fleet-smoke-inject"
CWD = r"C:\Users\Admin"


def main():
    fleet = Fleet()
    print("db:", fleet.paths.db_path, "exists:", fleet.paths.db_path.exists())
    print("warp.exe:", fleet.paths.warp_exe, "exists:", fleet.paths.warp_exe.exists())
    print("bus (win):", fleet.paths.bus_dir_win)
    print("bus (wsl):", fleet.paths.bus_dir_wsl())

    tasks = [
        Task(idx=0, title="worker-0", command="", cwd_win=CWD, mode="worker"),
        Task(idx=1, title="worker-1", command="", cwd_win=CWD, mode="worker"),
    ]
    info = fleet.spawn(RUN, tasks)
    print("spawned:", info)
    baseline = info["baseline_block_id"]

    print("correlating task->pane uuid ...")
    mapping = fleet.correlate(RUN, baseline, n_tasks=2, timeout=30)
    print("mapping:", mapping)
    if 0 not in mapping:
        print("FAIL: pane 0 not correlated"); return 1

    pane0 = mapping[0]
    print(f"injecting into pane0={pane0} ...")
    fleet.inject(pane0, 'echo "INJECTED_OK from $(hostname) at $(date -u +%T) pwd=$(pwd)"; whoami')

    print("waiting for worker result ...")
    for _ in range(40):
        res = fleet.read_worker_result(pane0)
        if res is not None:
            print("RESULT exit_code:", res["exit_code"])
            print("RESULT output:\n" + res["output"])
            ok = "INJECTED_OK" in res["output"] and res["exit_code"] == 0
            print("INJECT VERIFIED:", ok)
            fleet.stop_worker(pane0)
            fleet.stop_worker(mapping.get(1, pane0))
            return 0 if ok else 2
        time.sleep(0.5)
    print("FAIL: no worker result within timeout")
    return 3


if __name__ == "__main__":
    sys.exit(main())
