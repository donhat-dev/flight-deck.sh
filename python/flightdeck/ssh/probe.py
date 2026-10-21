"""Measuring what ControlMaster is worth on this particular route.

The design picked `ControlPersist=60s` from theory and said so: no host was reachable
from this machine when it was written, so the number had never been measured. This is
the measurement. It times the same trivial remote command two ways — once with no
socket to join, once through an established one — and reports both, so the choice can
be made from a number instead of an estimate.

Cold means cold on purpose: the existing master is dropped, and the timed run passes
`ControlMaster=no ControlPath=none` so it can neither join a socket nor leave one.
Median of several samples, because a single handshake carries whatever the network was
doing that second.
"""
import statistics

from flightdeck.ssh import master

PROBE_COMMAND = "true"


def run(cfg: dict, samples: int = 3) -> dict:
    samples = max(1, min(int(samples or 3), 10))

    shared, why = master.multiplexing()
    if not shared:
        return {"error": f"there is nothing to measure: {why}"}

    master.drop_master(cfg)
    cold = []
    for _ in range(samples):
        out = master.run(cfg, PROBE_COMMAND, timeout=30, control=False)
        if out["rc"] != 0:
            return {"error": "the cold connection failed: "
                             f"{(out['stderr'] or '').strip() or 'no detail'}"}
        cold.append(out["duration_ms"])

    opened = master.ensure_master(cfg)
    if opened.get("master") == "unavailable":
        return {"error": f"could not open a shared connection: {opened.get('detail')}"}

    warm = []
    for _ in range(samples):
        out = master.run(cfg, PROBE_COMMAND, timeout=30, control=True)
        if out["rc"] != 0:
            return {"error": "the shared connection failed: "
                             f"{(out['stderr'] or '').strip() or 'no detail'}"}
        warm.append(out["duration_ms"])

    cold_median = round(statistics.median(cold), 1)
    warm_median = round(statistics.median(warm), 1)
    return {
        "host": cfg["name"],
        "samples": samples,
        "command": PROBE_COMMAND,
        "cold_handshake_ms": cold,
        "cold_median_ms": cold_median,
        "shared_socket_ms": warm,
        "shared_median_ms": warm_median,
        "saved_ms": round(cold_median - warm_median, 1),
        "times_faster": round(cold_median / warm_median, 1) if warm_median else None,
        "control_persist": master.CONTROL_PERSIST,
        "socket": master.control_path(),
        # The measured saving is per call, so the honest reading of ControlPersist is
        # how many calls fall inside the window, not how long the window is.
        "reading": (f"a shared connection saves about "
                    f"{round(cold_median - warm_median, 1)}ms per call on this route; "
                    f"every call within {master.CONTROL_PERSIST} of the last one gets "
                    f"that saving"),
    }
