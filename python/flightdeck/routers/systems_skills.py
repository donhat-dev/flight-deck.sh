"""Manuals — skill inventory + usage routes.

The HTTP surface only: the source scanners, frontmatter parser and usage join
live in `flightdeck.systems.skills` (no web imports there). All filesystem
access is read-only; missing dirs become warnings, never a 500.
"""
from fastapi import APIRouter, Request

from flightdeck.systems.skills import (
    _scan_personal, _scan_plugins, _scan_workspace, _since, _usage)

router = APIRouter(prefix="/api/systems", tags=["systems"])


@router.get("/skills")
def skills_overview(request: Request, range: str = "all"):
    warnings = []
    skills = _scan_personal(warnings) + _scan_plugins(warnings) + _scan_workspace(warnings)

    # De-duplicate by invocation name. Keep the first occurrence (source order:
    # personal, plugin, workspace) as canonical; flag the rest as duplicates.
    first_seen = {}
    for s in skills:
        nm = s["name"]
        if nm in first_seen:
            canon = first_seen[nm]
            s["duplicate"] = True
            # Name the canonical entry: its version for same-source plugin
            # dups (stale cache), else its source.
            if canon["source"] == s["source"] == "plugin" and canon["version"]:
                s["duplicate_of"] = f"{canon['source']} v{canon['version']}"
            else:
                s["duplicate_of"] = canon["source"]
        else:
            first_seen[nm] = s

    # Usage join (tolerates an absent/empty DB).
    db_path = request.app.state.cfg["db_path"]
    since = _since(range)
    usage = _usage(db_path, since)
    on_disk = set()
    for s in skills:
        on_disk.add(s["name"])
        u = usage.get(s["name"])
        s["calls"] = u["calls"] if u else 0
        s["sessions"] = u["sessions"] if u else 0
        s["last_used"] = u["last_used"] if u else None

    # Ghosts: invoked names with no matching skill on disk (deleted/renamed).
    ghosts = [
        {"name": nm, "calls": u["calls"], "sessions": u["sessions"],
         "last_used": u["last_used"]}
        for nm, u in usage.items() if nm not in on_disk
    ]
    ghosts.sort(key=lambda g: g["calls"], reverse=True)

    # Stable display order: source, then name.
    _src_rank = {"personal": 0, "plugin": 1, "workspace": 2}
    skills.sort(key=lambda s: (_src_rank.get(s["source"], 9), s["name"] or ""))

    by_source = {"personal": 0, "plugin": 0, "workspace": 0}
    for s in skills:
        by_source[s["source"]] = by_source.get(s["source"], 0) + 1

    summary = {
        "total": len(skills),
        "by_source": by_source,
        "broken": sum(1 for s in skills if s["broken"]),
        "duplicates": sum(1 for s in skills if s["duplicate"]),
        "never_used": sum(1 for s in skills if not s["calls"] and not s["broken"]),
        "ghosts": len(ghosts),
    }

    return {
        "range": range,
        "summary": summary,
        "skills": skills,
        "ghosts": ghosts,
        "warnings": warnings,
    }
