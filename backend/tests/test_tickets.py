"""Tickets store: seed, the plan tree rollup, and the phase gates."""
import sqlite3

import pytest

from flightdeck.tickets import store


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    store.init(c)
    return c


def test_seed_fills_the_four_stages():
    c = _conn()
    data = store.list_tickets(c)
    assert len(data["tickets"]) == 15
    assert [p["key"] for p in data["phases"]] == ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    assert set(data["counts"]) == {"OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"}
    assert data["totals"]["blocked"] == 7
    # a ticket parked 8 days counts as stalled
    assert data["totals"]["stalled"] >= 2


def test_blocked_reason_carries_what_the_dropped_stages_meant():
    c = _conn()
    t = store.get_ticket(c, "CRM-11372")
    assert t["phase"] == "IN_PROGRESS"
    assert "spec owner" in t["blocked_reason"] and t["blocked_days"] == 5
    assert "pre-sale" in t["tags"]

    # clearing the reason clears its clock; setting one starts a new clock
    t = store.update_ticket(c, "CRM-11372", blocked_reason="")
    assert t["blocked_reason"] == "" and t["blocked_since"] == "" and t["blocked_days"] == 0
    t = store.update_ticket(c, "CRM-11372", blocked_reason="Blocked by another ticket")
    assert t["blocked_since"] and t["blocked_days"] == 0


def test_tags_round_trip_as_a_list():
    c = _conn()
    t = store.update_ticket(c, "CRM-12246", tags=["spec-review", "eshop"])
    assert t["tags"] == ["spec-review", "eshop"]
    assert "eshop" in store.list_tickets(c)["tags"]


def test_legacy_phases_fold_into_the_four_and_keep_their_meaning():
    c = _conn()
    store.update_ticket(c, "CRM-12246", title="legacy row")
    c.execute("UPDATE tickets SET phase='QUESTIONS_OUT', blocked_reason='' WHERE key='CRM-12246'")
    c.execute("UPDATE tickets SET phase='MERGED' WHERE key='CRM-12251'")
    store._migrate_phases(c)
    assert store.get_ticket(c, "CRM-12246")["phase"] == "IN_PROGRESS"
    assert "spec owner" in store.get_ticket(c, "CRM-12246")["blocked_reason"]
    assert store.get_ticket(c, "CRM-12251")["phase"] == "RESOLVED"


def test_plan_tree_is_two_levels_with_a_rollup():
    c = _conn()
    t = store.get_ticket(c, "CRM-12135")
    assert [m["code"] for m in t["plan"]] == ["M1", "M2", "M3", "M4", "M5", "M6", "M7"]
    m3 = next(m for m in t["plan"] if m["code"] == "M3")
    assert [k["kind"] for k in m3["children"]] == ["BUG", "AMEND", "RERUN"]
    assert t["rollup"] == {"planned_h": 32.0, "emergent_h": 3.5,
                           "milestones": 7, "children": 5}


def test_child_hours_never_change_the_milestone_estimate():
    c = _conn()
    m3 = next(m for m in store.plan_tree(c, "CRM-12135") if m["code"] == "M3")
    store.create_step(c, "CRM-12135", "Another surprise", parent_id=m3["id"],
                      kind="BUG", estimate_h=2)
    tree = store.plan_tree(c, "CRM-12135")
    again = next(m for m in tree if m["code"] == "M3")
    assert again["estimate_h"] == 7
    assert store.plan_rollup(tree)["emergent_h"] == 5.5


def test_estimate_hour_is_editable():
    c = _conn()
    m1 = store.plan_tree(c, "CRM-12135")[0]
    store.update_step(c, m1["id"], estimate_h=9.5)
    assert store.get_step(c, m1["id"])["estimate_h"] == 9.5


@pytest.mark.parametrize("key,lane,field,value", [
    ("CRM-12135", "IMPLEMENTATION", "mr_url", "https://gitlab.com/mr/1"),
    ("CRM-12246", "SPEC_REVIEW", "estimate", "~3d"),
])
def test_resolve_gate_asks_the_lane_for_its_proof(key, lane, field, value):
    c = _conn()
    store.update_ticket(c, key, lane=lane, estimate="", mr_url="")
    before = store.get_ticket(c, key)["phase"]
    t, reason = store.move_phase(c, key, "RESOLVED")
    assert reason and t["phase"] == before

    store.update_ticket(c, key, **{field: value})
    t, reason = store.move_phase(c, key, "RESOLVED")
    assert reason is None and t["phase"] == "RESOLVED"


