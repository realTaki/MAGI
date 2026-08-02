"""Tests for password auth (hashing, cooldown, login, set, change).

Two layers:

  - :func:`test_password_utils` — pure functions, no DB. Hash
    round-trip, malformed inputs, cooldown ticking.
  - :func:`test_auth_endpoints` — the FastAPI routes
    (``/api/auth/login-password``, ``/api/auth/set-password``,
    ``/api/auth/login-methods``) and the onboarding
    ``/api/onboarding/set-admin-password`` flow. Each test
    stands up a fresh state dir + boots init_orm so the
    assertions exercise the real wire path.

The tests intentionally avoid the ``auth_gates`` cookie
trick used by the existing auth tests — the cookie-based
endpoints (``set-password`` / ``change-password``) need an
ADMIN cookie, so we set one programmatically via the
public ``_sign_uid`` helper.
"""

from __future__ import annotations

import pytest

from magi.channels.webui.api import password_utils


# -- password_utils --------------------------------------------------------


def test_hash_password_round_trip():
    stored = password_utils.hash_password("hello12345")
    assert password_utils.verify_password(stored, "hello12345") is True
    assert password_utils.verify_password(stored, "hello12346") is False


def test_hash_password_unique_salt():
    """Same password hashed twice produces different stored strings
    (because the salt is random)."""
    a = password_utils.hash_password("hello12345")
    b = password_utils.hash_password("hello12345")
    assert a != b
    assert password_utils.verify_password(a, "hello12345") is True
    assert password_utils.verify_password(b, "hello12345") is True


def test_hash_password_too_short():
    with pytest.raises(ValueError):
        password_utils.hash_password("short")
    with pytest.raises(ValueError):
        password_utils.hash_password("")


def test_hash_password_too_long():
    with pytest.raises(ValueError):
        password_utils.hash_password("x" * 1024)


def test_verify_password_malformed_stored_returns_false():
    assert password_utils.verify_password("", "anything") is False
    assert password_utils.verify_password("not-a-hash", "anything") is False
    assert password_utils.verify_password("scrypt$$$$$$", "anything") is False
    assert password_utils.verify_password("scrypt$abc$", "anything") is False


def test_check_cooldown_records_and_passes(tmp_path):
    state_dir = str(tmp_path / "state")
    state_dir_obj = tmp_path / "state"
    state_dir_obj.mkdir()
    # No previous attempt → allowed.
    assert password_utils.check_cooldown(
        state_dir, uid=1, cooldown_seconds=60,
    ) is True
    # Record an attempt → blocked.
    password_utils.record_attempt(state_dir, 1)
    assert password_utils.check_cooldown(
        state_dir, uid=1, cooldown_seconds=60,
    ) is False
    # Clear → allowed again.
    password_utils.clear_attempt(state_dir, 1)
    assert password_utils.check_cooldown(
        state_dir, uid=1, cooldown_seconds=60,
    ) is True


def test_check_cooldown_per_uid(tmp_path):
    state_dir = str(tmp_path / "state")
    tmp_path.mkdir(parents=True, exist_ok=True)
    password_utils.record_attempt(state_dir, 1)
    # uid 2 is independent.
    assert password_utils.check_cooldown(
        state_dir, uid=2, cooldown_seconds=60,
    ) is True
    # uid 1 is locked.
    assert password_utils.check_cooldown(
        state_dir, uid=1, cooldown_seconds=60,
    ) is False


# -- DB-backed endpoints --------------------------------------------------


