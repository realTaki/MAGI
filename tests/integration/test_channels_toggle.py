"""Integration tests for ``/api/channels`` toggle.

Pins the operator-facing flow on the Settings → Channels
tab: the dashboard flips a row's switch and the server
persists the new ``enabled`` list. We also pin the
Pydantic V2 422 fix: an empty ``enabled`` list (after the
operator disables every non-required channel) used to
trip ``Field(min_length=1)`` with a 422; the contract is
now ``min_length=0`` with the view re-adding the
required ``WEBUI`` server-side.

Backstory: this regressed when a stale ``data`` ref in
the dashboard's ``useQuery`` cache caused the optimistic
``enabled`` list to be ``[]`` momentarily, and the
toggle handler's ``filter`` then sent an empty body to
the server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    sd = tmp_path / "state"
    sd.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))

    import magi.bus.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.bus.db import (
        init_orm,
        open_session,
    )
    from magi.bus.models.local.contact import Contact
    init_orm(str(sd))

    with open_session() as db:
        admin = Contact(
            name="Channel admin",
            telegram_id=8801,
            admin=True, role="assigned"
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return {"state": sd, "admin": admin}


@pytest.fixture
def client(state):
    from magi.channels.api.app import create_app
    from magi.channels.api.auth import _sign_uid
    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c


# -- tests -----------------------------------------------------------------


def test_get_channels_includes_webui_in_enabled(state, client):
    """The default ``enabled`` list is just ``["webui"]`` —
    the operator has not configured any IM channel yet.
    This is the snapshot the dashboard renders on first
    load of the Settings tab."""
    body = client.get("/api/channels").json()
    assert body["enabled"] == ["webui"]


def test_toggle_on_telegram_persists(state, client):
    """The operator flips the TG switch to "on". The
    server-side toggle endpoint must accept the
    ``["webui", "tg"]`` payload and re-add it to
    ``settings.channels.enabled``.
    """
    r = client.post("/api/channels", json={"enabled": ["webui", "tg"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tg" in body["enabled"]
    assert "webui" in body["enabled"]

    # Round-trip — a second GET reflects the persisted
    # state.
    listed = client.get("/api/channels").json()
    assert "tg" in listed["enabled"]


def test_toggle_off_telegram_keeps_webui(state, client):
    """After turning TG on, turning it back off leaves
    only ``webui`` enabled. ``webui`` is a required
    channel and must survive any operator toggle."""
    # First turn TG on.
    client.post("/api/channels", json={"enabled": ["webui", "tg"]})
    # Then turn it back off.
    r = client.post("/api/channels", json={"enabled": ["webui"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] == ["webui"]


def test_toggle_disabling_all_pins_webui(state, client):
    """Operator toggles off every IM channel — the empty
    ``enabled`` payload is accepted (Pydantic V2 used to
    reject it with a 422). The server re-adds ``webui``
    so the persisted state is never the literal ``[]``."""
    r = client.post("/api/channels", json={"enabled": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] == ["webui"]


def test_toggle_unknown_channel_returns_400(state, client):
    """A channel name that's not in the ``Channel`` enum
    is a programming error, not a runtime condition —
    the dashboard should never produce one. The endpoint
    surfaces 400 with a code the frontend can pattern on."""
    r = client.post("/api/channels", json={"enabled": ["webui", "fake-channel"]})
    assert r.status_code == 400
    assert r.json()["code"] == "channels.unknown"


def test_toggle_without_webui_in_payload_auto_pins_webui(state, client):
    """The view-level invariant: ``webui`` is required and
    cannot be disabled. The endpoint auto-inserts
    ``webui`` rather than 400-ing the operator — a stale
    dashboard toggle that drops ``webui`` from
    ``enabled`` can't accidentally disable the control
    plane. The response surfaces the corrected list so
    the dashboard picks it up on the next refetch."""
    r = client.post("/api/channels", json={"enabled": ["tg"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] == ["tg", "webui"]
    # Round-trip
    listed = client.get("/api/channels").json()
    assert listed["enabled"] == ["tg", "webui"]


def test_toggle_requires_admin(state, client):
    """Cookie-less request → 401, like every other
    admin-gated endpoint."""
    bare = TestClient(__import__("magi.channels.api.app", fromlist=["create_app"]).create_app())
    r = bare.post("/api/channels", json={"enabled": ["webui"]})
    assert r.status_code == 401
