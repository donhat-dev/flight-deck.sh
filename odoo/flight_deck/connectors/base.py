"""How Odoo hands a command to a runner and reads what the runner reports.

The runner protocol is the same for every connector: a command is a dict with
`cmd_id`, `seq`, `op`, `session_id`, `args` and an optional `expires_at`; an
event is a dict with `kind` (ack, state, result), `session_id`, and the fields
that kind carries. Only the carrier differs. A new carrier is a subclass
registered under the name used by `flightdeck.runner.kind`.
"""

_REGISTRY = {}


def register(kind):
    def wrap(cls):
        _REGISTRY[kind] = cls
        return cls
    return wrap


def kinds():
    return sorted(_REGISTRY)


def get_connector(runner):
    try:
        cls = _REGISTRY[runner.kind]
    except KeyError:
        raise ValueError("no connector registered for %r" % runner.kind)
    return cls(runner)


class RunnerConnector:
    def __init__(self, runner):
        self.runner = runner

    def submit(self, command):
        """Hand one command to the runner. Returns nothing; the ack is an event."""
        raise NotImplementedError

    def read_events(self, offset):
        """Events the runner wrote after `offset`, and the new offset.

        `offset` is opaque to the caller: a byte position for a file, a cursor
        for anything else. The caller stores it and passes it back.
        """
        raise NotImplementedError

    def status(self):
        """One line a person can read about whether the runner is reachable."""
        return ""
