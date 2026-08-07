"""The radar MCP surface, exercised through `handle()` the way an agent reaches it.

The tools are only worth as much as their refusals. A radar whose blips can be
positioned without a reason is a wall decoration, so every way of getting past that
rule gets a live negative test here — and each one also asserts that NOTHING was
written, because a guard that refuses and leaves debris behind has only moved the
problem.

Read paths get one test each. The interesting half is below `# --- refusals`.
"""
import json

import pytest

from flightdeck.radar import mcp_server, store


@pytest.fixture()
def wired(tmp_path):
    """Point the server at a scratch SQLite DB with one empty radar."""
    mcp_server.configure({"db_path": str(tmp_path / "radar.db"), "database_url": None})
    call("radar_create", {"slug": "r", "title": "Test radar"})
    return mcp_server


def call(name, args):
    resp = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}})
    return json.loads(resp["result"]["content"][0]["text"])


EV = [{"kind": "note", "title": "because", "dated": "2026-08-01"}]


def entered(name="Thing", quadrant="platforms", **over):
    """A blip on the radar with no position yet — the one evidence-free move."""
    args = {"slug": "r", "name": name, "quadrant": quadrant, "why": "worth watching",
            "period": "Q3 2026", "ring": None}
    return call("radar_blip_add", {**args, **over})


def placed(name="Thing", ring="assess", **over):
    return entered(name=name, ring=ring, evidence=EV, **over)


# --------------------------------------------------------------- the surface

def test_every_tool_is_listed_with_a_schema(wired):
    resp = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert len(tools) == len(mcp_server.TOOLS)
    # Anti-vacuity: a registry that lost its entries would satisfy the line above.
    assert len(tools) >= 15
    for t in tools:
        assert t["description"].strip(), t["name"]
        assert t["inputSchema"]["type"] == "object"
        # Every declared required field must exist in properties, or the agent is told
        # to pass something the schema does not describe.
        for r in t["inputSchema"]["required"]:
            assert r in t["inputSchema"]["properties"], (t["name"], r)


def test_the_write_verbs_are_all_reachable(wired):
    """The gap this server exists to close: creating a radar and adding a blip were
    reachable only from seed.py, and correcting a record was not reachable at all."""
    assert {"radar_create", "radar_blip_add", "radar_blip_update", "radar_blip_delete",
            "radar_reindex", "radar_move", "radar_move_update", "radar_move_delete",
            "radar_evidence_add", "radar_evidence_delete", "radar_update",
            "radar_delete"} <= set(mcp_server.TOOLS)


def test_an_unknown_tool_is_data_not_a_crash(wired):
    assert "unknown tool" in call("radar_nope", {})["error"]


def test_a_thrown_error_comes_back_as_data(wired):
    # The transport must survive any tool failing, or one bad call ends the session.
    out = call("radar_get", {"slug": "nope"})
    assert "LookupError" in out["error"]


# --------------------------------------------------------------- reads

def test_a_new_radar_is_empty_but_real(wired):
    board = call("radar_get", {"slug": "r"})
    assert board["title"] == "Test radar"
    assert board["blipCount"] == 0 and board["periods"] == []


def test_a_write_answers_with_the_derived_position(wired):
    out = placed(ring="trial")
    # Not the row that was written: the ring and the direction as the server derives
    # them, so the caller never has to compute or assume either.
    assert out["ring"] == "trial" and out["state"] == "new"
    assert out["moveCount"] == 1 and out["why"] == "worth watching"


def test_a_move_is_recorded_with_its_direction(wired):
    placed(ring="assess")
    out = call("radar_move", {"slug": "r", "num": 1, "ring": "adopt",
                              "period": "Q4 2026", "why": "the migration landed",
                              "evidence": EV})
    assert out["ring"] == "adopt"
    assert out["state"] == "in"          # assess -> adopt travels toward the centre
    assert out["moveCount"] == 2
    assert out["moves"][0]["why"] == "the migration landed"


def test_a_move_can_be_traced_to_the_session_that_made_it(wired):
    placed()
    out = call("radar_move", {"slug": "r", "num": 1, "ring": "adopt", "period": "Q4 2026",
                              "why": "held with fresh evidence", "evidence": EV,
                              "session_id": "5d924437"})
    assert out["moves"][0]["session_id"] == "5d924437"


def test_the_next_number_is_taken_without_being_asked_for(wired):
    assert entered(name="One")["num"] == 1
    assert entered(name="Two")["num"] == 2


# --------------------------------------------------------------- labels vs history

