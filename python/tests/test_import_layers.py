"""ML-5 import ratchet: core modules must not depend on the web layer.

Walks every module under ``python/flightdeck`` with ``ast`` and fails when a
non-server module imports ``fastapi``, ``starlette``, ``flightdeck.routers``
or ``flightdeck.server``.

Server layer (allowed to import the web framework):

- ``flightdeck.routers/**`` — the endpoint modules.
- ``flightdeck.server`` — the ASGI entry point (uvicorn target).
- ``flightdeck.runtime`` — the FastAPI app factory + lifespan; ``server.py``
  delegates app construction to it, so it is the server layer split across a
  second file, not core code.

Every other entry point (``flightdeck.cli``, ``flightdeck.mcp_server``)
deliberately stays subject to the rule: they spawn subprocesses instead of
importing the app, which is what keeps a long-lived MCP session from running
stale code.

Today's violations live in ``import_layers_allowlist.txt`` next to this
file, one ``module -> import`` line each. The list may only shrink: a new
violation fails, and an allowlist line that no longer occurs fails too, so
the file must be edited down as modules move under ``flightdeck/routers``.
"""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "flightdeck"
ALLOWLIST = Path(__file__).with_name("import_layers_allowlist.txt")

SERVER_MODULE = "flightdeck.server"
SERVER_PKG = "flightdeck.routers"
ENTRY_POINTS = {"flightdeck.runtime"}  # app factory + lifespan, see docstring

BANNED_ROOTS = ("fastapi", "starlette", "flightdeck.routers", "flightdeck.server",
                "flightdeck.runtime")


def module_name(path):
    rel = path.relative_to(PKG.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_server(mod):
    return (
        mod == SERVER_MODULE
        or mod == SERVER_PKG
        or mod.startswith(SERVER_PKG + ".")
        or mod in ENTRY_POINTS
    )


def banned_root(name):
    for root in BANNED_ROOTS:
        if name == root or name.startswith(root + "."):
            return root
    return None


def imports_of(path, mod):
    """Banned roots this module imports, resolved for relative imports."""
    tree = ast.parse(path.read_text(), filename=str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                r = banned_root(a.name)
                if r:
                    out.add(r)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:  # from . / .. import -> absolute name
                pkg = mod.split(".") if path.name == "__init__.py" else mod.split(".")[:-1]
                base = ".".join(pkg[: len(pkg) - node.level + 1])
                name = f"{base}.{name}" if name else base
            # `from x import y` may name the submodule x.y: resolve the
            # aliases too, so `from flightdeck import server`,
            # `from . import server` and `from .routers import x` cannot
            # slip the server layer past the module check.
            for c in [name] + [f"{name}.{a.name}" for a in node.names]:
                r = banned_root(c)
                if r:
                    out.add(r)
    return out


def observed():
    pairs = set()
    for path in sorted(PKG.rglob("*.py")):
        mod = module_name(path)
        if is_server(mod):
            continue
        for root in imports_of(path, mod):
            pairs.add((mod, root))
    return pairs


def allowlisted():
    return {
        tuple(part.strip() for part in line.split("->", 1))
        for line in ALLOWLIST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_import_layers():
    have, want = observed(), allowlisted()
    new = sorted(have - want)
    stale = sorted(want - have)
    msg = []
    for mod, root in new:
        msg.append(f"new layer violation: {mod} imports {root} "
                   f"(add to {ALLOWLIST.name} only to ratchet it)")
    for mod, root in stale:
        msg.append(f"allowlist entry no longer occurs: {mod} -> {root} "
                   f"(remove the line; the list only shrinks)")
    assert not msg, "\n".join(msg)


def test_evasive_import_syntaxes_resolve(tmp_path):
    """The ratchet must see `from x import y` naming a submodule, in both
    absolute and relative spellings — each of these used to pass silently."""
    cases = {
        "import flightdeck.server": {"flightdeck.server"},
        "import flightdeck.runtime": {"flightdeck.runtime"},
        "from flightdeck import server": {"flightdeck.server"},
        "from flightdeck.runtime import lifespan": {"flightdeck.runtime"},
        "from flightdeck.routers import core": {"flightdeck.routers"},
        "from . import server": {"flightdeck.server"},
        "from .routers import core": {"flightdeck.routers"},
        "from .runtime import lifespan": {"flightdeck.runtime"},
    }
    for src, want in cases.items():
        f = tmp_path / "somecore.py"
        f.write_text(src + "\n")
        got = imports_of(f, "flightdeck.somecore")
        assert want <= got, f"{src!r}: got {got}, expected {want} in it"


def test_init_relative_import_resolves(tmp_path):
    f = tmp_path / "__init__.py"
    f.write_text("from . import server\n")
    assert imports_of(f, "flightdeck") == {"flightdeck.server"}
