"""Copy the backend's turn labels into the Odoo module, once.

Run it under `odoo shell`:

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/import_task_labels.py

The backend keys a turn on the prompt id, as Odoo does. A label Odoo already
holds is kept, except that a person's label from the backend replaces one an
engine gave. Odoo has no merge kind: a merge comes in as a task, with its
backend kind kept in the label detail. Needs `GRANT SELECT ON turns TO
fdreader` on the backend database.
"""
import json
import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

LEDGER = dict(
    host=os.environ.get("FD_LEDGER_HOST", "pgcore-17"),
    port=int(os.environ.get("FD_LEDGER_PORT", "5432")),
    dbname=os.environ.get("FD_LEDGER_DB", "flightdeck"),
    user=os.environ.get("FD_LEDGER_USER", "fdreader"),
    password=os.environ["FD_LEDGER_PASSWORD"],
)
KIND = {"task": "task", "subtask": "subtask", "merge": "task",
        "automated": "no_work", "empty": "no_work"}


def parse_ts(value):
    if not value:
        return False
    stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


def run(env):
    src = psycopg2.connect(**LEDGER)
    src.set_session(readonly=True)
    cur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT turn_id, kind, parent_id, merged_ids, title, label_source,
                          label_confidence, label_detail, needs_review, labeled_at
                     FROM turns WHERE kind IS NOT NULL""")
    rows = cur.fetchall()
    Turn = env["flightdeck.turn"].with_context(fd_label_engine=True)
    held = {t.prompt_id: t for t in Turn.search([("prompt_id", "in", [r["turn_id"] for r in rows])])}
    done = skipped = 0
    for r in rows:
        turn = held.get(r["turn_id"])
        if turn is None:
            continue
        if turn.label_source and not (r["label_source"] == "human" and turn.label_source != "human"):
            skipped += 1
            continue
        try:
            detail = json.loads(r["label_detail"]) if r["label_detail"] else {}
        except ValueError:
            detail = {}
        probs = (detail or {}).get("jev") or {}
        top = max(probs, key=probs.get) if probs else None
        ranked = sorted(probs.values(), reverse=True)
        note = {"imported": True, "backend_kind": r["kind"]}
        if r["kind"] in ("automated", "empty"):
            note["reason"] = r["kind"]
        if r["merged_ids"]:
            note["merged_ids"] = json.loads(r["merged_ids"])
        parent = held.get(r["parent_id"]) if r["parent_id"] else None
        turn.write({
            "kind": KIND.get(r["kind"], "task"),
            "parent_id": parent.id if parent and r["kind"] == "subtask" else False,
            "title": r["title"] or False,
            "label_source": r["label_source"] if r["label_source"] in ("rule", "jev", "llm", "human") else "rule",
            "label_confidence": r["label_confidence"] or 0.0,
            "needs_review": bool(r["needs_review"]),
            "review_reason": "Imported from the backend's review queue" if r["needs_review"] else False,
            "labeled_at": parse_ts(r["labeled_at"]),
            "label_detail": note,
            "jev_kind": top or False,
            "jev_top1": ranked[0] if ranked else 0.0,
            "jev_margin": (ranked[0] - ranked[1]) if len(ranked) > 1 else (ranked[0] if ranked else 0.0),
            "jev_probs": {"kind": probs} if probs else False,
        })
        done += 1
    env.cr.commit()
    env["flightdeck.turn"]._rollup()
    env.cr.commit()
    print("labels: %s imported, %s kept as Odoo had them, %s backend turns not in Odoo"
          % (done, skipped, len(rows) - done - skipped))


run(env)  # noqa: F821 - odoo shell provides it