def test_labels_are_editable_and_are_not_history(wired):
    placed(name="Old name", ring="trial")
    out = call("radar_blip_update", {"slug": "r", "num": 1, "name": "New name",
                                     "quadrant": "tools"})
    assert out["name"] == "New name" and out["quadrant"] == "tools"
    # The label changed and the position did not: they are different kinds of fact.
    assert out["ring"] == "trial" and out["moveCount"] == 1


def test_there_is_no_tool_that_sets_a_ring_without_a_move(wired):
    """The rule the whole feature rests on, checked at the surface an agent sees."""
    for name, (_fn, _desc, props, _req) in mcp_server.TOOLS.items():
        if name in ("radar_move", "radar_move_update", "radar_blip_add"):
            continue          # these three take a ring, and all three demand a reason
        assert "ring" not in props, f"{name} can set a ring without recording why"


def test_a_blip_can_be_renumbered(wired):
    placed(name="A")
    placed(name="B")
    assert call("radar_blip_update", {"slug": "r", "num": 2, "new_num": 7})["num"] == 7


def test_reindex_closes_the_gap_a_delete_leaves(wired):
    for n in ("A", "B", "C"):
        placed(name=n)
    call("radar_blip_delete", {"slug": "r", "num": 2, "confirm": True})
    assert [b["num"] for b in call("radar_get", {"slug": "r"})["blips"]] == [1, 3]

    out = call("radar_reindex", {"slug": "r"})
    assert [b["num"] for b in out["board"]["blips"]] == [1, 2]
    assert out["changed"] == 1
    assert out["moved"] == [{"name": "C", "from": 3, "to": 2}]


def test_reindex_survives_a_shift_that_collides_at_every_step(wired):
    """The two-phase pass earns its keep here.

    Renumbering 2,3,4 -> 1,2,3 one row at a time hits the unique index on the FIRST
    update, because 3 is still held by the blip that has not moved yet. A naive
    implementation fails mid-way with half the radar already renumbered.
    """
    for n, num in (("A", 2), ("B", 3), ("C", 4)):
        placed(name=n, num=num)
    out = call("radar_reindex", {"slug": "r"})
    assert "error" not in out
    assert [(b["num"], b["name"]) for b in out["board"]["blips"]] == [
        (1, "A"), (2, "B"), (3, "C")]


def test_reindex_can_number_in_drawing_order(wired):
    placed(name="Tool", quadrant="tools")
    placed(name="Platform", quadrant="platforms")
    out = call("radar_reindex", {"slug": "r", "by": "quadrant"})
    # platforms is quadrant 0, tools is quadrant 2, so the numbers now scan the same
    # way the circle does rather than in insertion order.
    assert [(b["num"], b["name"]) for b in out["board"]["blips"]] == [
        (1, "Platform"), (2, "Tool")]


def test_a_subtitle_can_be_cleared_and_omission_leaves_it_alone(wired):
    call("radar_update", {"slug": "r", "subtitle": "Odoo 12 EE -> 19 CE"})
    assert call("radar_get", {"slug": "r"})["subtitle"] == "Odoo 12 EE -> 19 CE"
    # Omitted: untouched. This is the half a None sentinel would get right.
    assert call("radar_update", {"slug": "r", "title": "Renamed"})["subtitle"] \
        == "Odoo 12 EE -> 19 CE"
    # Explicit null: cleared. This is the half a None sentinel would silently drop.
    assert call("radar_update", {"slug": "r", "subtitle": None})["subtitle"] is None


def test_a_positioned_move_can_be_set_back_to_an_entry(wired):
    """`ring: null` has to survive the argument plumbing, or a mis-recorded position
    could never be walked back to 'on the radar, not yet placed'."""
    placed(ring="adopt")
    move_id = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    out = call("radar_move_update", {"slug": "r", "num": 1, "move_id": move_id,
                                     "ring": None})
    assert out["ring"] is None and out["state"] == "new"


# --------------------------------------------------------------- refusals

def test_a_blip_cannot_be_added_without_a_reason(wired):
    out = call("radar_blip_add", {"slug": "r", "name": "Nameless reason", "period": "Q3",
                                  "quadrant": "tools", "why": "   "})
    assert "reason" in out["error"]
    # And no debris: the compensating delete has to have run, or the radar now holds a
    # blip with no move — the state add_blip exists to make unreachable.
    assert call("radar_get", {"slug": "r"})["blipCount"] == 0


