"""Tests for the WebUI soul editor endpoint.

The endpoint mounts under ``/api`` and reads / writes the
workspace ``SOUL.md``. Tests cover:

  - GET returns the bundled default when the workspace file
    is missing (``is_bundled_fallback=true``)
  - GET returns the workspace copy with ``is_bundled_fallback=false``
    when present
  - PUT writes atomically and updates ``modified_at``
  - PUT with empty / whitespace-only content is rejected
  - PUT with > 8 KB content is rejected (422 from Pydantic)
  - POST /reset rewrites to the bundled default
  - Cookie-less callers get 401 (admin gate)

TestClient hits the in-process FastAPI app via ``magi.node``
factory wiring, so paths resolve against the same
``workspace_root`` derivation production uses.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def soul_env(monkeypatch, tmp_path):
    """Pin ``MAGI_STATE_DIR`` to a fresh tmp dir so each test
    starts with no ``SOUL.md`` (we'll write it explicitly in
    the cases that need one). The workspace root is
    ``<tmp>/parent/memories``'s parent = ``<tmp>/parent`` —
    but :func:`workspace_root` walks ``MAGI_STATE_DIR.parent``,
    so we set state dir to ``<tmp>/memories`` and the workspace
    lands at ``<tmp>``.
    """
    state = tmp_path / "memories"
    state.mkdir()
    workspace = tmp_path
    # ``init_orm`` casts the env var to ``os.environ[str]``;
    # pass a plain ``str`` to avoid a PosixPath TypeError on
    # Python 3.12.
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    # Set MAGI_WORKSPACE_DIR so ``workspace_root`` returns a
    # predictable, tmp-scoped path that won't collide with the
    # host's actual ``/workspace``.
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(workspace))
    return state, workspace


@pytest.fixture
def client(soul_env):
    """An in-process TestClient with the cookie admin gate.

    Seeds a single admin employee + writes the ``magi_session``
    cookie so the AdminGate dependency lets the request
    through.
    """
    state_dir, workspace = soul_env

    # Lazy import — keeps the test module fast to import and
    # ensures the env var is set before the factory builds.
    from magi.agent.db import (
        Employee,
        init_orm,
        open_session,
    )

    init_orm(str(state_dir))
    with open_session() as s:
        s.query(Employee).delete()
        s.add(
            Employee(
                name="TA-soul",
                telegram_id=8001,
                role="admin",
                provider="minimax",
                api_key="fake",
            )
        )
        s.commit()

    from magi.channels.webui.app import create_app

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "8001")
    return c


# -- GET ------------------------------------------------------------------


def test_get_soul_returns_bundled_fallback_when_missing(client, soul_env):
    """No SOUL.md on disk → is_bundled_fallback=true.

    The agent loop reads ``prompts/fallback_persona.md`` in this
    state; the API mirrors that so the Settings UI can warn
    the operator that "save" creates a real file.
    """
    _, workspace = soul_env
    # Defensive: make sure no stale SOUL.md exists.
    soul_path = workspace / "SOUL.md"
    if soul_path.exists():
        soul_path.unlink()

    r = client.get("/api/soul")
    assert r.status_code == 200
    data = r.json()
    assert data["is_bundled_fallback"] is True
    assert data["modified_at"] is None
    # The fallback persona is the generic bundled text — must
    # be non-empty so the textarea isn't blank.
    assert len(data["content"]) > 0


def test_get_soul_returns_workspace_copy_when_present(client, soul_env):
    """SOUL.md exists → content matches, is_bundled_fallback=false,
    modified_at is set to the file's mtime."""
    _, workspace = soul_env
    soul_path = workspace / "SOUL.md"
    soul_path.write_text("# Custom\n\nThis is the operator's persona.\n", encoding="utf-8")

    r = client.get("/api/soul")
    assert r.status_code == 200
    data = r.json()
    assert data["is_bundled_fallback"] is False
    assert data["modified_at"] is not None
    assert "Custom" in data["content"]
    assert "operator's persona" in data["content"]


# -- PUT ------------------------------------------------------------------


def test_put_soul_persists(client, soul_env):
    """Saving writes the new content to the workspace file."""
    _, workspace = soul_env
    soul_path = workspace / "SOUL.md"

    r = client.put(
        "/api/soul",
        json={"content": "# Saved\n\nThis is a test persona."},
    )
    assert r.status_code == 200
    data = r.json()
    assert "modified_at" in data

    # File on disk matches what we sent.
    assert soul_path.exists()
    on_disk = soul_path.read_text(encoding="utf-8").strip()
    assert "Saved" in on_disk
    assert "test persona" in on_disk


