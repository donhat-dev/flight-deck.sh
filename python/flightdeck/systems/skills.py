"""Manuals — skill inventory + usage.

Scans the three skill sources, parses SKILL.md frontmatter, flags broken
symlinks / duplicates, and joins with the ledger's tool_calls table
(tool = 'Skill', detail = invocation name) for real usage.

Sources
-------
1. Personal   — TOKEN_AUDIT_SKILLS_DIR (default ~/.claude/skills). Entries are
   dirs or relative symlinks (typically into ~/.agents/skills, mounted at
   /data/.agents in the container so the relative links still resolve). A skill
   is a dir/symlink whose (real) target holds a SKILL.md. Invocation name = the
   entry name.
2. Plugins    — TOKEN_AUDIT_PLUGINS_DIR (default ~/.claude/plugins). Glob
   cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md. Invocation
   name = "<plugin>:<name>".
3. Workspace  — FLIGHTDECK_WORKSPACE (same default as repodiff). Glob
   <ws>/agent/skills/*/SKILL.md and <ws>/*/agent/skills/*/SKILL.md only (the
   walk is bounded to those two shallow shapes — never a full-tree recurse).
   Invocation name = the entry name.

All filesystem access is read-only. Missing dirs become warnings, never a 500.
"""
import glob
import os
import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Request

from flightdeck import db

router = APIRouter(prefix="/api/systems", tags=["systems"])


# --- range cutoff (calendar-based, mirrors server._since, reimplemented local) -
def _since(range_key):
    today = date.today()
    if range_key == "today":
        return today.isoformat()
    if range_key == "7d":
        return (today - timedelta(days=6)).isoformat()
    if range_key == "30d":
        return (today - timedelta(days=29)).isoformat()
    return None  # "all" or unknown


# --- minimal SKILL.md YAML frontmatter parser --------------------------------
def _parse_frontmatter(md_path):
    """Return (name, description) from a SKILL.md's leading `---` block.

    Hand parser (no yaml dep): read only the top-level `name:` / `description:`
    keys inside the first fenced block. Tolerates single/double quotes and a
    folded value that overflows the first line. Raises OSError if unreadable so
    the caller can flag the skill broken."""
    name = None
    desc = None
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        first = f.readline()
        if first.strip() != "---":
            return name, desc  # no frontmatter; treat as empty meta
        cur_key = None          # key whose value may continue onto next lines
        for _ in range(200):    # bound the scan
            line = f.readline()
            if line == "" or line.strip() == "---":
                break
            # A new top-level key starts at column 0 as `key: value`.
            if line[:1] not in (" ", "\t") and ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key == "name":
                    name = val or None
                    cur_key = "name"
                elif key == "description":
                    desc = val or None
                    cur_key = "description"
                else:
                    cur_key = None
            elif cur_key == "description" and line.strip():
                # continuation of a folded/multi-line description
                desc = ((desc + " ") if desc else "") + line.strip().strip('"').strip("'")
    return name, desc


def _record(name, source, path, broken, warnings, version=None, plugin=None):
    """Build one skill record, parsing frontmatter unless already known broken."""
    desc = None
    if not broken:
        try:
            n, desc = _parse_frontmatter(path)
        except OSError as e:
            broken = True
            warnings.append(f"unreadable SKILL.md: {path} ({e.__class__.__name__})")
    return {
        "name": name,
        "source": source,
        "path": path,
        "description": desc,
        "broken": broken,
        "version": version,   # plugin version dir, else None
        "plugin": plugin,     # plugin name, else None
        "duplicate": False,
        "duplicate_of": None,
    }


# --- source scanners ---------------------------------------------------------
def _scan_personal(warnings):
    base = os.environ.get("TOKEN_AUDIT_SKILLS_DIR") or os.path.expanduser("~/.claude/skills")
    out = []
    if not os.path.isdir(base):
        warnings.append(f"personal skills dir missing: {base}")
        return out
    try:
        entries = sorted(os.listdir(base))
    except OSError as e:
        warnings.append(f"cannot list personal skills dir {base}: {e.__class__.__name__}")
        return out
    for entry in entries:
        if entry.startswith("."):
            continue
        p = os.path.join(base, entry)
        # Resolve symlinks (relative links into ~/.agents / /data/.agents).
        real = os.path.realpath(p)
        skill_md = os.path.join(real, "SKILL.md")
        # A skill is a dir (or symlink to one) that contains SKILL.md.
        if not (os.path.isdir(real) or os.path.islink(p)):
            continue
        broken = not os.path.isfile(skill_md)
        if broken:
            warnings.append(f"broken personal skill: {entry} -> {real} (no SKILL.md)")
        out.append(_record(entry, "personal", skill_md, broken, warnings))
    return out