def test_ungated_phase_moves_and_resets_the_clock():
    c = _conn()
    t, reason = store.move_phase(c, "CRM-12246", "IN_PROGRESS")
    assert reason is None
    assert t["phase"] == "IN_PROGRESS" and t["days_in_phase"] == 0


def test_deleting_a_milestone_takes_its_children():
    c = _conn()
    m3 = next(m for m in store.plan_tree(c, "CRM-12135") if m["code"] == "M3")
    kid = m3["children"][0]["id"]
    assert store.delete_step(c, m3["id"])
    assert store.get_step(c, kid) is None
    assert len(store.plan_tree(c, "CRM-12135")) == 6


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    cfg = tmp_path / "config.toml"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        'subscription_monthly_usd = 0.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{(tmp_path / "audit.db").as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))
    from flightdeck import server
    return TestClient(server.create_app())


def test_api_board_form_and_plan_edits(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        board = c.get("/api/tickets").json()
        assert board["totals"]["all"] == 15
        assert [p["key"] for p in board["phases"]] == ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
        assert board["block_reasons"] and "build-api" in board["tags"]

        t = c.get("/api/tickets/CRM-12135").json()
        assert t["rollup"]["planned_h"] == 32.0
        m3 = next(m for m in t["plan"] if m["code"] == "M3")
        assert "embed_token" in m3["body"]

        # the estimate hour is editable through the API the pane uses
        assert c.patch(f"/api/tickets/steps/{m3['id']}", json={"estimate_h": 8}).json()["estimate_h"] == 8

        # a child step is added under the milestone, and only moves emergent hours
        kid = c.post("/api/tickets/CRM-12135/steps",
                     json={"title": "Fix the frame height callback", "parent_id": m3["id"],
                           "kind": "BUG", "estimate_h": 1.5, "origin": "raised by you"}).json()
        after = c.get("/api/tickets/CRM-12135").json()
        assert after["rollup"]["emergent_h"] == 5.0
        assert after["rollup"]["planned_h"] == 33.0

        assert c.delete(f"/api/tickets/steps/{kid['id']}").json()["ok"] is True

        # the resolve gate answers with 409 and the reason, and the card stays put
        blocked = c.post("/api/tickets/CRM-12135/phase", json={"phase": "RESOLVED"})
        assert blocked.status_code == 409
        assert "merge request" in blocked.json()["detail"]
        assert c.get("/api/tickets/CRM-12135").json()["phase"] == "IN_PROGRESS"

        c.patch("/api/tickets/CRM-12135", json={"mr_url": "https://gitlab.com/mr/9"})
        assert c.post("/api/tickets/CRM-12135/phase", json={"phase": "RESOLVED"}).json()["phase"] == "RESOLVED"

        # blocking is a field, not a stage: the card stays where the work is
        blocked_now = c.patch("/api/tickets/CRM-12135",
                              json={"blocked_reason": "Waiting on QA to finish the test round"}).json()
        assert blocked_now["phase"] == "RESOLVED" and blocked_now["blocked_since"]


def test_api_404s(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.get("/api/tickets/NOPE-1").status_code == 404
        assert c.patch("/api/tickets/steps/s_nope", json={"title": "x"}).status_code == 404


def test_api_create_ticket(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        made = c.post("/api/tickets", json={
            "key": "crm-99999", "title": "A real one", "phase": "IN_PROGRESS",
            "tags": ["odoo-migration"], "blocked_reason": "Waiting on an environment or access",
        }).json()
        assert made["key"] == "CRM-99999" and made["tags"] == ["odoo-migration"]
        assert made["blocked_since"] and made["phase"] == "IN_PROGRESS"
        assert c.post("/api/tickets", json={"key": "CRM-99999", "title": "again"}).status_code == 409
        assert c.get("/api/tickets").json()["totals"]["all"] == 16
