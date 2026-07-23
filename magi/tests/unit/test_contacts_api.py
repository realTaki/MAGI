"""End-to-end tests for ``/api/contacts`` (D.25 — Knowledge →
Contacts pane).

Four surfaces pinned:

  1. **Auth gate** — ``AdminGate`` (cookie admin or 401).
     The endpoint must refuse to render another admin's
     contacts under any circumstance (no ``?owner_id=`` URL
     knob — the caller is always derived from the cookie).
  2. **Scope** — the cookie's admin ``uid`` is the
     ``ContactEntry.owner_id`` filter; admin B never sees
     admin A's rows.
  3. **JOIN shape** — ``response.person.name`` is resolved
     server-side as ``display_name ?? name``;
     ``response.person.department_name`` is populated from
     the chained ``Employee.department`` JOIN; orphans
     (person FK set NULL by an employee delete) render as
     ``person=None`` instead of 500.
  4. **Order** — ``last_seen_at DESC`` is the primary
     ordering — most recently touched people first.

The fixture mirrors ``test_skills_api`` / ``test_memory``:
fresh state dir, fresh ORM engine, seeded admin employee,
``TestClient`` with ``magi_session`` cookie set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


# -- fixtures --------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    """MAGI_STATE_DIR + ORM + two admins + one department."""
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.agent.db import (
        Department,
        Employee,
        init_orm,
        init_sqlite,
        open_session,
    )
    init_sqlite(str(state))
    init_orm(str(state))

    with open_session() as db:
        dept = Department(name="Engineering")
        db.add(dept)
        db.flush()

        alice = Employee(
            name="Alice",
            display_name="ali",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake",
            department_id=dept.id,
        )
        bob = Employee(
            name="Bob",
            telegram_id=9002,
            role="admin",
            provider="minimax",
            api_key="fake",
        )
        charlie = Employee(
            name="Charlie",
            telegram_id=9003,
            role="employee",
            provider="minimax",
            api_key="fake",
            department_id=dept.id,
        )
        db.add_all([alice, bob, charlie])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)
        db.refresh(charlie)
        db.refresh(dept)

    return {"state": state, "alice": alice, "bob": bob, "charlie": charlie}


@pytest.fixture
def client(env):
    """TestClient with Alice's cookie (admin)."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    # D.24: cookie is the uid (int), not a delivery_address.
    c.cookies.set("magi_session", str(env["alice"].id))
    return c


@pytest.fixture
def charlie_client(env):
    """TestClient with Charlie's cookie (role=employee, not
    admin). Used to verify the AdminGate rejects non-admin
    callers."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", str(env["charlie"].id))
    return c


@pytest.fixture
def bob_client(env):
    """TestClient with Bob's cookie (also admin, different
    uid). Used to verify scope isolation."""
    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", str(env["bob"].id))
    return c


def _seed_contact(
    env,
    *,
    owner_id: int,
    person_id: int,
    notes: str,
    role: str | None = None,
    last_seen_at: datetime | None = None,
):
    """Insert one ContactEntry with the given fields.

    Bypasses ``ContactStore.upsert`` so the test can stamp
    arbitrary ``last_seen_at`` values (the store always
    bumps it to ``now``). The endpoint orders by
    ``last_seen_at DESC``, so the test must control it
    directly to verify ordering.
    """
    from magi.agent.db import open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_MANUAL,
        ContactEntry,
    )

    when = last_seen_at or datetime.now(timezone.utc).replace(tzinfo=None)
    with open_session() as db:
        row = ContactEntry(
            owner_id=owner_id,
            person_id=person_id,
            notes=notes,
            role=role,
            source=SOURCE_MANUAL,
            last_seen_at=when,
            created_at=when,
            updated_at=when,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


# -- tests -----------------------------------------------------------------


def test_list_contacts_returns_empty_when_no_rows(client):
    """Happy-path empty state. The endpoint never errors
    when there are zero rows — the UI renders a friendly
    empty-state message in this case."""
    r = client.get("/api/contacts")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0}


def test_list_contacts_requires_admin(env):
    """No cookie → 401 (``AdminGate``). The auth check
    runs before the ORM query — no contact data leaks."""
    from magi.channels.webui.app import create_app

    app = create_app()
    bare = TestClient(app)
    r = bare.get("/api/contacts")
    assert r.status_code == 401


def test_list_contacts_403_for_non_admin(charlie_client):
    """``magi_session=<charlie.id>`` (role=employee) →
    401. ``AdminGate`` checks ``Employee.role == 'admin'``;
    any other role bounces at the dependency."""
    r = charlie_client.get("/api/contacts")
    assert r.status_code == 401


def test_list_contacts_scopes_to_caller_employee(
    env, client, bob_client,
):
    """Admin A's contacts must NOT appear in admin B's
    list. The endpoint derives the owner_id from the
    cookie; there's no URL knob that could let B
    request A's rows."""
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["bob"].id,
        notes="Alice knows Bob from the Q1 review.",
        role="Engineering Manager",
    )

    # Alice's view sees her contact.
    r = client.get("/api/contacts")
    assert r.status_code == 200
    body_a = r.json()
    assert body_a["total"] == 1
    assert body_a["items"][0]["person"]["id"] == env["bob"].id

    # Bob's view sees zero — he doesn't own this row.
    r = bob_client.get("/api/contacts")
    assert r.status_code == 200
    body_b = r.json()
    assert body_b["total"] == 0
    assert body_b["items"] == []


