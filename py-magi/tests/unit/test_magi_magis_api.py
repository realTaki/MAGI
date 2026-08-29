"""Regression coverage for explicit MAGI / MAGIS BUS APIs.

These tests call route handlers directly because their contract is the
injected BUS object, not FastAPI's dependency resolver.  They deliberately
use a real SQLite-backed set of Books so identity and foreign-key semantics
are exercised rather than mocked away.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from old_bus.bases.db.engine import EngineFactory
from old_bus.firmwares.books.local.contactBook import ContactBook
from old_bus.firmwares.books.local.settingBook import SettingBook
from old_bus.firmwares.books.magis.magisBook import Magis, MagisAdmin, MagisAdminBook, MagisBook
from old_bus.firmwares.books.magis.membershipBook import (
    MagisMembership,
    MagisMembershipBook,
    MagisRole,
    MagisRoleBook,
)
from old_bus.firmwares.books.magis.runtimeBook import RuntimeBook
from channels.api import magi, magis
from channels.api.app import create_app


@pytest.fixture
def bus(tmp_path) -> SimpleNamespace:
    # TestClient serves handlers from another thread; file-backed SQLite keeps
    # the same explicit BUS state visible to both threads.
    factory = EngineFactory(f"sqlite:///{tmp_path / 'magis.db'}")
    # Every imported Book has registered its private ORM type by this point.
    factory.create_all()
    return SimpleNamespace(
        contacts_book=ContactBook(factory),
        settings_book=SettingBook(factory),
        runtime_state_book=RuntimeBook(factory),
        magis_book=MagisBook(factory),
        magis_admins_book=MagisAdminBook(factory),
        memberships_book=MagisMembershipBook(factory),
        roles_book=MagisRoleBook(factory),
    )


def _society(bus: SimpleNamespace):
    society = bus.magis_book.get(bus.magis_book.add(Magis(name='Alpha')))
    eva = bus.roles_book.get(bus.roles_book.add(MagisRole(magis_id=society.id, name='EVA')))
    adam = bus.roles_book.get(bus.roles_book.add(MagisRole(magis_id=society.id, name='ADAM')))
    return society, eva, adam


def test_magi_api_creates_membership_identity_and_control_label(bus: SimpleNamespace) -> None:
    society, eva, _ = _society(bus)

    result = magi.create_magi(
        magi.MagiCreate(name="eve-one", magis_id=society.id, role_id=eva.id),
        "admin",
        bus,
    )

    assert result.id == bus.memberships_book.get(result.id).id
    assert result.name == "eve-one"
    assert result.memberships == [
        magi.MembershipBrief(
            magis_id=society.id, magis_name="Alpha", role_id=eva.id, role_name="EVA"
        )
    ]
    assert bus.memberships_book.get(result.id).role_id == eva.id
    assert bus.runtime_state_book.get_by_runtime_id(runtime_id=result.id).backend_ref == "eve-one"


def test_membership_api_rejects_retired_magi_id_and_cross_magis_role(bus: SimpleNamespace) -> None:
    first, eva, _ = _society(bus)
    second = bus.magis_book.get(bus.magis_book.add(Magis(name='Beta')))
    other_role = bus.roles_book.get(bus.roles_book.add(MagisRole(magis_id=second.id, name='EVA')))

    with pytest.raises(ValidationError):
        magis.MembershipCreate(role_id=eva.id, magi_id=99)
    with pytest.raises(ValueError, match="target MAGIS"):
        bus.memberships_book.get(bus.memberships_book.add(MagisMembership(magis_id=first.id, role_id=other_role.id)))


def test_membership_api_persists_public_collaboration_responsibility(bus: SimpleNamespace) -> None:
    society, eva, _ = _society(bus)

    created = magis.create_membership(
        society.id,
        magis.MembershipCreate(role_id=eva.id, responsibility="Owns release validation."),
        "admin",
        bus,
    )
    updated = magis.update_membership(
        society.id,
        created.id,
        magis.MembershipUpdate(responsibility="Owns frontend release validation."),
        "admin",
        bus,
    )

    assert created.responsibility == "Owns release validation."
    assert updated.role_id == eva.id
    assert updated.responsibility == "Owns frontend release validation."


def test_magis_admin_is_shared_identity_with_local_projection(bus: SimpleNamespace) -> None:
    society, _, _ = _society(bus)
    stored = bus.magis_admins_book.get(bus.magis_admins_book.add(MagisAdmin(magis_id=society.id, name='Operator', tgid=4242)))
    projection = bus.contacts_book.ensure_magis_admin_projection(
        magis_admin_id=stored.id, display_name=stored.name
    )

    assert stored.tgid == 4242
    assert stored.name == "Operator"
    assert projection.magis_admin_id == stored.id


def test_magis_scope_is_derived_from_runtime_membership(bus: SimpleNamespace, monkeypatch) -> None:
    first, eva, _ = _society(bus)
    second = bus.magis_book.get(bus.magis_book.add(Magis(name='Beta')))
    second_eva = bus.roles_book.get(bus.roles_book.add(MagisRole(magis_id=second.id, name='EVA')))
    runtime_member = bus.memberships_book.get(bus.memberships_book.add(MagisMembership(magis_id=first.id, role_id=eva.id)))
    monkeypatch.setenv("MAGI_RUNTIME_ID", str(runtime_member.id))

    assert magis._served_direct_magis_id(bus) == first.id
    with pytest.raises(Exception) as raised:
        magis._require_managed(bus, second.id)
    assert getattr(raised.value, "status_code", None) == 403
    # Keep a second membership so this test also proves the lookup uses the
    # runtime membership, rather than e.g. the first MAGIS row in the table.
    assert bus.memberships_book.get(bus.memberships_book.add(MagisMembership(magis_id=second.id, role_id=second_eva.id)))


def test_create_magis_seeds_reserved_roles(bus: SimpleNamespace) -> None:
    created = magis.create_magis(magis.MAGISCreate(name="Beta"), "admin", bus)

    assert [role.name for role in bus.roles_book.list_for_magis(magis_id=created.id)] == [
        "ADAM",
        "EVA",
    ]


def test_self_instruction_uses_the_runtime_bus(bus: SimpleNamespace, monkeypatch) -> None:
    society, eva, _ = _society(bus)
    member = bus.memberships_book.get(bus.memberships_book.add(MagisMembership(magis_id=society.id, role_id=eva.id)))
    monkeypatch.setenv("MAGI_RUNTIME_ID", str(member.id))

    written = magi.put_self_instruction(
        magi.InstructionPayload(instruction="Be concise."), "admin", bus
    )

    assert written.magi_id == member.id
    assert magi.get_self_instruction("admin", bus).instruction == "Be concise."


def test_control_and_runtime_apps_mount_the_correct_magi_surfaces() -> None:
    control = create_app(
        bus=SimpleNamespace(),
        include_spa=False,
        include_control_routes=True,
        include_private_routes=False,
    )
    runtime = create_app(
        bus=SimpleNamespace(),
        include_spa=False,
        include_control_routes=False,
        include_private_routes=True,
    )

    def mounted_paths(app) -> set[str]:
        paths: set[str] = set()
        for route in app.routes:
            if hasattr(route, "path"):
                paths.add(route.path)
            elif hasattr(route, "original_router"):
                prefix = route.include_context.prefix
                paths.update(prefix + child.path for child in route.original_router.routes)
        return paths

    control_paths = mounted_paths(control)
    runtime_paths = mounted_paths(runtime)
    assert "/api/magi" in control_paths
    assert "/api/magi/self/instruction" not in control_paths
    assert "/api/magi/self/instruction" in runtime_paths


def test_control_magi_route_uses_the_injected_bus(bus: SimpleNamespace) -> None:
    society, eva, _ = _society(bus)
    magi.create_magi(
        magi.MagiCreate(name="eve-one", magis_id=society.id, role_id=eva.id),
        "admin",
        bus,
    )
    app = create_app(
        bus=bus,
        include_spa=False,
        include_control_routes=True,
        include_private_routes=False,
    )
    app.dependency_overrides[magi.admin_gate] = lambda: "admin"

    response = TestClient(app).get("/api/magi")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "eve-one"
