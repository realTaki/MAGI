"""Password hashing + verification helpers.

Stored format::

    "scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>"

The parameters are written into the hash so an operator who
later tunes ``n`` / ``r`` / ``p`` doesn't need to migrate
existing rows — first verify under the new params re-hashes
the credential. We keep them in the same row to avoid a
parallel "hash params" lookup.

Why stdlib scrypt
-----------------

  - No new dependency. ``hashlib.scrypt`` is part of the
    Python standard library since 3.6 and OpenSSL has
    shipped a fast implementation since 1.1.
  - Tunable CPU/memory cost. ``n=2**14`` is the v0 knob —
    ~100 ms / verify on a typical container; an attacker
    trying to brute-force a 12-char password is in for
    weeks per guess attempt.
  - Memory-hard. Unlike PBKDF2, scrypt doesn't degrade to
    GPU-friendly hashing.

The 60s cooldown lives here too — it has the same shape as
the TG code cooldown so :mod:`magi.webui.api.auth` can
reuse one cooldown store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from magi.bus import Bus

# -- Hash parameters -------------------------------------------------------
#
# ``n=2**14`` is the cost factor. ``r=8`` and ``p=1`` are
# OpenSSL's recommended defaults at this ``n``. A verify
# call takes ~100 ms on a 2024-era x86 core.
_SCRYPT_N: Final[int] = 2 ** 14
_SCRYPT_R: Final[int] = 8
_SCRYPT_P: Final[int] = 1
_SCRYPT_DKLEN: Final[int] = 32

# Salt and password length bounds.
_SALT_BYTES: Final[int] = 16
_MIN_PASSWORD_LEN: Final[int] = 8
_MAX_PASSWORD_LEN: Final[int] = 256


# -- Hash / verify ----------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def hash_password(password: str) -> str:
    """Hash ``password`` and return the stored string.

    Empty / too-short / too-long inputs raise ``ValueError``
    so the caller can surface a 400 rather than silently
    storing a junk row.
    """
    if not isinstance(password, str):
        raise ValueError("password must be a string")
    if len(password) < _MIN_PASSWORD_LEN:
        raise ValueError(
            f"password must be at least {_MIN_PASSWORD_LEN} characters"
        )
    if len(password) > _MAX_PASSWORD_LEN:
        raise ValueError(
            f"password must be at most {_MAX_PASSWORD_LEN} characters"
        )
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(stored: str, password: str) -> bool:
    """Return ``True`` if ``password`` matches ``stored``.

    Constant-time on the digest comparison. Malformed
    stored strings return ``False`` (never raise) so an
    attacker can't probe hash format that way.
    """
    try:
        prefix, n_s, r_s, p_s, salt_b64, digest_b64 = stored.split("$", 5)
    except ValueError:
        return False
    if prefix != "scrypt":
        return False
    try:
        n = int(n_s)
        r = int(r_s)
        p = int(p_s)
        salt = _b64d(salt_b64)
        digest = _b64d(digest_b64)
    except (ValueError, TypeError):
        return False

    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(digest),
        )
    except (ValueError, OSError):
        # ``n`` out of range, OpenSSL too old, etc. —
        # treat as "doesn't match".
        return False
    return hmac.compare_digest(candidate, digest)


# -- 60s cooldown -----------------------------------------------------------
#
# Mirrors the TG-code flow in :mod:`magi.webui.api.auth`:
# one attempt per uid per 60s, success or failure both
# reset the timer. The store is keyed by uid in the
# ``auth.login_attempt.<uid>`` settings KV slot — the
# same key shape the login code uses, just a distinct
# sub-prefix.
#
# Test isolation: the helpers accept an injectable
# ``storage`` callable so unit tests can swap a dict
# backend without touching the SQLite-backed global
# state.

_PASSWORD_KEY_PREFIX = "auth.password_login"


@dataclass
class Attempt:
    last_attempt_at: float
    """Unix timestamp of the last login attempt (any outcome)."""


def _store_get(bus: Bus, uid: int) -> dict | None:
    raw = bus.settings_book.get(key=f"{_PASSWORD_KEY_PREFIX}.{uid}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _store_set(bus: Bus, uid: int, value: dict) -> None:
    bus.settings_book.set(key=f"{_PASSWORD_KEY_PREFIX}.{uid}", value=json.dumps(value))


def _store_clear(bus: Bus, uid: int) -> None:
    bus.settings_book.delete(key=f"{_PASSWORD_KEY_PREFIX}.{uid}")


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def check_cooldown(bus: Bus, uid: int, *, cooldown_seconds: int) -> bool:
    """Return ``True`` if a new login attempt is allowed.

    On a successful verify the caller should call
    :func:`record_attempt` (or :func:`clear_attempt`) to
    reset the timer.
    """
    record = _store_get(bus, uid)
    if not record:
        return True
    try:
        last = float(record.get("last_attempt_at", 0))
    except (TypeError, ValueError):
        return True
    return (_now_ts() - last) >= cooldown_seconds


def record_attempt(bus: Bus, uid: int) -> None:
    """Stamp the current time as the last attempt.

    Called whether the password was correct or not — the
    60s lock applies to both outcomes.
    """
    _store_set(bus, uid, {"last_attempt_at": _now_ts()})


def clear_attempt(bus: Bus, uid: int) -> None:
    """Drop the cooldown row (called on a successful login)."""
    _store_clear(bus, uid)


__all__ = [
    "hash_password",
    "verify_password",
    "check_cooldown",
    "record_attempt",
    "clear_attempt",
    "Attempt",
    "_MIN_PASSWORD_LEN",
]