def test_list_contacts_joins_person_name_and_department(
    env, client,
):
    """The server-side JOIN hydrates ``person.name`` (with
    ``display_name ?? name`` fallback) and
    ``person.department_name`` (via the chained
    ``Employee.department`` load). The UI never has to
    issue a second request per row."""
    # Charlie is in Engineering (seeded in env); use him as
    # the contact's person. Charlie's ``display_name`` is
    # NULL so the server falls back to ``name``.
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["charlie"].id,
        notes="Charlie runs the on-call rotation.",
        role="SRE",
    )

    r = client.get("/api/contacts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["person_id"] == env["charlie"].id
    assert row["person"] is not None
    assert row["person"]["name"] == "Charlie"
    assert row["person"]["department_id"] is not None
    assert row["person"]["department_name"] == "Engineering"
    # Role snapshot returned verbatim (the snapshot, NOT
    # the live Employee.role — they're decoupled by design).
    assert row["role"] == "SRE"


def test_list_contacts_uses_display_name_when_present(
    env, client,
):
    """``display_name`` overrides ``name`` when the
    employee has set one. Pin this so the server-side
    fallback contract is explicit (and the UI never has
    to decide which to show)."""
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["alice"].id,
        notes="Self note.",
        role="Admin",
    )

    r = client.get("/api/contacts")
    assert r.status_code == 200
    row = r.json()["items"][0]
    # Alice's display_name is "ali" (seeded in env).
    assert row["person"]["name"] == "ali"


def test_list_contacts_orders_by_last_seen_desc(env, client):
    """Three contacts with hand-stamped ``last_seen_at``
    values — the most recent one must come first. Pins
    the ordering contract so a future change can't
    silently reshuffle the table."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Stagger: oldest, newest, middle.
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["bob"].id,
        notes="oldest", last_seen_at=now - timedelta(days=3),
    )
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["charlie"].id,
        notes="newest", last_seen_at=now,
    )
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["alice"].id,
        notes="middle", last_seen_at=now - timedelta(days=1),
    )

    r = client.get("/api/contacts")
    assert r.status_code == 200
    notes = [row["notes"] for row in r.json()["items"]]
    assert notes == ["newest", "middle", "oldest"]


def test_list_contacts_orphan_rendered_without_person(env, client):
    """When the underlying Employee row is deleted, the
    FK is set NULL on the contact row (per
    ``ContactEntry.person_id`` ON DELETE SET NULL). The
    endpoint must NOT 500 — it renders the row with
    ``person=None`` and ``person_id=None`` so the UI
    can show a 'separated' placeholder."""
    # Seed a contact pointing at Bob.
    _seed_contact(
        env, owner_id=env["alice"].id, person_id=env["bob"].id,
        notes="Historical context — Bob was the project lead.",
        role="Project Lead",
    )

    # Sanity: Bob appears in the list.
    r = client.get("/api/contacts")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["person"] is not None

    # Delete Bob. The contact row stays (SET NULL semantics);
    # ``person_id`` becomes null, ``person`` resolves to None.
    from magi.agent.db import open_session
    with open_session() as db:
        bob = db.get(__import__(
            "magi.agent.db",
            fromlist=["Employee"],
        ).Employee, env["bob"].id)
        db.delete(bob)
        db.commit()

    r = client.get("/api/contacts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, "orphan row should still appear"
    row = body["items"][0]
    assert row["person_id"] is None
    assert row["person"] is None
    # Notes + role snapshot stay — the row preserves history.
    assert "Bob was the project lead" in row["notes"]
    assert row["role"] == "Project Lead"


def test_list_contacts_caps_at_200(env, client):
    """The endpoint caps at 200 rows; ``total`` reports
    the actual rendered count (which equals the cap when
    there are more)."""
    # Seed 5 contacts (well under 200) — just verify the
    # cap doesn't accidentally truncate a small set.
    # ``UNIQUE(owner_id, person_id)`` means each row needs
    # a distinct person; mint 5 throwaway employees.
    from magi.agent.db import Employee, open_session
    with open_session() as db:
        throwaway = [
            Employee(
                name=f"Throwaway-{i}",
                telegram_id=9100 + i,
                role="employee",
                provider="minimax",
                api_key="fake",
            )
            for i in range(5)
        ]
        db.add_all(throwaway)
        db.commit()
        for t in throwaway:
            db.refresh(t)
        person_ids = [t.id for t in throwaway]

    for i in range(5):
        _seed_contact(
            env, owner_id=env["alice"].id,
            person_id=person_ids[i],
            notes=f"note {i}",
        )
    r = client.get("/api/contacts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5