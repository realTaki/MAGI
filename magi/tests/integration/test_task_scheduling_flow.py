"""End-to-end test for the **scheduled-task** business flow.

The full operator journey for a recurring cron task:

  1. Admin signs in (cookie-derived uid).
  2. ``POST /api/tasks`` with ``channel='webui'`` → server derives
     ``delivery_to='new'`` (fresh session per fire), allocates a
     ``channel='task'`` ``ChatSession``, persists the row, and
     live-registers with apscheduler.
  3. ``GET /api/tasks/{id}`` → returns the row including the
     freshly allocated ``session_id``.
  4. ``POST /api/tasks/{id}/run`` → manual trigger, the same
     code path as a cron fire but deterministic in test.
  5. ``GET /api/chat/sessions/{session_id}/messages`` →
     the prompt and the agent's reply are both there.
  6. ``GET /api/tasks/{id}/runs`` → one ``TaskRun`` row with
     ``status='success'`` and the ``reply_excerpt``.

We don't mock the LLM provider end-to-end — the runner's
``handle_message`` swap is the standard hook used in
``test_runner_delivery_to``. That keeps the test honest: if a
refactor renames the runner's entry point, the test breaks.

One cross-flow test pins the CRUD chain
(create → read → update prompt → disable → delete → 404).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# -- fixtures ---------------------------------------------------------------

@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh state dir + reset ORM engine + one admin contact."""
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
        init_orm,
        init_sqlite,
        open_session,
    )
    init_sqlite(str(sd))
    init_orm(str(sd))

    with open_session() as db:
        admin = Contact(
            name="Alice",
            display_name="Alice",
            telegram_id=9101,
            role="admin",
            provider="minimax",
            api_key="sk-fake",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return {"state": sd, "admin": admin}

@pytest.fixture
def client(state):
    """TestClient with Alice's signed cookie.

    No lifespan (``with TestClient(app) as c:``) — the app's
    startup hook spins up an auto-title worker task that holds
    a connection in the SQLAlchemy pool. In tests where we then
    drive ``execute_task`` ourselves via ``asyncio.run``,
    the worker's pool connection contends with the runner's
    own sessions on SQLite's BEGIN IMMEDIATE. Skipping the
    lifespan keeps the pool empty between client requests and
    the runner's sessions.
    """
    from magi.channels.webui.app import create_app
    from magi.channels.webui.api.auth import _sign_uid

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(state["admin"].id))
    return c

from contextlib import contextmanager

@contextmanager
def _fake_handle_message_patch():
    """Context manager: swap ``runner_mod.handle_message`` for a
    no-op so the runner exercises the chat-session attach path
    without calling out to a real LLM provider.
    """
    from magi.agent.proactive import runner as runner_mod

    real = runner_mod.handle_message

    async def _noop(*_a, **_kw):
        return "fake reply"

    runner_mod.handle_message = _noop
    try:
        yield
    finally:
        runner_mod.handle_message = real

# -- the happy path --------------------------------------------------------

