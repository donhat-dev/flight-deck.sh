"""Load config.toml, with optional environment-variable overrides.

Env overrides let a container (or CI) point at different paths without editing
the checked-in config.toml:
  TOKEN_AUDIT_PROJECTS_DIR    -> projects_dir
  TOKEN_AUDIT_DB_PATH         -> db_path
  TOKEN_AUDIT_SUBSCRIPTION_USD-> subscription_monthly_usd
  TOKEN_AUDIT_DATABASE_URL    -> database_url (None => SQLite at db_path)
"""
import os
import tomllib


def load(path: str = "config.toml") -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    cfg.setdefault("subscription_monthly_usd", 0.0)
    cfg.setdefault("projects_dir", "~/.claude/projects")
    cfg.setdefault("db_path", "audit.db")
    # When set (e.g. postgresql://…), db.py uses PostgreSQL instead of SQLite.
    # Left None for now — DB provisioning is out of scope; wire it here later.
    cfg.setdefault("database_url", None)

    # Environment overrides win over the file.
    if os.environ.get("TOKEN_AUDIT_PROJECTS_DIR"):
        cfg["projects_dir"] = os.environ["TOKEN_AUDIT_PROJECTS_DIR"]
    if os.environ.get("TOKEN_AUDIT_DB_PATH"):
        cfg["db_path"] = os.environ["TOKEN_AUDIT_DB_PATH"]
    if os.environ.get("TOKEN_AUDIT_SUBSCRIPTION_USD"):
        try:
            cfg["subscription_monthly_usd"] = float(os.environ["TOKEN_AUDIT_SUBSCRIPTION_USD"])
        except ValueError:
            pass
    if os.environ.get("TOKEN_AUDIT_DATABASE_URL"):
        cfg["database_url"] = os.environ["TOKEN_AUDIT_DATABASE_URL"]

    cfg["projects_dir"] = os.path.expanduser(cfg["projects_dir"])
    cfg["db_path"] = os.path.expanduser(cfg["db_path"])
    return cfg
