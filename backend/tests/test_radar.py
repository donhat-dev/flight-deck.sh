"""The radar store keeps history, and refuses a position it cannot justify.

Three properties are worth a test each, and each of them is a design decision that a
future change could quietly undo:

  - a blip has NO stored ring. Its position is the ring of its newest move, so the
    drawing and the history can never disagree.
  - `state` (in / out / new / held) is the RELATION between two moves, not a field on
    one. A recorded direction would be wrong the moment an earlier move was fixed.
  - a move without a reason or without evidence is not representable. That refusal is
    the difference between a record and a wall decoration.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from flightdeck import db
from flightdeck.radar import seed as radar_seed
from flightdeck.radar import service, store


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr(db, "_URL", None)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    store.init(c)
    c.execute("INSERT INTO radar (slug, title) VALUES ('r', 'R')")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def add(conn, num, name="X", quadrant="platforms"):
    return store.add_blip(conn, radar="r", num=num, name=name, quadrant=quadrant)


EV = [{"kind": "note", "title": "because", "dated": "2026-08-01"}]


class _Counting:
    """Counts queries by wrapping the connection, since sqlite3's `execute` cannot be
    replaced on the instance."""

    def __init__(self, conn):
        self._c = conn
        self.n = 0

    def execute(self, sql, params=()):
        self.n += 1
        return self._c.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._c, name)


# ------------------------------------------------------ a move must justify itself

def test_a_move_without_a_reason_is_refused(conn):
    b = add(conn, 1)
    with pytest.raises(ValueError, match="needs a reason"):
        store.add_move(conn, blip=b["id"], ring="adopt", period="Q4", why="  ", evidence=EV)


def test_a_positioned_move_without_evidence_is_refused(conn):
    b = add(conn, 1)
    with pytest.raises(ValueError, match="at least one piece of evidence"):
        store.add_move(conn, blip=b["id"], ring="adopt", period="Q4", why="w", evidence=[])


def test_the_entry_move_is_the_one_exception(conn):
    # Entering the radar is not yet a decision, so there is nothing to cite. Demanding
    # evidence there would force a fake citation on every new blip.
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring=None, period="Q1", why="noticed it", evidence=[])
    assert len(store.moves_of(conn, b["id"])) == 1


def test_an_unknown_ring_is_refused(conn):
    b = add(conn, 1)
    with pytest.raises(ValueError, match="unknown ring"):
        store.add_move(conn, blip=b["id"], ring="maybe", period="Q4", why="w", evidence=EV)


def test_an_unknown_quadrant_is_refused(conn):
    with pytest.raises(ValueError, match="unknown quadrant"):
        store.add_blip(conn, radar="r", num=9, name="X", quadrant="vibes")


def test_blip_numbers_are_unique_per_radar(conn):
    add(conn, 1)
    with pytest.raises(sqlite3.IntegrityError):
        add(conn, 1)


# --------------------------------------------------------------- ring is derived

def test_a_blips_ring_is_its_newest_move(conn):
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring="assess", period="Q2", why="w", evidence=EV,
                   ts="2026-05-01T00:00:00+00:00")
    store.add_move(conn, blip=b["id"], ring="adopt", period="Q4", why="w", evidence=EV,
                   ts="2026-08-01T00:00:00+00:00")
    assert service.blip_view(conn, b)["ring"] == "adopt"


def test_there_is_no_ring_column_to_disagree_with(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(radar_blip)").fetchall()]
    assert "ring" not in cols, "a stored ring is a second truth that will drift"


def test_a_blip_with_no_moves_has_no_ring(conn):
    # Not an error and not a default: a blip nobody has placed has no position, and
    # inventing `assess` would claim a judgement that was never made.
    assert service.blip_view(conn, add(conn, 1))["ring"] is None


# ------------------------------------------------------------- state is a relation

@pytest.mark.parametrize("older,newer,expected", [
    ("assess", "adopt", "in"),
    ("caution", "trial", "in"),
    ("adopt", "trial", "out"),
    ("trial", "caution", "out"),
    ("trial", "trial", "held"),
])
def test_direction_is_read_from_two_moves(conn, older, newer, expected):
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring=older, period="Q2", why="w", evidence=EV,
                   ts="2026-05-01T00:00:00+00:00")
    store.add_move(conn, blip=b["id"], ring=newer, period="Q4", why="w", evidence=EV,
                   ts="2026-08-01T00:00:00+00:00")
    assert service.blip_view(conn, b)["state"] == expected


def test_the_first_placement_reads_as_new_not_as_a_move(conn):
    # It arrived; it did not travel. Comparing against the ring-less entry row would
    # otherwise report every first placement as an inward move.
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring=None, period="Q1", why="entered", evidence=[],
                   ts="2026-01-01T00:00:00+00:00")
    store.add_move(conn, blip=b["id"], ring="trial", period="Q2", why="w", evidence=EV,
                   ts="2026-05-01T00:00:00+00:00")
    v = service.blip_view(conn, b)
    assert (v["ring"], v["state"]) == ("trial", "new")


def test_an_entry_row_between_two_placements_is_skipped(conn):
    # `prev` is the previous POSITIONED move. If it were simply moves[1] then a
    # correction that inserted an entry row would flip the arrow on the drawing.
    b = add(conn, 1)
    for ts, ring in [("2026-01-01", "caution"), ("2026-02-01", None), ("2026-03-01", "adopt")]:
        store.add_move(conn, blip=b["id"], ring=ring, period="Q1", why="w",
                       evidence=EV if ring else [], ts=f"{ts}T00:00:00+00:00")
    assert service.blip_view(conn, b)["state"] == "in"


# ---------------------------------------------------------------- evidence and age

def test_evidence_age_comes_from_the_newest_piece(conn):
    from datetime import datetime, timezone
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring="adopt", period="Q4", why="w", ts="2026-08-01T00:00:00+00:00",
                   evidence=[{"kind": "note", "title": "old", "dated": "2026-01-01"},
                             {"kind": "note", "title": "new", "dated": "2026-08-01"}])
    v = service.blip_view(conn, b, today=datetime(2026, 8, 4, tzinfo=timezone.utc))
    # The freshest piece, not the average and not the oldest: one recent confirmation
    # is enough to say the position is still believed.
    assert v["evidenceAgeDays"] == 3
    assert v["evidenceCount"] == 2


def test_evidence_is_fetched_for_many_moves_in_one_query(conn):
    b = add(conn, 1)
    ids = []
    for i in range(4):
        m = store.add_move(conn, blip=b["id"], ring="adopt", period="Q4", why="w",
                           evidence=EV, ts=f"2026-0{i + 1}-01T00:00:00+00:00")
        ids.append(m["id"])
    # A PROXY, not a monkeypatch: `sqlite3.Connection.execute` is read-only, so
    # assigning over it raises rather than counting anything.
    counted = _Counting(conn)
    got = store.evidence_of(counted, ids)
    # One round trip for the whole history. Per-move queries turn one blip into a
    # dozen, and the panel always wants all of it.
    assert counted.n == 1
    assert sorted(got) == sorted(ids)


def test_asking_for_no_evidence_does_not_query_at_all(conn):
    counted = _Counting(conn)
    assert store.evidence_of(counted, []) == {}
    assert counted.n == 0


# ------------------------------------------------------------------- the whole board

def test_the_board_derives_ring_counts_and_staleness(conn):
    from datetime import datetime, timezone
    for num, ring, dated in [(1, "adopt", "2026-08-01"), (2, "adopt", "2026-08-01"),
                             (3, "caution", "2026-01-01")]:
        b = add(conn, num)
        store.add_move(conn, blip=b["id"], ring=ring, period="Q4", why="w",
                       ts="2026-08-01T00:00:00+00:00",
                       evidence=[{"kind": "note", "title": "e", "dated": dated}])
    board = service.radar_board(conn, "r", today=datetime(2026, 8, 4, tzinfo=timezone.utc))
    assert board["rings"] == {"adopt": 2, "trial": 0, "assess": 0, "caution": 1}
    assert board["blipCount"] == 3
    assert board["stale"] == 1


def test_periods_come_from_the_moves_not_from_a_list(conn):
    # A hand-kept period list shows quarters that never happened. Deriving them means
    # the scrubber's stops and the history are the same fact.
    for num, period, ts in [(1, "Q2 2026", "2026-05-01"), (2, "Q4 2026", "2026-08-01"),
                            (3, "Q4 2026", "2026-08-02")]:
        b = add(conn, num)
        store.add_move(conn, blip=b["id"], ring="adopt", period=period, why="w",
                       evidence=EV, ts=f"{ts}T00:00:00+00:00")
    periods = service.radar_board(conn, "r")["periods"]
    assert [(p["key"], p["moves"]) for p in periods] == [("Q2 2026", 1), ("Q4 2026", 2)]
    assert [p["key"] for p in periods if p["current"]] == ["Q4 2026"]


def test_a_blip_carries_the_period_of_its_newest_move(conn):
    # The index needs it to answer "moved this quarter". Inferring that from
    # `state != held` counted every blip that had ever moved, which after any history
    # enrichment is all of them — a facet reading 34 of 34 says nothing.
    b = add(conn, 1)
    store.add_move(conn, blip=b["id"], ring="trial", period="Q2 2026", why="w", evidence=EV,
                   ts="2026-05-01T00:00:00+00:00")
    store.add_move(conn, blip=b["id"], ring="adopt", period="Q4 2026", why="w", evidence=EV,
                   ts="2026-08-01T00:00:00+00:00")
    assert service.blip_view(conn, b)["period"] == "Q4 2026"


def test_an_unknown_radar_is_none_rather_than_an_empty_board(conn):
    # An empty board would render as a radar with nothing on it, which is a different
    # and legitimate state. Absence has to stay distinguishable from emptiness.
    assert service.radar_board(conn, "nope") is None


# ------------------------------------------------------------------------ the seeder

def test_the_seed_is_idempotent(conn):
    first = radar_seed.seed(conn)
    assert first["blips"] > 30
    again = radar_seed.seed(conn)
    assert again == {"radars": 0, "blips": 0, "moves": 0}
    assert len(store.blips_of(conn, "subscription-migration")) == first["blips"]


def test_the_seeded_board_matches_what_the_drawing_expects(conn):
    radar_seed.seed(conn)
    board = service.radar_board(conn, "subscription-migration")
    assert board["blipCount"] == 34
    assert sum(board["rings"].values()) == 34
    # Every blip must have a ring; one without would be unplaceable on the drawing.
    assert all(b["ring"] for b in board["blips"])
    assert all(b["why"] for b in board["blips"])


def test_enriching_history_is_additive_and_idempotent(conn):
    radar_seed.seed(conn)
    before = service.radar_board(conn, "subscription-migration")["moveCount"]
    first = radar_seed.enrich_history(conn)
    assert first["moves"] > 5
    # Idempotent, and — more importantly — ADDITIVE. Nothing is deleted: the board is
    # real data, and rewriting history so a drawing looks busier is the one thing
    # this feature exists to prevent.
    assert radar_seed.enrich_history(conn) == {"moves": 0}
    after = service.radar_board(conn, "subscription-migration")
    assert after["moveCount"] == before + first["moves"]


def test_a_prior_position_makes_the_direction_readable(conn):
    # Before the prior positions existed every blip read as "just entered", which is
    # correct and uniformly uninformative. The point of the extra move is that the
    # arrows start carrying information.
    radar_seed.seed(conn)
    radar_seed.enrich_history(conn)
    states = [b["state"] for b in service.radar_board(conn, "subscription-migration")["blips"]]
    assert states.count("in") >= 3
    assert states.count("out") >= 3


def test_the_headline_decision_keeps_its_whole_history(conn):
    radar_seed.seed(conn)
    d = service.blip_detail(conn, "subscription-migration", 5)
    assert d["name"] == "OCA subscription_oca"
    assert d["ring"] == "adopt"
    assert d["state"] == "in"
    assert [m["ring"] for m in d["moves"]] == ["adopt", "trial", "assess", None]
    assert all(m["why"] for m in d["moves"])
    assert len(d["moves"][0]["evidence"]) == 4


# ---------------------------------------------------------------------------- the API

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(tmp_path / "cfg.toml"))
    (tmp_path / "cfg.toml").write_text(f'db_path = "{tmp_path / "t.db"}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_WATCH", "0")
    from flightdeck import server
    app = server.create_app()
    with TestClient(app) as c:
        conn = db.open_write(app.state.cfg["db_path"])
        try:
            radar_seed.seed(conn)
        finally:
            conn.close()
        yield c


def test_the_api_returns_a_drawable_board(client):
    r = client.get("/api/radar/subscription-migration")
    assert r.status_code == 200
    board = r.json()
    assert board["blipCount"] == 34
    assert {b["state"] for b in board["blips"]} <= {"in", "out", "new", "held"}


def test_the_api_404s_on_an_unknown_radar(client):
    assert client.get("/api/radar/nope").status_code == 404


def test_moving_a_blip_needs_evidence(client):
    r = client.post("/api/radar/subscription-migration/blips/5/moves",
                    json={"ring": "trial", "period": "Q1 2027", "why": "changed my mind",
                          "evidence": []})
    # 422 from the schema, not 400 from the service: refusing at the boundary names
    # the field, which is what a form needs to point at.
    assert r.status_code == 422
    assert "evidence" in r.text


def test_moving_a_blip_records_the_move_and_re_derives_the_ring(client):
    r = client.post("/api/radar/subscription-migration/blips/5/moves",
                    json={"ring": "trial", "period": "Q1 2027",
                          "why": "the 1.0 upgrade broke the invoice hook",
                          "evidence": [{"kind": "trace", "title": "regression run",
                                        "dated": "2026-08-04"}]})
    assert r.status_code == 200, r.text
    assert r.json()["ring"] == "trial"
    assert r.json()["state"] == "out"
    # And the old position survives as history rather than being overwritten.
    rings = [m["ring"] for m in r.json()["moves"]]
    assert rings[:2] == ["trial", "adopt"]


def test_there_is_no_way_to_set_a_ring_without_a_move(client):
    # The absence of such an endpoint IS the feature. A PUT that set a position would
    # let the drawing say one thing and the history another.
    paths = [r.path for r in client.app.routes if getattr(r, "path", "").startswith("/api/radar")]
    assert not any("PUT" in getattr(r, "methods", set()) for r in client.app.routes
                   if getattr(r, "path", "").startswith("/api/radar")), paths