def test_a_positioned_blip_can_be_added_without_evidence(wired):
    """Reversed by decision: evidence is a recommendation, not a gate. A citation typed to
    get past a check supports nothing, and an uncited move that admits it is easier to
    fix later than a decorative reference nobody reads."""
    out = call("radar_blip_add", {"slug": "r", "name": "Uncited", "quadrant": "tools",
                                 "why": "worth a look", "period": "Q3", "ring": "adopt"})
    assert out["ring"] == "adopt" and out["evidenceCount"] == 0
    assert call("radar_get", {"slug": "r"})["blipCount"] == 1


def test_a_move_can_be_recorded_without_evidence(wired):
    placed()
    out = call("radar_move", {"slug": "r", "num": 1, "ring": "adopt",
                              "period": "Q4 2026", "why": "the probe passed"})
    assert out["ring"] == "adopt" and out["moveCount"] == 2


def test_evidence_is_no_longer_a_required_argument(wired):
    """The schema is the only thing an agent reads before calling. If it still listed
    evidence as required, the tool would accept what the schema forbade."""
    assert "evidence" not in mcp_server.TOOLS["radar_move"][3]
    assert "OPTIONAL" in mcp_server.TOOLS["radar_move"][2]["evidence"]["description"]
    # And the schema says WHEN to offer it, since "optional" alone reads as "skip it".
    desc = mcp_server.TOOLS["radar_move"][2]["evidence"]["description"]
    assert "CHANGES ring" in desc and "Adopt" in desc


def test_a_move_still_cannot_be_recorded_without_a_reason(wired):
    # The half that did not move.
    placed()
    out = call("radar_move", {"slug": "r", "num": 1, "ring": "adopt",
                              "period": "Q4 2026", "why": "   ", "evidence": EV})
    assert "reason" in out["error"]
    assert call("radar_blip", {"slug": "r", "num": 1})["moveCount"] == 1


def test_an_entry_move_can_be_promoted_to_a_ring(wired):
    """This check existed to close one door: promoting an evidence-free entry move to a
    real ring. With evidence optional there is no door there, so the promotion is now a
    plain correction — and the reason still has to be there."""
    entered()
    move_id = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    out = call("radar_move_update", {"slug": "r", "num": 1, "move_id": move_id,
                                     "ring": "adopt"})
    assert out["ring"] == "adopt"
    assert call("radar_move_update", {"slug": "r", "num": 1, "move_id": move_id,
                                      "why": ""})["error"].count("reason")


def test_a_reason_cannot_be_edited_away(wired):
    placed()
    move_id = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    out = call("radar_move_update", {"slug": "r", "num": 1, "move_id": move_id,
                                     "why": ""})
    assert "reason" in out["error"]


def test_a_blips_last_move_cannot_be_deleted(wired):
    placed()
    move_id = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    out = call("radar_move_delete", {"slug": "r", "num": 1, "move_id": move_id,
                                     "confirm": True})
    assert "only move" in out["error"] and "Delete the blip instead" in out["error"]
    assert call("radar_blip", {"slug": "r", "num": 1})["moveCount"] == 1


def test_an_earlier_move_can_be_deleted_and_the_position_re_derives(wired):
    placed(ring="assess")
    call("radar_move", {"slug": "r", "num": 1, "ring": "adopt", "period": "Q4 2026",
                        "why": "landed", "evidence": EV})
    oldest = call("radar_blip", {"slug": "r", "num": 1})["moves"][-1]["id"]
    out = call("radar_move_delete", {"slug": "r", "num": 1, "move_id": oldest,
                                     "confirm": True})
    assert out["blip"]["moveCount"] == 1
    # Ring unchanged (it came from the newest move) but the direction did change: there
    # is no earlier positioned move left to have travelled from.
    assert out["blip"]["ring"] == "adopt" and out["blip"]["state"] == "new"


def test_the_last_evidence_can_be_removed_and_the_answer_says_so(wired):
    """No refusal left, because the uncited state is reachable from add_move anyway —
    keeping it here would only have made removing a WRONG citation harder than never
    adding one. `remaining` is how a caller learns what it just did."""
    placed(ring="trial")
    ev_id = call("radar_blip", {"slug": "r", "num": 1})["evidence"][0]["id"]
    out = call("radar_evidence_delete", {"slug": "r", "num": 1, "evidence_id": ev_id})
    assert out["remaining"] == 0
    assert call("radar_blip", {"slug": "r", "num": 1})["evidenceCount"] == 0
    assert call("radar_blip", {"slug": "r", "num": 1})["ring"] == "trial"


