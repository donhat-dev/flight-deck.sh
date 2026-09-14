"""Spool connector: a shared directory, one file per command, one events file.

Layout under `runner.spool_dir`:

    cmd/    <seq>-<cmd_id>.json   written here, dot-prefixed, then renamed
    done/                         where the runner moves a handled command
    state/  <session_id>.json     the runner's view of each live process
    events.jsonl                  appended by the runner; read by byte offset

No network and no token: whoever can write into cmd/ drives the runner, and
the mount is what grants that.
"""
import json
import os

from .base import RunnerConnector, register


@register("spool")
class SpoolConnector(RunnerConnector):
    def _path(self, *parts):
        return os.path.join(self.runner.spool_dir, *parts)

    def submit(self, command):
        name = "%012d-%s.json" % (command["seq"], command["cmd_id"])
        cmd_dir = self._path("cmd")
        os.makedirs(cmd_dir, exist_ok=True)
        tmp = os.path.join(cmd_dir, "." + name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(command, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, os.path.join(cmd_dir, name))

    def read_events(self, offset):
        path = self._path("events.jsonl")
        if not os.path.exists(path):
            return [], offset
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        if size == offset:
            return [], offset
        with open(path, "rb") as f:
            f.seek(offset)
            buf = f.read()
        # Only whole lines: the runner may be mid-write on the last one.
        end = buf.rfind(b"\n")
        if end < 0:
            return [], offset
        events = []
        for raw in buf[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                events.append(json.loads(raw))
            except ValueError:
                continue
        return events, offset + end + 1

    def live_states(self):
        """What the runner says is running, from state/."""
        out = {}
        state_dir = self._path("state")
        if not os.path.isdir(state_dir):
            return out
        for name in os.listdir(state_dir):
            if name.startswith(".") or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(state_dir, name), encoding="utf-8") as f:
                    row = json.load(f)
                out[row["session_id"]] = row
            except (ValueError, OSError, KeyError):
                continue
        return out

    def status(self):
        if not self.runner.spool_dir:
            return "No spool directory set."
        if not os.path.isdir(self._path("cmd")):
            return "Spool directory has no cmd/ yet: the runner has not started there."
        return "%d command(s) waiting, %d process(es) running." % (
            len([n for n in os.listdir(self._path("cmd")) if not n.startswith(".")]),
            len(self.live_states()),
        )
