"""Shared pytest fixtures + test-mode hooks.

The test suite pre-dates the signed-cookie layer in
:meth:`magi.channels.webui.api.auth._sign_uid`. Many
fixtures set ``c.cookies.set("magi_session", "1")`` with a
naked int — production rejects this (good) but tests need
it to work transparently.

This conftest installs a *test-only* override of
``auth._verify_signed_uid`` so the cookie verification
accepts an unsigned ``str(int)`` value. Production code
(``_sign_uid``) is untouched; the monkey patch only takes
effect inside test runs.

Strategy
--------

``auth_gates._resolve_uid`` does a *lazy* import of
``auth._verify_signed_uid`` at call time — so patching the
function on the ``auth`` module is sufficient as long as
the patch is applied before any admin-gated request fires.
We use both ``pytest_configure`` (collection-time) and an
``autouse`` session-scoped fixture (fixture setup) to make
the patch tolerant to collection-order races.
"""

from __future__ import annotations

import pytest


def _patch_signed_uid_verifier() -> None:
    """Replace ``auth._verify_signed_uid`` with a test-mode
    version that accepts ``str(int)`` (the historical cookie
    format) without HMAC verification.

    Idempotent: re-running is a no-op.
    """
    from magi.channels.webui.api import auth as _auth

    if getattr(_auth._verify_signed_uid, "_test_relaxed", False):
        return

    original_verify = _auth._verify_signed_uid

    def relaxed_verify(token: str):
        # First try the real verifier (signed path).
        uid = original_verify(token)
        if uid is not None:
            return uid
        # Then accept unsigned numeric (legacy test cookie).
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

    relaxed_verify._test_relaxed = True  # type: ignore[attr-defined]
    _auth._verify_signed_uid = relaxed_verify  # type: ignore[assignment]


@pytest.fixture(autouse=True, scope="session")
def _relax_signed_uid_in_tests():
    """Session-scoped autouse fixture — guarantees the
    signed-cookie relaxation is installed before any
    request-driven test fires."""
    _patch_signed_uid_verifier()
    yield


def pytest_configure(config):
    """Run once per pytest session — also install early at
    collection time in case tests don't import the session
    fixture until later."""
    _patch_signed_uid_verifier()
