"""Credentials in SQLite (secrets never leave the DB volume).

`data` (which may embed a "secret" field, e.g. an Odoo API key/password) is
stored as a single JSON blob and is only ever returned by get() -- list()
returns id/name/kind only, so listing credentials in the UI can never leak
a secret.
"""
import json
import uuid
from typing import List, Optional


def init(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hub_credentials "
        "(id TEXT PRIMARY KEY, name TEXT, kind TEXT, data TEXT)"
    )
    conn.commit()


def list(conn) -> List[dict]:
    """Secret-safe summaries: id/name/kind only -- never the data/secret blob."""
    rows = conn.execute(
        "SELECT id, name, kind FROM hub_credentials ORDER BY name"
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in rows]


def get(conn, cred_id: str) -> Optional[dict]:
    """Full record including the decoded data/secret blob, for the resolver only."""
    r = conn.execute(
        "SELECT id, name, kind, data FROM hub_credentials WHERE id=?", (cred_id,)
    ).fetchone()
    if not r:
        return None
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "data": json.loads(r["data"])}


def upsert(conn, cred: dict) -> dict:
    """Insert or update by id (assigning a new id if absent). Returns the secret-safe
    summary (id/name/kind) -- callers that need the data back should use get()."""
    if not cred.get("id"):
        cred["id"] = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO hub_credentials (id, name, kind, data) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
        "data=excluded.data",
        (cred["id"], cred["name"], cred["kind"], json.dumps(cred["data"])),
    )
    conn.commit()
    return {"id": cred["id"], "name": cred["name"], "kind": cred["kind"]}


def delete(conn, cred_id: str) -> None:
    conn.execute("DELETE FROM hub_credentials WHERE id=?", (cred_id,))
    conn.commit()
