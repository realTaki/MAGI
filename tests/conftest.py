"""Shared pytest fixtures + test-mode hooks.

The test suite pre-dates the signed-cookie layer in
:meth:`magi.channels.api.auth._sign_uid`. Many
fixtures set ``c.cookies.set("magi_session", "1")`` with a
naked int — production rejects this (good) but tests need
it to work transparently.

This conftest installs a *test-only* override of
``auth._verify_signed_uid`` so the cookie verification
accepts an unsigned ``str(int)`` value. Production code
(``_sign_uid``) is untouched.

Lazy install
------------

``pytest_configure`` runs very early in pytest startup;
importing ``magi.channels.api.auth`` here would pull
in the ``telegram`` channel module (and its third-party
deps). We therefore wrap the import in a try/except so a
test environment without ``python-telegram-bot`` can still
run. The autouse session fixture installs on first test
run, after collection is finished.
"""

from __future__ import annotations

import pytest


_PATCH_INSTALLED = False


def _install_signed_uid_relaxation() -> bool:
    """Idempotently replace ``auth._verify_signed_uid`` with
    a test-mode version that accepts naked ``str(int)``
    cookies.

    Returns True if applied; False if the ``auth`` module
    could not be imported (no webui channel wired in this
    test — that's fine).
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return True

    try:
        from magi.channels.api import auth as _auth
    except Exception:
        return False

    if getattr(_auth._verify_signed_uid, "_test_relaxed", False):
        _PATCH_INSTALLED = True
        return True

    original_verify = _auth._verify_signed_uid

    def relaxed_verify(token: str):
        uid = original_verify(token)
        if uid is not None:
            return uid
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

    relaxed_verify._test_relaxed = True  # type: ignore[attr-defined]
    _auth._verify_signed_uid = relaxed_verify  # type: ignore[assignment]
    _PATCH_INSTALLED = True
    return True


@pytest.fixture(autouse=True, scope="session")
def _install_signed_uid_relaxation_fixture():
    """Best-effort install before any test runs. Catches
    the early-shutdown path where pytest_configure's
    import errored out (e.g. the telegram channel isn't
    importable in this environment)."""
    _install_signed_uid_relaxation()
    yield


def pytest_configure(config):
    """Best-effort install at collection time — silent
    no-op if the auth module can't be imported yet (e.g.
    the telegram third-party dep isn't installed)."""
    _install_signed_uid_relaxation()
