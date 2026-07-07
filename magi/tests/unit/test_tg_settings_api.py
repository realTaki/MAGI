"""Tests for the TG channel settings API + config module.

Two surfaces under test:

  - ``magi.channels.telegram.config.get_read_reaction_emoji``
    / ``set_read_reaction_emoji``: read/write the meta key,
    default fallback, allowlist enforcement.

  - ``GET /api/tg-settings/read-reaction`` /
    ``PUT /api/tg-settings/read-reaction``: the WebUI
    surface; returns the current + the radio group, validates
    ``emoji`` against the allowlist, persists via the
    config module.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def tg_settings_env(monkeypatch, tmp_path):
    """Fresh state dir per test. The config module reads
    ``MAGI_STATE_DIR`` via :mod:`magi.agent.state.settings`,
    so a tmp dir keeps tests isolated and idempotent.

    Calls :func:`init_sqlite` (not just :func:`init_orm`)
    because the config module's :func:`state_get` reads the
    ``settings`` meta table — that table is created by
    ``init_sqlite``, not ``init_orm``.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    from magi.agent.state import init_sqlite
    init_sqlite(str(state))
    return state


@pytest.fixture
def client(tg_settings_env):
    """TestClient with an admin cookie; mirrors the other
    settings-API fixtures in this suite."""
    from magi.agent.state.orm import (
        Employee,
        init_orm,
        open_session,
    )

    init_orm(str(tg_settings_env))
    with open_session() as s:
        s.query(Employee).delete()
        s.add(
            Employee(
                name="TA-tg-settings",
                telegram_id=9001,
                role="admin",
                provider="minimax",
                api_key="fake",
            )
        )
        s.commit()

    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    c = TestClient(app)
    c.cookies.set("magi_session", "9001")
    return c


# -- config module --------------------------------------------------------


def test_config_default_when_unset(tg_settings_env):
    """No prior write → ``DEFAULT_REACTION_EMOJI`` (👀)."""
    from magi.channels.telegram.config import (
        DEFAULT_REACTION_EMOJI,
        get_read_reaction_emoji,
    )

    assert get_read_reaction_emoji(str(tg_settings_env)) == DEFAULT_REACTION_EMOJI


def test_config_round_trip(tg_settings_env):
    """Set then read returns the same value."""
    from magi.channels.telegram.config import (
        get_read_reaction_emoji,
        set_read_reaction_emoji,
    )

    set_read_reaction_emoji(str(tg_settings_env), "🤝")
    assert get_read_reaction_emoji(str(tg_settings_env)) == "🤝"


def test_config_falls_back_on_unknown_value(tg_settings_env):
    """A hand-edited / corrupted meta key falls back to the
    default rather than crashing the inbound handler.
    """
    from magi.channels.telegram.config import (
        DEFAULT_REACTION_EMOJI,
        get_read_reaction_emoji,
    )
    from magi.agent.state.settings import state_set

    # ✅ is in the Unicode block but NOT in our user-facing
    # choices (Telegram rejects it as a reaction type) —
    # the fallback path catches this so a leftover from a
    # previous config version doesn't silently 400 every
    # inbound message.
    state_set(str(tg_settings_env), "tg.read_reaction_emoji", "✅")
    assert get_read_reaction_emoji(str(tg_settings_env)) == DEFAULT_REACTION_EMOJI


def test_config_rejects_emoji_not_in_tg_whitelist(tg_settings_env):
    """The actual Telegram whitelist is the source of truth:
    even if a value is in our user-facing list, if Telegram
    doesn't accept it, the SDK will 400. Pinned here so
    anyone adding a new choice must verify against
    ``telegram.constants.ReactionEmoji`` first.

    The list is small (~70 entries); our 5 picks must all
    appear in it. If Telegram ever drops one (very rare),
    this test starts failing and we know to swap.
    """
    from telegram.constants import ReactionEmoji
    from magi.channels.telegram.config import REACTION_CHOICES

    tg_allowed = {e.value for e in ReactionEmoji}
    for value, _label in REACTION_CHOICES:
        assert value in tg_allowed, (
            f"REACTION_CHOICES value {value!r} not in Telegram's "
            "ReactionEmoji whitelist — pick a different emoji."
        )


def test_config_reaction_choices_distinct():
    """The 5 emoji in ``REACTION_CHOICES`` are unique (used
    as radio values, so duplicates would render twice)."""
    from magi.channels.telegram.config import REACTION_CHOICES

    values = [v for v, _ in REACTION_CHOICES]
    assert len(values) == len(set(values))
    assert len(values) == 5  # pin the surface


def test_config_reaction_choices_human_labels_present():
    """Every choice has a non-empty label (used in the radio
    row's ``<span>``); an empty label would render as a
    blank radio button."""
    from magi.channels.telegram.config import REACTION_CHOICES

    for value, label in REACTION_CHOICES:
        assert label and label.strip(), f"label for {value!r} is empty"
        # Labels start with the emoji itself for visual
        # pairing with the radio value.
        assert label.startswith(value), (
            f"label for {value!r} doesn't start with the emoji"
        )


# -- API surface ----------------------------------------------------------


def test_get_returns_current_and_choices(client):
    r = client.get("/api/tg-settings/read-reaction")
    assert r.status_code == 200
    data = r.json()
    assert "current" in data
    assert "default" in data
    assert "choices" in data
    # No prior write → current == default.
    assert data["current"] == data["default"]
    assert len(data["choices"]) == 5


def test_put_persists_and_returns_new_value(client, tg_settings_env):
    """``PUT`` saves through the config module and echoes
    the new current."""
    r = client.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": "🤝"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["current"] == "🤝"

    # Subprocess-equivalent: a fresh ``get_read_reaction_emoji``
    # call reads the same value back.
    from magi.channels.telegram.config import get_read_reaction_emoji
    assert get_read_reaction_emoji(str(tg_settings_env)) == "🤝"


def test_put_rejects_unicode_that_isnt_in_tg_reaction_whitelist(client):
    """🦄 is in ``ReactionEmoji`` but not in our user-facing
    choices list — covered by the first assertion below.

    💬 looks like a great fit semantically ("about to reply")
    but Telegram's ``ReactionEmoji`` whitelist doesn't include
    it; passing it to ``set_message_reaction`` would route
    to ``ReactionTypeCustomEmoji`` and 400. Our user-facing
    allowlist excludes it before the SDK ever sees it."""
    # Outside our 5 user-facing choices (🦄 is in the TG
    # whitelist but not on the radio group).
    r = client.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": "🦄"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "validation.unknown_reaction_emoji"

    # 💬 specifically: in our user-facing list historically
    # but moved out because Telegram doesn't accept it.
    r = client.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": "💬"},
    )
    assert r.status_code == 400

    # Same for ✅.
    r = client.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": "✅"},
    )
    assert r.status_code == 400


def test_put_rejects_empty_emoji(client):
    """Empty string is below Pydantic's ``min_length=1``
    → 422 (Pydantic's validation, before our allowlist)."""
    r = client.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": ""},
    )
    assert r.status_code == 422


def test_get_requires_admin(client):
    """Cookie-less caller → 401."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    bare = TestClient(create_app())
    r = bare.get("/api/tg-settings/read-reaction")
    assert r.status_code == 401


def test_put_requires_admin(client):
    """Same gate on PUT."""
    from magi.channels.webui.app import create_app
    from fastapi.testclient import TestClient

    bare = TestClient(create_app())
    r = bare.put(
        "/api/tg-settings/read-reaction",
        json={"emoji": "👀"},
    )
    assert r.status_code == 401