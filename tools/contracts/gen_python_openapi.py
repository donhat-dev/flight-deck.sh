#!/usr/bin/env python3
"""Regenerate api/openapi/python.json from the FastAPI app.

Run with the venv interpreter from the worktree root:

    .venv/bin/python tools/contracts/gen_python_openapi.py [out.json]

Builds the app against a throwaway config/DB/projects dir (the same env the
tests use: no TOKEN_AUDIT_DATABASE_URL, engine state reset) and writes
app.openapi() with sorted keys so the checked-in file diffs meaningfully.
The app's lifespan never runs — `app.openapi()` is pure route introspection.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "api" / "conformance"))


def build_app():
    tmp = Path(tempfile.mkdtemp(prefix="fd-openapi-"))
    projects = tmp / "projects"
    projects.mkdir()
    # Pin every path override under tmp (config.load() gives them precedence
    # over the TOML) and clear the non-path ones — shared with the
    # conformance fixture so the two stay in sync.
    from envpins import apply_env
    apply_env(tmp)
    cfg = tmp / "config.toml"
    cfg.write_text(
        "subscription_monthly_usd = 0.0\n"
        f'projects_dir = "{projects.as_posix()}"\n'
        f'db_path = "{(tmp / "audit.db").as_posix()}"\n')
    os.environ["TOKEN_AUDIT_CONFIG"] = str(cfg)
    from flightdeck import db
    db._URL = None
    db._POOL = None
    from flightdeck.server import app
    return app


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "api" / "openapi" / "python.json"
    spec = build_app().openapi()
    out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