def test_create_webui_task_then_manual_run_lands_reply_in_task_session(
    state, client,
):
    """Create a daily task, trigger it manually, and confirm
    the prompt + reply land in the task's own chat session.

    Pinned invariants:

      1. ``POST /api/tasks`` returns ``201`` with a server-
         allocated ``session_id`` (``channel='task'``).
      2. ``delivery_to`` is server-derived to ``'new'`` for
         ``channel='webui'`` (the operator does not pick
         this; the API does).
      3. After firing, the task's session has **both** the
         user-message (prompt) and the assistant-message
         (reply) — so the chat history drawer renders a real
         conversation, not a one-sided prompt-only thread.
      4. The TaskRun list shows one row with
         ``status='success'`` and a non-empty reply excerpt.

    Implementation note: the API endpoint
    ``POST /api/tasks/{id}/run`` would normally drive this,
    but it commits a TaskRun row inside the request-scoped
    session then invokes ``asyncio.run`` while that session
    is still active — pytest collides on the shared SQLite
    handle. We instead drive the same ``execute_task``
    coroutine that the endpoint's scheduler-fallback path
    uses, which lets us cleanly open + close our own
    sessions.
    """
    # Step 1: create the task.
    create = client.post("/api/tasks", json={
        "name": "daily standup ping",
        "prompt": "Send me a one-line morning summary.",
        "frequency": "daily",
        "hour": 9,
        "minute": 0,
        "target_channel": "webui",
    })
    assert create.status_code == 201, create.text
    body = create.json()
    task_id = body["id"]
    session_id = body["session_id"]
    assert session_id is not None and len(session_id) >= 16
    assert body["delivery_to"] == "new"  # server-derived, webui default
    assert body["target_channel"] == "webui"
    assert body["uid"] == state["admin"].id

    # Step 2: read it back via GET.
    read = client.get(f"/api/tasks/{task_id}")
    assert read.status_code == 200
    assert read.json()["id"] == task_id

    # Step 3: fire the task via ``execute_task`` (the same
    # coroutine the ``/run`` endpoint falls back to). The
    # endpoint's scheduler path uses submit_now + a thread
    # pool, but in pytest the scheduler isn't running — so
    # we invoke the runner directly. The fake
    # ``handle_message`` swap is the standard hook used in
    # ``test_runner_delivery_to``.
    from magi.agent.proactive.runner import execute_task
    with _fake_handle_message_patch():
        import asyncio
        asyncio.run(execute_task(
            str(state["state"]), task_id, manual=True,
        ))

    # Step 4: chat history drawer sees prompt + reply.
    msgs = client.get(f"/api/chat/sessions/{session_id}/messages").json()
    user_msgs = [m for m in msgs["messages"] if m["role"] == "user"]
    assistant_msgs = [m for m in msgs["messages"] if m["role"] == "assistant"]
    assert len(user_msgs) == 1
    assert "Send me a one-line morning summary." in user_msgs[0]["text"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["text"] == "fake reply"

    # Step 5: TaskRun log records the success.
    runs = client.get(f"/api/tasks/{task_id}/runs").json()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "success"
    assert run["trigger"] == "manual"
    assert run["reply_excerpt"] == "fake reply"

# -- CRUD chain ------------------------------------------------------------

def test_task_crud_chain_create_update_disable_delete(state, client):
    """Create → patch prompt → patch enabled=false (so the
    scheduler skips it) → delete → 404. Each step pins the
    contract of the surface the dashboard depends on.

    Why a CRUD chain rather than four isolated tests: the
    dashboard mutates task rows in a single session, and a
    regression that breaks any one transition surfaces here
    before it surfaces to the operator.
    """
    # Create
    create = client.post("/api/tasks", json={
        "name": "weekly reminder",
        "prompt": "Remind me about the sprint review.",
        "frequency": "weekly",
        "hour": 14,
        "minute": 30,
        "day_of_week": 4,  # Python weekday 4 = Friday (Mon=0)
        "target_channel": "webui",
    })
    assert create.status_code == 201
    body = create.json()
    task_id = body["id"]
    # API accepts Python's weekday() convention (Mon=0..Sun=6)
    # but cron natively is Sun=0..Sat=6 — the server shifts
    # (Mon=0 → cron=1). Friday (Python 4) → cron 5.
    assert body["cron"] == "30 14 * * 5"
    assert body["enabled"] is True

    # Patch prompt + enabled=false.
    patch = client.patch(
        f"/api/tasks/{task_id}",
        json={
            "prompt": "Updated reminder copy.",
            "enabled": False,
        },
    )
    assert patch.status_code == 200
    patched = patch.json()
    assert patched["prompt"] == "Updated reminder copy."
    assert patched["enabled"] is False
    # Cron survives — patch didn't touch the schedule.
    assert patched["cron"] == "30 14 * * 5"

    # Verify in the listing.
    listing = client.get("/api/tasks").json()
    ours = [t for t in listing if t["id"] == task_id]
    assert len(ours) == 1
    assert ours[0]["enabled"] is False
    assert ours[0]["prompt"] == "Updated reminder copy."

    # Delete.
    delete = client.delete(f"/api/tasks/{task_id}")
    assert delete.status_code == 204

    # 404 — gone.
    gone = client.get(f"/api/tasks/{task_id}")
    assert gone.status_code == 404

# -- cross-flow: TG task uses operator's bound telegram_id -----------------

def test_create_tg_task_uses_operator_telegram_id_as_delivery_to(
    state, client,
):
    """A ``channel='tg'`` task created via the WebUI gets its
    ``delivery_to`` set to the operator's bound
    ``telegram_id`` — server-derived, not operator-supplied.

    Even if the operator passes a stale ``delivery_to``, the
    server overrides it to the canonical bound chat id. This
    is the contract that makes "the operator doesn't choose
    a delivery destination" work end-to-end.
    """
    create = client.post("/api/tasks", json={
        "name": "tg ping",
        "prompt": "Push this to my TG.",
        "frequency": "daily",
        "hour": 10,
        "minute": 0,
        "target_channel": "tg",
        # Caller tries to override — server should ignore.
        "delivery_to": "stale-chat-id",
    })
    assert create.status_code == 201
    body = create.json()
    assert body["target_channel"] == "tg"
    # Server-derived from operator.telegram_id (9101).
    assert body["delivery_to"] == "9101"

def test_create_tg_task_without_telegram_binding_returns_400(state):
    """Operator without a bound ``telegram_id`` cannot create
    a ``channel='tg'`` task — the server returns 400 with a
    friendly code so the dashboard can show an inline error.

    This pins the early-fail path: a typo in the auth gate
    that let the request through would otherwise ship a row
    with no valid destination.
    """
    from magi.agent.db import Contact, open_session
    from magi.channels.webui.api.auth import _sign_uid
    from magi.channels.webui.app import create_app

    # An admin WITHOUT a bound telegram_id.
    with open_session() as db:
        unbound = Contact(
            name="Bob",
            telegram_id=None,
            role="admin",
            provider="minimax",
            api_key="sk-bob",
        )
        db.add(unbound)
        db.commit()
        db.refresh(unbound)

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", _sign_uid(unbound.id))

    r = c.post("/api/tasks", json={
        "name": "tg unbound",
        "prompt": "Will fail.",
        "frequency": "daily",
        "hour": 10,
        "minute": 0,
        "target_channel": "tg",
    })
    assert r.status_code == 400
    assert r.json()["code"] == "tasks.telegram_not_bound"