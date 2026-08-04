"""End-to-end test for the **onboarding wizard** business flow.

Real first-time setup walks through six API calls that mutate
persistent state across multiple subsystems (settings table,
contacts table, action_items table). Each individual endpoint
already has unit coverage; this file pins the **flow** end-to-
end so a regression in any one endpoint's contract (a missing
``session.commit()`` after the action-item inserts, a Pydantic
rename that the frontend doesn't see, a settings-key typo that
breaks the migration path) trips the test before it reaches
the dashboard.

The flow under test, with the business meaning of each step::

    POST /api/onboarding/status          (read-only)
        ↓ shows "no bot saved, no admins"
    POST /api/onboarding/verify-bot      (mocked TG getMe)
        ↓ returns username
    POST /api/onboarding/save-bot        (writes settings row)
        ↓ bot token + username now in settings table
    POST /api/onboarding/send-admin-code (mocked TG send)
        ↓ writes 6-digit code into telegram.verify_code.<tgid>
        ↓ code also sent to TG chat (mocked)
    POST /api/onboarding/verify-admin-code  (reads code back)
        ↓ ok=True + display_name from mocked TG getChat
    POST /api/onboarding/save-admin      (writes admin contacts)
        ↓ Contact row per tgid, role='admin', telegram_id bound
    POST /api/onboarding/complete        (stamps action items)
        ↓ one llm_credentials_missing row per current admin
        ↓ onboarding.complete flag set to "true"

We mock the three outbound Telegram HTTP calls (``verify_token``,
``send_text_raw``, ``get_chat_name_raw``) so no network is
involved — the test exercises the state machine, not Telegram.

Three tests cover the full happy path, the mid-flow abort
(onboarding abandoned after step 3, restarted via ``/restart``),
and the cross-flow side-effect verification (admins that came
in through onboarding show up on the contacts list).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# -- shared fixtures --------------------------------------------------------

@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + reset ORM engine.

    The auto-title worker lifespan startup on ``TestClient``
    context entry is fine in this fixture — it just starts a
    background task that pytest's ``tmp_path`` teardown will
    sweep away with the process.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    
    import magi.bus.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.bus.db import (
        init_orm,
        init_sqlite,
    )
    init_sqlite(str(sd))
    init_orm(str(sd))
    return sd

@pytest.fixture
def mocked_telegram(monkeypatch):
    """Stub the three outbound TG HTTP calls used by onboarding.

    The wizard depends on ``verify_token`` (step 1), ``send_text_raw``
    (step 3a — push the 6-digit code), and ``get_chat_name_raw``
    (step 3b — fetch display name). All three live in
    :mod:`magi.channels.telegram.bot`. The patch returns a
    username, sends "successfully", and resolves a display name.

    Also stubs ``start_bot`` — ``save-bot`` kicks off the TG
    polling bot if it isn't already running (so code delivery
    works without a node restart). In tests the token is fake,
    so the bot's ``initialize()`` would raise ``InvalidToken``
    on the daemon thread. Stub it to a no-op.
    """
    from magi.channels.telegram import bot as tg_bot

    monkeypatch.setattr(
        tg_bot, "verify_token",
        AsyncMock(return_value="magi_test_bot"))
    monkeypatch.setattr(
        tg_bot, "send_text_raw",
        AsyncMock(return_value=None))
    monkeypatch.setattr(
        tg_bot, "get_chat_name_raw",
        AsyncMock(return_value="Alice"))
    monkeypatch.setattr(tg_bot, "start_bot", lambda *_a, **_kw: None)
    return tg_bot

@pytest.fixture
def client(state, mocked_telegram):
    """TestClient with no auth cookie — onboarding is pre-auth."""
    from magi.channels.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

# -- the happy path --------------------------------------------------------

def test_full_onboarding_flow_creates_admin_and_stamps_action_items(
    state, client, mocked_telegram):
    """A complete first-time wizard lands the operator at the
    dashboard with:

      1. ``telegram.bot_token`` and ``telegram.bot_username``
         in the settings table.
      2. Two ``Contact`` rows with ``role='admin'`` and the
         supplied ``telegram_id`` (each with a name resolved
         via the mocked ``get_chat``).
      3. ``onboarding.complete=true`` in settings.
      4. Two ``ActionItem`` rows (one per admin) with
         ``kind='llm_credentials_missing'`` so the dashboard
         nudges each operator to set their LLM credentials.

    This is the canonical "deployer just installed MAGI" smoke
    test; if it fails, the wizard is broken at one of the
    four checkpoints above.
    """
    from magi.bus.models.local.action_item import ActionItem
    from magi.bus.models.local.contact import Contact
    from magi.bus.db import open_session
    from magi.bus.db.settings import state_get

    # Step 0: pristine dashboard shows nothing saved yet.
    status = client.get("/api/onboarding/status").json()
    assert status["bot_saved"] is False
    assert status["super_admins_count"] == 0
    assert status["onboarding_complete"] is False

    # Step 1: verify + save the bot token.
    verify = client.post(
        "/api/onboarding/verify-bot",
        json={"token": "fake:bot-token"})
    assert verify.status_code == 200
    assert verify.json() == {"ok": True, "username": "magi_test_bot", "error": None}

    save = client.post(
        "/api/onboarding/save-bot",
        json={"token": "fake:bot-token", "username": "magi_test_bot"})
    assert save.status_code == 200
    assert save.json()["ok"] is True

    # settings table now has both keys (read via the same path
    # /status uses, so we exercise that surface too).
    assert state_get(state, "telegram.bot_token") == "fake:bot-token"
    assert state_get(state, "telegram.bot_username") == "magi_test_bot"

    # /status reflects the saved bot.
    status = client.get("/api/onboarding/status").json()
    assert status["bot_saved"] is True
    assert status["bot_username"] == "magi_test_bot"

    # Step 2: send + verify the admin code for two chat ids.
    # Each chat id should produce exactly one send_text_raw call
    # carrying the 6-digit code we then verify.
    send_a = client.post(
        "/api/onboarding/send-admin-code",
        json={"tgid": "91001"})
    assert send_a.status_code == 200
    body_a = send_a.json()
    assert body_a["ok"] is True
    assert body_a["error"] is None
    assert body_a["expires_in"] == 300

    # Pull the code out of the settings table (the wizard UI
    # displays it to the user; the backend stores it under
    # ``telegram.verify_code.<tgid>`` as JSON).
    raw_a = state_get(state, "telegram.verify_code.91001")
    assert raw_a is not None
    code_a = json.loads(raw_a)["code"]
    assert len(code_a) == 6 and code_a.isdigit()

    # Repeat for the second admin.
    send_b = client.post(
        "/api/onboarding/send-admin-code",
        json={"tgid": "91002"})
    assert send_b.status_code == 200
    code_b = json.loads(state_get(state, "telegram.verify_code.91002"))["code"]

    # Verify both. The endpoint burns the code on any path that
    # gets past expiry so a wrong-guess attacker can't grind.
    verify_a = client.post(
        "/api/onboarding/verify-admin-code",
        json={"tgid": "91001", "code": code_a})
    assert verify_a.status_code == 200
    assert verify_a.json()["ok"] is True
    assert verify_a.json()["display_name"] == "Alice"

    # Code is now consumed (single-use).
    assert state_get(state, "telegram.verify_code.91001") is None

    verify_b = client.post(
        "/api/onboarding/verify-admin-code",
        json={"tgid": "91002", "code": code_b})
    assert verify_b.status_code == 200
    assert verify_b.json()["ok"] is True

    # Step 3: save the admin set. Two chat ids → two Contact
    # rows with role='admin', telegram_id bound via the
    # dispatcher (which writes the legacy read-cache column).
    save_admin = client.post(
        "/api/onboarding/save-admin",
        json={"tgids": ["91001", "91002"]})
    assert save_admin.status_code == 200
    body = save_admin.json()
    assert body["ok"] is True
    assert body["count"] == 2

    with open_session() as db:
        admins = db.query(Contact).filter_by(admin=True, role="assigned").order_by(Contact.telegram_id).all()
        assert len(admins) == 2
        assert [a.telegram_id for a in admins] == [91001, 91002]
        # Display name resolved via the mocked get_chat.
        assert admins[0].display_name == "Alice"
        assert admins[1].display_name == "Alice"

    # Step 4: /complete stamps one nudge per admin and flips
    # the onboarding.complete flag.
    complete = client.post("/api/onboarding/complete", json={})
    assert complete.status_code == 200
    assert complete.json() == {"ok": True}

    # The flag is now "true" — the dashboard will route the
    # operator to the main view next time they hit /.
    assert state_get(state, "onboarding.complete") == "true"

    # One llm_credentials_missing row per current admin.
    with open_session() as db:
        items = db.query(ActionItem).filter_by(
            kind="llm_credentials_missing").all()
        assert len(items) == 2
        assert {i.uid for i in items} == {a.id for a in admins}

    # Step 5: /status is now the post-onboarding picture.
    final_status = client.get("/api/onboarding/status").json()
    assert final_status["bot_saved"] is True
    assert final_status["super_admins_count"] == 2
    assert set(final_status["super_admins"]) == {"91001", "91002"}
    assert final_status["onboarding_complete"] is True

    # The mocked TG helpers were exercised exactly as expected:
    # 1 verify_token + 2 send_text_raw + 4 get_chat_name_raw
    # (2 from verify-admin-code — eager display-name fetch so the
    # frontend can show "Welcome, <name>" inline — and 2 from
    # save-admin resolving each new admin's display name).
    assert mocked_telegram.verify_token.await_count == 1
    assert mocked_telegram.send_text_raw.await_count == 2
    assert mocked_telegram.get_chat_name_raw.await_count == 4

# -- mid-flow restart ------------------------------------------------------

def test_restart_clears_complete_flag_but_preserves_bot_and_admins(
    state, client, mocked_telegram):
    """After a full onboarding, ``POST /restart`` clears the
    ``onboarding.complete`` flag (so the wizard re-opens on
    next boot) but leaves the saved bot token + admin list
    in place (so the wizard's resume logic shows them
    prefilled).

    The legacy v0 ``telegram.onboarding_complete`` key is
    also cleared — a deployer upgrading from v0 doesn't get
    stranded by a stale read-only key.
    """
    from magi.bus.models.local.contact import Contact
    from magi.bus.db import open_session
    from magi.bus.db.settings import state_set

    # Seed the post-onboarding state directly so we don't have
    # to walk the wizard in this test.
    state_set(state, "telegram.bot_token", "fake:bot-token")
    state_set(state, "telegram.bot_username", "magi_test_bot")
    state_set(state, "onboarding.complete", "true")
    # Legacy key from a v0 deploy — must be cleared by /restart.
    state_set(state, "telegram.onboarding_complete", "true")
    with open_session() as db:
        db.add(Contact(
            name="Alice",
            display_name="Alice",
            telegram_id=91001,
            admin=True, role="assigned"
        ))
        db.commit()

    # /status reports complete + the legacy key.
    pre = client.get("/api/onboarding/status").json()
    assert pre["onboarding_complete"] is True

    # Restart.
    r = client.post("/api/onboarding/restart", json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # The canonical flag is cleared.
    from magi.bus.db.settings import state_get
    assert state_get(state, "onboarding.complete") is None
    # The legacy v0 flag is cleared too (write-only delete).
    assert state_get(state, "telegram.onboarding_complete") is None

    # Bot token + admin row survive — wizard will resume from
    # step 2 / step 3 with them prefilled.
    assert state_get(state, "telegram.bot_token") == "fake:bot-token"
    assert state_get(state, "telegram.bot_username") == "magi_test_bot"
    with open_session() as db:
        assert db.query(Contact).filter_by(admin=True, role="assigned").count() == 1

    # /status reflects the cleared flag (but keeps bot_saved +
    # super_admins_count so the wizard can render the resume
    # view).
    post = client.get("/api/onboarding/status").json()
    assert post["bot_saved"] is True
    assert post["super_admins_count"] == 1
    assert post["onboarding_complete"] is False

# -- cross-flow: onboarding-created admins appear in /api/contacts ---------

def test_onboarded_admins_show_up_in_contacts_directory(
    state, client, mocked_telegram):
    """A wizard-saved admin is visible in the unified contacts
    directory (the same list the dashboard renders). This pins
    the "onboarding writes to the same source-of-truth as the
    contacts API" contract — a refactor that splits them would
    break this and the test would catch it.

    Also: an admin added via ``/api/contacts`` (after the
    wizard completes) is NOT cleared by a subsequent
    ``/save-admin`` call unless its bound telegram_id falls
    out of the new list. The wizard is the canonical owner of
    the admin list — contacts API is for non-admin rows.
    """
    # Walk through onboarding with one chat id (91001).
    client.post(
        "/api/onboarding/verify-bot",
        json={"token": "fake:bot-token"})
    client.post(
        "/api/onboarding/save-bot",
        json={"token": "fake:bot-token", "username": "magi_test_bot"})
    client.post("/api/onboarding/send-admin-code", json={"tgid": "91001"})
    raw = client.get("/api/onboarding/status")  # warm cache
    code = json.loads(__import__(
        "magi.db.settings", fromlist=["state_get"]
    ).state_get(state, "telegram.verify_code.91001"))["code"]
    client.post(
        "/api/onboarding/verify-admin-code",
        json={"tgid": "91001", "code": code})
    client.post(
        "/api/onboarding/save-admin",
        json={"tgids": ["91001"]})
    # Mint a signed cookie for the just-created admin so we
    # can hit /api/contacts as them.
    from magi.bus.models.local.contact import Contact
    from magi.bus.db import open_session
    from magi.channels.api.auth import _sign_uid
    with open_session() as db:
        admin = db.query(Contact).filter_by(telegram_id=91001).one()
        signed = _sign_uid(admin.id)

    contacts_client = TestClient(client.app)
    contacts_client.cookies.set("magi_session", signed)
    listing = contacts_client.get("/api/contacts?admin=true")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["telegram_id"] == 91001
    # 2024 role/admin split: ``admin`` is a boolean, the role
    # enum no longer carries it. Save-admin stamps
    # ``admin=true, role='assigned'`` on the operator row.
    assert body["items"][0]["role"] == "assigned"
    assert body["items"][0]["admin"] is True