def _scan_plugins(warnings):
    base = os.environ.get("TOKEN_AUDIT_PLUGINS_DIR") or os.path.expanduser("~/.claude/plugins")
    out = []
    cache = os.path.join(base, "cache")
    if not os.path.isdir(cache):
        warnings.append(f"plugins cache dir missing: {cache}")
        return out
    # cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md
    pattern = os.path.join(cache, "*", "*", "*", "skills", "*", "SKILL.md")
    rows = []
    for md in glob.glob(pattern):
        skills_dir = os.path.dirname(os.path.dirname(md))      # .../skills
        version = os.path.basename(os.path.dirname(skills_dir))  # <version>
        plugin = os.path.basename(os.path.dirname(os.path.dirname(skills_dir)))  # <plugin>
        skill_name = os.path.basename(os.path.dirname(md))     # <name>
        rows.append((plugin, skill_name, version, md))
    # Newest version first per (plugin, skill), so when the same version is
    # cached twice the newest is the canonical entry and stale caches flag as
    # duplicates. Falls back to string order for non-numeric version tags.
    def _vkey(v):
        parts = []
        for p in v.replace("-", ".").split("."):
            parts.append((0, int(p)) if p.isdigit() else (1, p))
        return parts
    rows.sort(key=lambda r: (r[0], r[1], _vkey(r[2])), reverse=True)
    for plugin, skill_name, version, md in rows:
        invocation = f"{plugin}:{skill_name}"
        broken = not os.path.isfile(md)  # glob matched, but be defensive
        out.append(_record(invocation, "plugin", md, broken, warnings,
                            version=version, plugin=plugin))
    return out


def _scan_workspace(warnings):
    try:
        from flightdeck import repodiff
        ws = repodiff.WORKSPACE
    except Exception:
        ws = os.path.realpath(
            os.environ.get("FLIGHTDECK_WORKSPACE")
            or os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    out = []
    if not os.path.isdir(ws):
        warnings.append(f"workspace dir missing: {ws}")
        return out
    seen = set()
    # Two shallow shapes only — never a full-tree recurse.
    patterns = [
        os.path.join(ws, "agent", "skills", "*", "SKILL.md"),
        os.path.join(ws, "*", "agent", "skills", "*", "SKILL.md"),
    ]
    for pat in patterns:
        for md in sorted(glob.glob(pat)):
            real = os.path.realpath(md)
            if real in seen:
                continue
            seen.add(real)
            name = os.path.basename(os.path.dirname(md))
            broken = not os.path.isfile(md)
            out.append(_record(name, "workspace", md, broken, warnings))
    return out


# --- usage join --------------------------------------------------------------
def _usage(db_path, since):
    """Map invocation name -> {calls, sessions, last_used} from tool_calls.

    Returns {} if the DB or table is absent/empty. Read-only, short-lived
    connection (same pattern as server._read_conn)."""
    usage = {}
    try:
        c = db.open_read(db_path)
    except sqlite3.Error:
        return usage
    try:
        sql = ("SELECT detail AS name, COUNT(*) AS calls, "
               "COUNT(DISTINCT session_id) AS sessions, MAX(ts) AS last_used "
               "FROM tool_calls WHERE tool = 'Skill' AND detail IS NOT NULL")
        params = []
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        sql += " GROUP BY detail"
        for r in c.execute(sql, params):
            usage[r["name"]] = {
                "calls": r["calls"],
                "sessions": r["sessions"],
                "last_used": r["last_used"],
            }
    except sqlite3.Error:
        # tool_calls not created yet, or transient — degrade to zero usage.
        return {}
    finally:
        c.close()
    return usage


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