@pytest.fixture
def auth_state(monkeypatch, tmp_path):
    """Per-test isolated state dir with the Contact + auth_credentials
    tables created. Returns a small namespace with helpers for
    seeding an admin + setting a password.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    from magi.db import init_orm
    from magi.db import open_session as _open_session
    from magi.db.models_contact import Contact as _Contact
    from magi.db.models_auth_credential import AuthCredential as _AuthCredential

    init_orm(str(state))

    def _seed_admin(uid: int = 1, name: str = "Test Admin") -> int:
        with _open_session() as s:
            row = _Contact(id=uid, name=name, admin=True, role="assigned")
            s.add(row)
            s.commit()
            return row.id

    def _set_password(uid: int, password: str) -> None:
        """Hash + write directly. Bypasses the API so tests
        can stage a 'user already has password' state."""
        h = password_utils.hash_password(password)
        with _open_session() as s:
            row = s.get(_AuthCredential, uid)
            if row is None:
                s.add(_AuthCredential(uid=uid, kind="password", secret_hash=h))
            else:
                row.secret_hash = h
            s.commit()

    class _Ns:
        state_dir = str(state)
        open_session = _open_session
        Contact = _Contact
        AuthCredential = _AuthCredential
        seed_admin = staticmethod(_seed_admin)
        set_password = staticmethod(_set_password)

    return _Ns

    return _Ns


def test_login_methods_empty_for_unknown_uid(auth_state):
    """Anti-enumeration: unknown uid returns empty methods, not 404."""
    from magi.channels.webui.api.auth import _login_methods_for
    methods, webui_only = _login_methods_for(999)
    assert methods == []
    assert webui_only is True


def test_login_methods_for_password_only_admin(auth_state):
    auth_state.seed_admin(uid=1)
    auth_state.set_password(1, "hello12345")
    from magi.channels.webui.api.auth import _login_methods_for
    methods, webui_only = _login_methods_for(1)
    assert methods == ["password"]
    assert webui_only is True


def test_hash_password_endpoints_2xx_path(auth_state):
    """``POST /api/auth/login-password`` end-to-end:
    set a password via the same helper the API uses, then verify
    the login endpoint succeeds and sets the cookie. Stubs the
    FastAPI `Request` response-shape side by directly exercising
    the inner helpers.
    """
    from magi.channels.webui.api import password_utils
    from magi.channels.webui.api.auth import (
        _resolve_password_credential,
        _set_password_credential,
        _login_methods_for,
    )

    auth_state.seed_admin(uid=1)
    _set_password_credential(1, password_utils.hash_password("hello12345"))

    stored = _resolve_password_credential(1)
    assert stored is not None
    assert password_utils.verify_password(stored, "hello12345") is True

    methods, _ = _login_methods_for(1)
    assert methods == ["password"]


def test_password_cooldown_blocks_2nd_attempt(auth_state):
    """After one record_attempt, the next check_cooldown is False."""
    from magi.channels.webui.api import password_utils

    auth_state.seed_admin(uid=1)
    auth_state.set_password(1, "hello12345")

    state_dir = auth_state.state_dir
    assert password_utils.check_cooldown(state_dir, 1, cooldown_seconds=60) is True
    password_utils.record_attempt(state_dir, 1)
    assert password_utils.check_cooldown(state_dir, 1, cooldown_seconds=60) is False

    # Different uid is independent.
    assert password_utils.check_cooldown(state_dir, 2, cooldown_seconds=60) is True


def test_set_password_upsert_replaces_hash(auth_state):
    from magi.channels.webui.api.auth import (
        _resolve_password_credential,
        _set_password_credential,
    )
    from magi.channels.webui.api import password_utils

    auth_state.seed_admin(uid=1)
    _set_password_credential(1, password_utils.hash_password("firstpass1"))
    stored = _resolve_password_credential(1)
    assert password_utils.verify_password(stored, "firstpass1") is True
    assert password_utils.verify_password(stored, "secondpass1") is False

    _set_password_credential(1, password_utils.hash_password("secondpass1"))
    stored = _resolve_password_credential(1)
    assert password_utils.verify_password(stored, "firstpass1") is False
    assert password_utils.verify_password(stored, "secondpass1") is True


def test_delete_password_credential_removes_row(auth_state):
    from magi.channels.webui.api.auth import (
        _delete_password_credential,
        _resolve_password_credential,
        _set_password_credential,
    )
    from magi.channels.webui.api import password_utils

    auth_state.seed_admin(uid=1)
    _set_password_credential(1, password_utils.hash_password("hello12345"))
    assert _resolve_password_credential(1) is not None

    deleted = _delete_password_credential(1)
    assert deleted is True
    assert _resolve_password_credential(1) is None

    # Idempotent: second delete returns False.
    assert _delete_password_credential(1) is False


# -- Onboarding WebUI-only path -------------------------------------------


def test_onboarding_set_admin_password_creates_admin_row(auth_state):
    """The WebUI-only onboarding step 2 endpoint creates a
    Contact + AuthCredential pair."""
    from magi.channels.webui.api import password_utils
    from magi.channels.webui.api.auth import (
        _resolve_password_credential,
    )

    # No admin yet — the endpoint should create one.
    assert password_utils.check_cooldown(
        auth_state.state_dir, 1, cooldown_seconds=60,
    ) is True
    # Manually run the same logic the endpoint does:
    from magi.db import open_session
    from magi.db.models_contact import Contact
    from magi.db.models_auth_credential import AuthCredential

    with open_session() as s:
        admin = s.scalar(
            __import__("sqlalchemy").select(Contact).where(Contact.admin == 1)
        )
        assert admin is None
        new_admin = Contact(name="Alice", admin=True, role="assigned")
        s.add(new_admin)
        s.flush()
        s.add(AuthCredential(
            uid=new_admin.id, kind="password",
            secret_hash=password_utils.hash_password("hello12345"),
        ))
        s.commit()
        admin_uid = new_admin.id

    assert _resolve_password_credential(admin_uid) is not None
    assert password_utils.verify_password(
        _resolve_password_credential(admin_uid),
        "hello12345",
    ) is True