def test_put_soul_empty_body_rejected(client):
    """Pydantic min_length=1 → 422."""
    r = client.put("/api/soul", json={"content": ""})
    assert r.status_code == 422


def test_put_soul_whitespace_only_rejected(client):
    """Trim happens server-side; whitespace-only is also rejected."""
    r = client.put("/api/soul", json={"content": "   \n\n   "})
    assert r.status_code == 400
    data = r.json()
    assert data["code"] == "validation.soul_empty"


def test_put_soul_too_long_rejected(client):
    """> 8 KB → 422 (Pydantic max_length)."""
    r = client.put(
        "/api/soul",
        json={"content": "x" * 8001},
    )
    assert r.status_code == 422


def test_put_soul_atomic_no_leftover_tmp(client, soul_env):
    """A successful PUT leaves no ``.SOUL.md.*.tmp`` file behind."""
    _, workspace = soul_env
    r = client.put(
        "/api/soul",
        json={"content": "# atomic\n\ntest"},
    )
    assert r.status_code == 200
    leftovers = list(workspace.glob(".SOUL.md.*.tmp"))
    assert leftovers == []


# -- POST /reset ---------------------------------------------------------


def test_reset_soul_writes_bundled_default(client, soul_env):
    """Reset overwrites a customised SOUL.md with the bundled one."""
    _, workspace = soul_env
    soul_path = workspace / "SOUL.md"
    soul_path.write_text("# Custom garbage\n\ndelete me\n", encoding="utf-8")

    r = client.post("/api/soul/reset")
    assert r.status_code == 200

    on_disk = soul_path.read_text(encoding="utf-8").strip()
    # The bundled default starts with "# MAGI Soul"; the
    # garbage string must be gone.
    assert "Custom garbage" not in on_disk
    assert "delete me" not in on_disk
    assert "MAGI Soul" in on_disk


# -- auth gate ------------------------------------------------------------


def test_get_soul_without_cookie_is_403(soul_env):
    """``AdminOrAssignedGate`` rejects cookie-less callers.

    Note: ``AdminGate`` (used by the rest of the admin
    surface) returns 401, but the soul gate returns 403
    because it's a *role* check, not a session check.
    The semantic shift matters: 401 = "who are you?" (log
    in); 403 = "I know who you are, you can't do this".
    """
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    r = c.get("/api/soul")
    assert r.status_code == 403
    assert r.json()["code"] == "auth.soul_edit_forbidden"


def test_put_soul_without_cookie_is_403(soul_env):
    """Same gate covers PUT."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    r = c.put("/api/soul", json={"content": "anything"})
    assert r.status_code == 403


# -- role-based gate ------------------------------------------------------
#
# Spec: ``admin`` and ``assigned`` can read/write SOUL.md;
# ``employee`` / ``guest`` get 403. The fixture's default
# admin covers the happy paths above; these cases pin the
# role whitelist so a future "let everyone edit" slip is
# caught.


def _client_with_role(soul_env, *, role: str, chat_id: int):
    """Build a TestClient whose cookie resolves to an
    employee with the requested ``role``."""
    state_dir, _workspace = soul_env
    from magi.agent.db import (
        Employee,
        init_orm,
        open_session,
    )

    init_orm(str(state_dir))
    with open_session() as s:
        s.query(Employee).delete()
        s.add(
            Employee(
                name=f"TA-{role}",
                telegram_id=chat_id,
                role=role,
                provider="minimax",
                api_key="fake",
            )
        )
        s.commit()

    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", str(chat_id))
    return c


def test_assigned_role_can_read_soul(soul_env):
    c = _client_with_role(soul_env, role="assigned", chat_id=8002)
    r = c.get("/api/soul")
    assert r.status_code == 200


def test_assigned_role_can_write_soul(soul_env):
    c = _client_with_role(soul_env, role="assigned", chat_id=8002)
    r = c.put("/api/soul", json={"content": "assigned employee persona"})
    assert r.status_code == 200


def test_assigned_role_can_reset_soul(soul_env):
    c = _client_with_role(soul_env, role="assigned", chat_id=8002)
    r = c.post("/api/soul/reset")
    assert r.status_code == 200


def test_employee_role_cannot_read_soul(soul_env):
    c = _client_with_role(soul_env, role="employee", chat_id=8003)
    r = c.get("/api/soul")
    assert r.status_code == 403


def test_employee_role_cannot_write_soul(soul_env):
    c = _client_with_role(soul_env, role="employee", chat_id=8003)
    r = c.put("/api/soul", json={"content": "nope"})
    assert r.status_code == 403


def test_guest_role_cannot_write_soul(soul_env):
    c = _client_with_role(soul_env, role="guest", chat_id=8004)
    r = c.put("/api/soul", json={"content": "nope"})
    assert r.status_code == 403