def test_evidence_can_be_removed_once_something_replaces_it(wired):
    placed(ring="trial")
    move_id = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    call("radar_evidence_add", {"slug": "r", "num": 1, "move_id": move_id,
                                "evidence": [{"kind": "jira", "title": "CRM-11197"}]})
    ev_id = call("radar_blip", {"slug": "r", "num": 1})["evidence"][0]["id"]
    call("radar_evidence_delete", {"slug": "r", "num": 1, "evidence_id": ev_id})
    assert len(call("radar_blip", {"slug": "r", "num": 1})["evidence"]) == 1


@pytest.mark.parametrize("tool, args, still_there", [
    ("radar_delete", {"slug": "r"}, ("radar_get", {"slug": "r"})),
    ("radar_blip_delete", {"slug": "r", "num": 1}, ("radar_blip", {"slug": "r", "num": 1})),
])
def test_nothing_is_deleted_without_confirm(wired, tool, args, still_there):
    placed()
    out = call(tool, args)
    assert out["confirmed"] is False and "confirm=true" in out["error"]
    # The refusal must not have deleted anything on the way to reporting the cost.
    assert "error" not in call(*still_there)


def test_the_refusal_says_how_much_would_go(wired):
    placed()
    call("radar_move", {"slug": "r", "num": 1, "ring": "adopt", "period": "Q4",
                        "why": "landed", "evidence": EV})
    assert "1 blips and 2 moves" in call("radar_delete", {"slug": "r"})["error"]


def test_a_taken_number_is_refused_and_names_the_occupant(wired):
    placed(name="Incumbent")
    placed(name="Challenger")
    out = call("radar_blip_update", {"slug": "r", "num": 2, "new_num": 1})
    assert "already blip 'Incumbent'" in out["error"]
    assert call("radar_blip", {"slug": "r", "num": 2})["name"] == "Challenger"


def test_a_blip_cannot_be_added_to_a_radar_that_does_not_exist(wired):
    out = call("radar_blip_add", {"slug": "ghost", "name": "X", "quadrant": "tools",
                                 "why": "w", "period": "Q3"})
    assert "no radar" in out["error"]


def test_creating_a_radar_that_exists_is_refused_not_an_overwrite(wired):
    call("radar_update", {"slug": "r", "title": "Do not lose me"})
    out = call("radar_create", {"slug": "r", "title": "Clobber"})
    assert "already exists" in out["error"]
    assert call("radar_get", {"slug": "r"})["title"] == "Do not lose me"


def test_a_move_cannot_be_edited_through_the_wrong_blip(wired):
    """Move ids are opaque and global. Without an ownership check the edit would land
    on another blip while the response — built from slug/num — showed the one named,
    untouched, so the call would look like it did nothing."""
    placed(name="A")           # num 1
    placed(name="B")           # num 2
    move_of_a = call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["id"]
    out = call("radar_move_update", {"slug": "r", "num": 2, "move_id": move_of_a,
                                     "why": "wrong target"})
    assert "does not belong to blip 2" in out["error"]
    assert call("radar_blip", {"slug": "r", "num": 1})["moves"][0]["why"] \
        == "worth watching"


def test_an_unknown_ring_or_quadrant_is_refused(wired):
    assert "unknown quadrant" in entered(quadrant="middle")["error"]
    placed()
    assert "unknown ring" in call("radar_move", {
        "slug": "r", "num": 1, "ring": "maybe", "period": "Q4", "why": "w",
        "evidence": EV})["error"]


# --------------------------------------------------------------- cascade + plumbing

