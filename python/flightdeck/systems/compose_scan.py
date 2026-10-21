"""Compose stack discovery — find compose files on disk (READ-ONLY).

The Hangar board can only see stacks that still have containers. A stack that
was brought fully down leaves nothing behind, so it vanishes from the board
entirely. This module supplies the other half: the compose files present in the
workspace, so a stack that is DOWN is still listed with the path that would
bring it back up.

Nothing here writes, executes, or shells out. It reads files and returns rows.

Two things this had to get right, both found by measuring the real workspace:

1. A compose FILE is not a STACK. `odoo12-ce-vanilla/` holds five compose files
   and `nakivo_local_config/` five more, but each directory is one or two real
   projects. The join key is Compose's own project name: the top-level `name:`
   key when the file declares one, else the sanitized directory basename — which
   is exactly what `com.docker.compose.project` carries on a running container,
   so the two sides join cleanly.

2. An unpruned walk is mostly noise. `node_modules`, vendored Odoo source trees
   and `orphans/` contribute compose files that are not this workspace's stacks.
"""
import os
import re

import yaml

# Directory names never descended into. Vendored source trees and dependency
# dirs carry compose files that belong to somebody else's project.
_PRUNE = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".worktrees",
    "OCB", "odoo12-ce-source", "odoo19-ce-source", "orphans",
    "dist", "build", ".next", ".cache", "site-packages",
}

# docker-compose.yml, docker-compose.override.yml, compose.yaml, …
_COMPOSE_RE = re.compile(r"^(docker-)?compose([.-][\w.-]+)?\.ya?ml$", re.I)

# How deep below a root to walk. The estate's stacks all sit within three levels
# (e.g. postgresql-core/postgres-17/docker-compose.yml); deeper hits are noise.
_MAX_DEPTH = 4


def _roots():
    """Workspace roots to scan. TOKEN_AUDIT_COMPOSE_ROOTS is a colon-separated
    list, following the env-override convention in config.py."""
    env = os.environ.get("TOKEN_AUDIT_COMPOSE_ROOTS")
    if env:
        raw = [p for p in env.split(":") if p.strip()]
    else:
        raw = ["~/Documents/Projects"]
    out = []
    for p in raw:
        full = os.path.abspath(os.path.expanduser(p.strip()))
        if os.path.isdir(full):
            out.append(full)
    return out


def _sanitize_project(name):
    """Compose's default project name from a directory basename: lowercased,
    with everything outside [a-z0-9_-] dropped."""
    return re.sub(r"[^a-z0-9_-]", "", (name or "").lower())


def _declared_name(path):
    """Top-level `name:` from a compose file, or None.

    Returns None on any parse failure — a malformed or templated file is a row
    that falls back to its directory name, never an exception that takes the
    whole endpoint down.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    name = doc.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _walk(root):
    """Compose file paths under root, pruned and depth-limited."""
    found = []
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - root_depth >= _MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _PRUNE and not d.startswith(".")]
        for fn in filenames:
            if _COMPOSE_RE.match(fn):
                found.append(os.path.join(dirpath, fn))
    return found


def _base_file(paths):
    """The file Compose would read with a bare `docker compose up` in that
    directory: docker-compose.yml / compose.yml (and their .yaml spellings)."""
    for cand in ("docker-compose.yml", "docker-compose.yaml",
                 "compose.yml", "compose.yaml"):
        for p in paths:
            if os.path.basename(p).lower() == cand:
                return p
    return None


def scan():
    """Compose stacks found on disk, keyed by project name.

    Resolution is per DIRECTORY, not per file, and that ordering is the whole
    point. A variant file (`docker-compose.debug.yml`, `.override.yml`) rarely
    repeats the `name:` key, so naming it from its own directory basename splits
    one stack into two: `nakivo_local_config/` declares `name: nakivo-local` in
    its base file, and its two undeclared variants would otherwise appear as a
    second stack called `nakivo_local_config` that no running container can ever
    join against. So each directory resolves a base name first, and undeclared
    files inherit it.

    Each row: {project, working_dir, config_files[], declared} where `declared`
    says the name came from a file's own `name:` key rather than the directory
    basename — the caller surfaces that, because a guessed name is the one that
    can fail to join against a running container.
    """
    by_dir = {}
    for root in _roots():
        for path in _walk(root):
            by_dir.setdefault(os.path.dirname(path), []).append(path)

    stacks = {}
    for wd, paths in by_dir.items():
        base = _base_file(paths)
        base_declared = _declared_name(base) if base else None
        base_name = base_declared or _sanitize_project(os.path.basename(wd))
        for path in sorted(paths):
            declared = _declared_name(path)
            project = declared or base_name
            if not project:
                continue
            row = stacks.setdefault(project, {
                "project": project,
                "working_dir": wd,
                "config_files": [],
                "declared": bool(declared) or (project == base_declared),
            })
            if path not in row["config_files"]:
                row["config_files"].append(path)
    for row in stacks.values():
        row["config_files"].sort()
    return stacks
