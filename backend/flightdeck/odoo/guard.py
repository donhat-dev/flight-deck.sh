"""Three gates in front of every write, all of them closed by default.

    layer 0   the tool does not exist        odoo_unlink is absent from TOOLS   exit 3
    layer 1   per-instance allowlist         model not in write_models          exit 2
              (or model.method not in write_methods, for odoo_call)
    layer 2   explicit confirmation          confirm is not true                exit 2

All three run **before a packet leaves this machine**, which is what lets a refusal say
"nothing changed" and mean it. Past them, Odoo's own access rights and record rules are
still in force — this layer narrows what may be attempted, it does not widen anything.

Three things no configuration can switch on:

- **Deleting.** There is no `odoo_unlink`, and `odoo_call` refuses `method="unlink"`
  before it consults the allowlist, so listing `unlink` in `write_methods` does not
  help. Deletion cascades through `ondelete` rules the tool cannot see ahead of time,
  and one of the declared instances is a restore with no backup.
- **An empty allowlist meaning "anything".** Empty means absolutely read-only.
- **A missing allowlist meaning "anything".** An incomplete config reads as empty.

A refusal has to say what a second call with `confirm=true` would actually do. A refusal
that only says "refused" makes the retry a formality rather than a decision.
"""
from __future__ import annotations

# Refused before the allowlist is consulted, so no config file can reach these.
HARD_DENIED_METHODS = frozenset({"unlink"})


def _base(inst, extra) -> dict:
    """`error` first, because that is the key the CLI keys its exit code off and the
    first thing a person reads."""
    rest = dict(extra)
    out = {"error": rest.pop("error"), "layer": "guard", "instance": inst.name,
           "confirmed": False, **rest}
    if inst.note:
        out["reason"] = inst.note
    return out


def check_model(inst, model) -> dict | None:
    """`None` when this model may be written on this instance, a refusal otherwise."""
    if model in inst.write_models:
        return None
    return _base(inst, {
        "error": f"refused: writes to {model!r} are not allowed on instance "
                 f"{inst.name!r}",
        "model": model, "write_models": list(inst.write_models),
        "hint": f"add {model!r} to write_models for {inst.name!r} in the instance "
                f"registry, and only after checking who else uses that instance"})


def check_method(inst, model, method) -> dict | None:
    """The `odoo_call` gate: a hard denial first, then the `model.method` allowlist."""
    if method in HARD_DENIED_METHODS:
        return _base(inst, {
            "error": f"refused: {method!r} is denied outright and cannot be allowed by "
                     f"configuration",
            "model": model, "method": method,
            "hint": "deleting records is not on this surface at all; do it in the UI "
                    "or in odoo shell, with someone watching"})
    key = f"{model}.{method}"
    if key in inst.write_methods:
        return None
    return _base(inst, {
        "error": f"refused: method {key!r} is not allowed on instance {inst.name!r}",
        "model": model, "method": method,
        "write_methods": list(inst.write_methods),
        "hint": f"add {key!r} to write_methods for {inst.name!r}, after reading what "
                f"the method does and recording why in the entry's note"})


def check_confirm(inst, confirm, what, extra=None) -> dict | None:
    """The last gate. `what` must describe the change, not the refusal."""
    if confirm is True:
        return None
    return _base(inst, {
        "error": f"refused: pass confirm=true to {what}", **(extra or {})})