def test_deleting_a_radar_takes_its_whole_tree(wired):
    placed(name="A")
    placed(name="B")
    out = call("radar_delete", {"slug": "r", "confirm": True})
    assert out == {"radar": "r", "blips": 2, "moves": 2, "evidence": 2}
    # Written-out cascade, because SQLite is not enforcing these keys: anything left
    # behind is unreachable rows nobody can ever see or clean up.
    conn = mcp_server._conn()
    for table in ("radar_blip", "radar_move", "radar_evidence"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_handle_commits_after_every_call_including_reads(wired):
    """Same lesson as the treasures server: the write connection is not autocommit, so
    a read-only call that never commits leaves the session idle in transaction holding
    a lock."""
    real = mcp_server._conn()
    seen = {"n": 0}

    class Spy:
        def __getattr__(self, name):
            return getattr(real, name)

        def commit(self):
            seen["n"] += 1
            real.commit()

    mcp_server._state["conn"] = Spy()
    try:
        call("radar_list", {})
        call("radar_get", {"slug": "nope"})     # errors, and must still commit
        assert seen["n"] == 2
    finally:
        mcp_server._state["conn"] = real


def test_an_idle_connection_is_released(wired):
    mcp_server._conn()
    assert mcp_server.release_idle(ttl=0) is True
    assert mcp_server._state["conn"] is None
    # And the next call reopens it rather than failing.
    assert call("radar_get", {"slug": "r"})["slug"] == "r"


def test_the_ring_and_quadrant_vocabularies_come_from_the_store(wired):
    """The schemas advertise enums so an agent does not have to guess, and they are read
    off the store rather than retyped — a divergence would be invisible until a call
    the schema called valid was refused."""
    props = mcp_server.TOOLS["radar_move"][2]
    assert [r for r in props["ring"]["enum"] if r] == list(store.RINGS)
    assert mcp_server.TOOLS["radar_blip_add"][2]["quadrant"]["enum"] \
        == list(store.QUADRANTS)


# --------------------------------------------- what a blip is, and what it sits beside

def test_a_blip_can_be_given_a_definition_and_a_link_at_creation(wired):
    out = entered(name="OCA subscription_oca",
                  description="The OCA module that carries recurring billing on 19 CE.",
                  ref="https://github.com/OCA/contract")
    assert out["description"].startswith("The OCA module")
    assert out["ref"] == "https://github.com/OCA/contract"


def test_a_definition_survives_a_move(wired):
    """The reason it is a blip field and not a move field, checked at the tool surface."""
    placed(name="Thing", ring="assess", description="What it is, independent of any ring.")
    out = call("radar_move", {"slug": "r", "num": 1, "ring": "adopt", "period": "Q4",
                              "why": "it landed", "evidence": EV})
    assert out["ring"] == "adopt"
    assert out["description"] == "What it is, independent of any ring."


def test_there_is_no_tool_that_puts_a_definition_on_a_move(wired):
    # A definition on a move would change with every ring change and repeat in every
    # history row, so the move tools must not accept one.
    for name in ("radar_move", "radar_move_update"):
        assert "description" not in mcp_server.TOOLS[name][2], name


def test_related_blips_are_stated_once_and_read_both_ways(wired):
    placed(name="subscription_oca", ring="adopt")
    placed(name="OCA contract", ring="trial")
    out = call("radar_blip_update", {"slug": "r", "num": 1, "related": [2]})
    assert [r["num"] for r in out["related"]] == [2]
    # Stating it on 1 is enough for 2 to show it, and 2's entry carries 1's real ring.
    back = call("radar_blip", {"slug": "r", "num": 2})
    assert [(r["num"], r["ring"]) for r in back["related"]] == [(1, "adopt")]


def test_related_replaces_the_whole_set(wired):
    for n in ("A", "B", "C"):
        placed(name=n)
    call("radar_blip_update", {"slug": "r", "num": 1, "related": [2, 3]})
    assert len(call("radar_blip", {"slug": "r", "num": 1})["related"]) == 2
    out = call("radar_blip_update", {"slug": "r", "num": 1, "related": [3]})
    assert [r["num"] for r in out["related"]] == [3]


def test_relating_to_a_number_with_no_blip_is_refused(wired):
    placed()
    out = call("radar_blip_update", {"slug": "r", "num": 1, "related": [42]})
    assert "no blip 42" in out["error"]


def test_the_schemas_advertise_the_three_blip_level_fields(wired):
    """An agent only knows a field exists if the schema says so."""
    for name in ("radar_blip_add", "radar_blip_update"):
        props = mcp_server.TOOLS[name][2]
        assert {"description", "ref", "related"} <= set(props), name
        assert "not of a move" in props["description"]["description"]
        assert props["related"]["items"]["type"] == "integer"


def test_every_schema_required_list_matches_its_function_signature(wired):
    """Caught a real bug the moment it was written.

    Dropping `evidence` from `radar_move`'s required list without also giving the Python
    parameter a default left the schema saying "optional" while the function still refused
    to be called without it. An agent reading the schema would have been told it could omit
    a field that then raised TypeError — and the tool answers errors as data, so it would
    have looked like a radar problem rather than a contract mismatch.
    """
    import inspect
    mismatched = []
    for name, (fn, _desc, _props, required) in mcp_server.TOOLS.items():
        for p in inspect.signature(fn).parameters.values():
            needs_it = p.default is inspect.Parameter.empty
            says_it = p.name in required
            if needs_it != says_it:
                mismatched.append(
                    f"{name}.{p.name}: signature={'required' if needs_it else 'optional'} "
                    f"schema={'required' if says_it else 'optional'}")
    assert mismatched == []
    # Anti-vacuity: a registry that lost its tools would pass the line above.
    assert len(mcp_server.TOOLS) >= 15
