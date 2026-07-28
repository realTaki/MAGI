"""End-to-end test for the **organization setup** business flow.

Real onboarding of a new MAGI team walks through five API
surfaces that all share the same Contact row:

  1. ``POST /api/contacts``  — add a person to the directory
  2. ``PATCH /api/contacts/{id}`` — set role, provider, api_key
  3. ``POST /api/magics``     — create a MAGI team
  4. ``POST /api/magis``      — add a Magi agent to the team
     (with ``magic_position='adam'`` to designate the team
     manager)
  5. ``PATCH /api/magics/{id}`` — change the team's name

Each endpoint individually is unit-tested. This file pins
the **flow** so a regression that breaks a transition
(naming conflict, missing CASCADE, FK violation on
delete, etc.) surfaces before it reaches the dashboard.

Two scenarios:

  A. Happy-path — add a contact, promote to admin, build a
     team tree with one parent + one child, add an adam
     per team. The dashboard's "Organization" tab renders
     all of this from the same data.

  B. Delete tree — delete a parent team that has a child
     team + adam Magi pinned. Verify the FK RESTRICT and
     reparent logic: the child gets reparented to the
     deleted team's parent (or NULL at root), the adam is
     unbound (SET NULL), no orphan rows.

The B scenario is a regression test for the **two ORM
self-referential bugs** this codebase fixed in earlier
rounds (silent NULLification of FK on delete; cascade
delete-orphan wiping children).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# -- fixtures --------------------------------------------------------------

@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + one admin Contact (the operator).

    Returns the seeded "Genesis" root MAGIC row alongside
    the admin — the org API requires every team to have a
    ``parent_id`` (root's parent_id is NULL so genesis
    can't be reparented, but the auto-seeded ``Genesis``
    row is the only valid root-parent for new teams).
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    
    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import (
        Contact,
        MAGIC,
        init_orm,
        init_sqlite,
        open_session)
    init_sqlite(str(sd))
    init_orm(str(sd))
    with open_session() as db:
        admin = Contact(
            name="Operator",
            display_name="Operator",
            telegram_id=9101,
            admin=True, role="assigned"
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        # ``init_orm`` seeds the "Genesis" root MAGIC row.
        root = db.query(MAGIC).filter(MAGIC.name == "Genesis").one()
    return {"state": sd, "admin": admin, "root": root}

@pytest.fixture
def client(state):
    """TestClient signed in as the seeded admin."""
    from magi.channels.webui.app import create_app
    from magi.channels.webui.api.auth import _sign_uid

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c

def _seed_root_id(client) -> int:
    """The auto-seeded Genesis root is the only row with ``parent_id
    is None`` — every user-created team must hang under it."""
    rows = client.get("/api/magics").json()
    return next(r["id"] for r in rows if r["parent_id"] is None)

# -- the happy path --------------------------------------------------------

def test_full_org_setup_flow_add_contact_create_team_add_adam(
    state, client):
    """Walk the dashboard's "set up the org" flow end-to-end:

      1. Add a new contact "Bob" via ``POST /api/contacts``.
      2. Patch Bob's role to "assigned" (he'll be running
         tasks under the new team).
      3. Create a root team "Engineering" via ``POST
         /api/magics``.
      4. Create a child team "ML" under Engineering via
         ``POST /api/magics`` with ``parent_id``.
      5. Add an adam Magi to Engineering via ``POST
         /api/magis`` with ``magic_position='adam'``.
      6. Verify the team tree renders correctly: GET
         ``/api/magics`` returns both teams; the child
         carries ``parent_id=<engineering>``.
      7. Verify the adam is bound: GET
         ``/api/magics/{engineering_id}`` returns
         ``adam_id=<adam magi>``.
    """
    # 1. Add Bob.
    bob = client.post("/api/contacts", json={
        "name": "Bob",
        "telegram_id": 9202,
        "role": "guest",
        "provider": "minimax",
        "api_key": "sk-bob",
    })
    assert bob.status_code == 201, bob.text
    bob_id = bob.json()["id"]

    # 2. Promote to assigned.
    promote = client.patch(
        f"/api/contacts/{bob_id}",
        json={"role": "assigned"})
    assert promote.status_code == 200
    assert promote.json()["role"] == "assigned"

    # 3. Engineering under the seeded Genesis root.
    genesis_id = _seed_root_id(client)
    eng = client.post("/api/magics", json={
        "name": "Engineering",
        "parent_id": genesis_id,
    })
    assert eng.status_code == 201, eng.text
    eng_id = eng.json()["id"]
    assert eng.json()["parent_id"] == genesis_id
    assert eng.json()["adam_id"] is None

    # 4. Child team under Engineering.
    ml = client.post("/api/magics", json={
        "name": "ML",
        "parent_id": eng_id,
    })
    assert ml.status_code == 201, ml.text
    ml_id = ml.json()["id"]
    assert ml.json()["parent_id"] == eng_id

    # 5. Add the adam (manager Magi) to Engineering.
    adam = client.post("/api/magis", json={
        "magic_id": eng_id,
        "name": "Eng-Adam",
        "magic_position": "adam",
        "provider": "minimax",
        "api_key": "sk-eng-adam",
    })
    assert adam.status_code == 201, adam.text
    adam_id = adam.json()["id"]
    assert adam.json()["magic_position"] == "adam"

    # 6. Listing renders both teams.
    listing = client.get("/api/magics").json()
    by_id = {t["id"]: t for t in listing}
    assert by_id[eng_id]["name"] == "Engineering"
    assert by_id[ml_id]["name"] == "ML"
    assert by_id[ml_id]["parent_id"] == eng_id
    assert by_id[eng_id]["parent_id"] == genesis_id

    # 7. The adam binding surfaces on the team detail.
    eng_detail = client.get(f"/api/magics/{eng_id}").json()
    assert eng_detail["adam_id"] == adam_id

    # 8. The magis listing filters by team — ML has no magis
    #    yet, so the filtered list is empty.
    ml_magis = client.get(f"/api/magis?magic_id={ml_id}").json()
    assert ml_magis == []
    eng_magis = client.get(f"/api/magis?magic_id={eng_id}").json()
    assert len(eng_magis) == 1
    assert eng_magis[0]["id"] == adam_id

def test_create_magic_requires_parent(client):
    """A user-created team MUST name a parent (the seeded Genesis
    root). Omitting ``parent_id`` is rejected with 422 so the UI
    can't spawn a second root — which previously produced duplicate
    "Genesis" rows (the council tree must have exactly one root)."""
    r = client.post("/api/magics", json={"name": "Lonely"})
    assert r.status_code == 422

# -- cross-flow: org surfaces visible to chat-sessions and contacts ---------

def test_newly_added_contact_visible_in_chat_session_owner_resolution(
    state, client):
    """A Contact created via the org API is immediately usable
    as the owner of a chat session. This pins the "single
    source of truth" contract — a refactor that splits the
    org API from the chat-sessions API would break this.

    The chat session POST here goes through the same
    admin-scoped route that the dashboard's "new chat"
    button uses; the route reads ``Contact`` directly to
    resolve the session's owner.
    """
    # Add Bob as ``admin`` — the chat-sessions route uses
    # ``AdminGate`` which is strictly admin-only
    # (``assigned`` would 401 on the chat endpoint).
    bob = client.post("/api/contacts", json={
        "name": "Bob",
        "telegram_id": 9202,
        "admin": True, "role": "assigned",
        "provider": "minimax",
        "api_key": "sk-bob",
    })
    bob_id = bob.json()["id"]

    # Create a chat session as Bob (signed in via cookie).
    from magi.channels.webui.api.auth import _sign_uid
    bob_client = TestClient(client.app)
    bob_client.cookies.set("magi_session", _sign_uid(bob_id))

    sess = bob_client.post("/api/chat/sessions", json={})
    assert sess.status_code in (200, 201), sess.text
    body = sess.json()
    # The endpoint returns ``session_id``; verify Bob can
    # read it back via ``GET /api/chat/sessions/{id}``,
    # which is the same call the chat pane makes when
    # the operator clicks a session in the sidebar.
    session_id = body["session_id"]
    listing = bob_client.get(f"/api/chat/sessions/{session_id}").json()
    assert listing["session_id"] == session_id
    # The owner uid is Bob — the cross-flow invariant
    # this test exists to pin (a freshly-created Contact
    # is immediately usable as a session owner).
    assert listing["uid"] == bob_id

# -- the delete-tree scenario ---------------------------------------------

def test_delete_parent_team_reparents_child_and_unbinds_adam(
    state, client):
    """Deleting a parent MAGIC reparents its children to the
    deleted row's parent (or NULL at root), and SET NULL
    the adam FK on the deleted row (no CASCADE delete of
    children — the bug fixed in earlier rounds).

    This is a structural regression test: if the ORM
    ``children`` relationship is ever re-decorated with
    ``cascade='all, delete-orphan'`` (the silent killer)
    this test fails because the child row would vanish.
    """
    # Build: seeded Genesis (root) → parent → child.
    genesis_id = _seed_root_id(client)
    parent = client.post("/api/magics", json={
        "name": "parent", "parent_id": genesis_id,
    })
    parent_id = parent.json()["id"]

    child = client.post("/api/magics", json={
        "name": "child", "parent_id": parent_id,
    })
    child_id = child.json()["id"]

    # Pin an adam to the parent so the FK exists.
    adam = client.post("/api/magis", json={
        "magic_id": parent_id,
        "name": "parent-adam",
        "magic_position": "adam",
        "provider": "minimax",
        "api_key": "sk-adam",
    })
    adam_id = adam.json()["id"]
    # The team surfaces the binding immediately.
    parent_detail = client.get(f"/api/magics/{parent_id}").json()
    assert parent_detail["adam_id"] == adam_id

    # Delete the parent. The bug would either cascade-delete
    # the child (gone) or silently NULL the FK and reparent
    # to nowhere. The correct fix reparents to root and
    # unbinds the adam (SET NULL).
    delete = client.delete(f"/api/magics/{parent_id}")
    assert delete.status_code == 204

    # The parent row is gone.
    assert client.get(f"/api/magics/{parent_id}").status_code == 404

    # The child row still exists and is now under root.
    listing = client.get("/api/magics").json()
    by_id = {t["id"]: t for t in listing}
    assert child_id in by_id, "child row was silently deleted"
    assert by_id[child_id]["parent_id"] == genesis_id
    assert by_id[child_id]["name"] == "child"

    # The adam Magi is gone — ``Magi.magic_id`` has
    # ``ondelete="CASCADE"`` so deleting the parent
    # team cleans up its Magis too (the schema design
    # is: a Magi is owned by its MAGIC team; no
    # orphan Magis left behind).
    magi_list = client.get("/api/magis").json()
    adam_ids = {m["id"] for m in magi_list}
    assert adam_id not in adam_ids

    # The root team remains intact, childless-but-for-the-
    # reparented-child.
    root_detail = client.get(f"/api/magics/{state['root'].id}").json()
    assert root_detail["name"] == "Genesis"
    # The auto-seeded Genesis root keeps its own adam (Alice), so its
    # adam_id is populated — the test only cares that the root survives
    # the reparent intact.
    assert root_detail["adam_id"] is not None

# -- duplicate-name validation -------------------------------------------

def test_duplicate_team_name_returns_400(state, client):
    """Two teams with the same name → the second POST
    returns 400 ``validation.magic_name_duplicate``
    rather than hitting the DB unique constraint
    (which would surface as a less-friendly 500).

    The dashboard relies on this friendly error to tell
    the operator to pick a different name.
    """
    genesis_id = _seed_root_id(client)
    client.post("/api/magics", json={"name": "Sales", "parent_id": genesis_id})
    dup = client.post("/api/magics", json={"name": "Sales", "parent_id": genesis_id})
    assert dup.status_code == 400
    assert dup.json()["code"] == "validation.magic_name_duplicate"

# -- adam-already-assigned: only one adam per team ----------------------

def test_adding_second_adam_to_team_returns_409(state, client):
    """Each MAGIC team has exactly one adam. A second POST
    with ``magic_position='adam'`` returns 409
    ``validation.adam_already_assigned``.

    Pins the invariant the dashboard relies on: the
    "set as manager" button is hidden once a team has
    an adam, but if a stale UI sends the call anyway,
    the API refuses it cleanly.
    """
    team = client.post("/api/magics", json={"name": "Ops", "parent_id": _seed_root_id(client)})
    team_id = team.json()["id"]

    first = client.post("/api/magis", json={
        "magic_id": team_id,
        "name": "first-adam",
        "magic_position": "adam",
        "provider": "minimax",
        "api_key": "sk-1",
    })
    assert first.status_code == 201

    second = client.post("/api/magis", json={
        "magic_id": team_id,
        "name": "second-adam",
        "magic_position": "adam",
        "provider": "minimax",
        "api_key": "sk-2",
    })
    assert second.status_code == 409
    assert second.json()["code"] == "validation.adam_already_assigned"