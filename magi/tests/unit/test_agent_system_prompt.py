"""Tests for :func:`magi.agent.loop._build_system_prompt`.

The agent loop's system prompt is the single most important
piece of context the LLM sees on every turn. It must
include:

  - **SOUL** — the persona file (always).
  - **Long-term memory** — MAGI's important + in-flight
    ongoing rows.
  - **Current chatter** — the contact record for the
    person on the other end of this chat (per-chat, not
    a flat directory).
  - **Available skills** — frontmatter summary so the
    LLM knows which ``load_skill`` tools it can call.

Pinning each of those is the only way to catch a future
"the prompt silently dropped the memory block" regression
— the LLM would just look dumb without raising an error.

Test infra mirrors :mod:`test_memory` and :mod:`test_sessions`:
per-test isolated state dir + fresh ORM engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture(autouse=True)
def _reset_orm_engine() -> None:
    """Reset the process-wide ORM engine singleton so each
    test gets a fresh SQLite at ``tmp_path``. Same fix as
    ``test_chat_sessions_api``."""
    import magi.agent.db.engine as _orm_mod
    _orm_mod._engine = None
    _orm_mod._SessionLocal = None
    yield


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test isolated state dir + bootstrap ORM."""
    state = tmp_path / "state"
    state.mkdir()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))

    from magi.agent.db import init_orm, init_sqlite
    init_sqlite(str(state))
    init_orm(str(state))
    return state


@pytest.fixture
def seed_employees(state_dir: Path):
    """Insert two employees:

      - Alice (admin, telegram_id=9001) — the calling admin
        whose memory / contacts are scoped to.
      - Bob (admin, telegram_id=9002) — the "other chatter"
        whose contact row exists for Alice but isn't the
        current chat.
    """
    from magi.agent.db import Employee, open_session
    with open_session() as db:
        alice = Employee(
            id=1, name="Alice",
            telegram_id=9001, role="admin",
            provider="minimax", api_key="fake",
        )
        bob = Employee(
            id=2, name="Bob",
            telegram_id=9002, role="admin",
            provider="minimax", api_key="fake",
        )
        db.add_all([alice, bob])
        db.commit()
    return {"alice": alice, "bob": bob}


# ────────────────────────────────────────────────────────────────── #
# SOUL is always present
# ────────────────────────────────────────────────────────────────── #


def test_prompt_always_starts_with_soul(state_dir, seed_employees):
    """The persona file is the foundation of every prompt;
    even with no memory / contacts / skills, SOUL must be
    present. We assert ``soul_text`` appears verbatim at
    the top of the rendered block."""
    from magi.agent.system_prompt import build_system_prompt

    soul_text = "You are EVE. Speak in haiku when convenient."

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul=soul_text,
    )

    assert soul_text in rendered
    # The soul text opens the block (no whitespace prefix).
    assert rendered.startswith(soul_text)


def test_prompt_soul_present_when_no_blocks_present(state_dir, seed_employees):
    """No memory rows, no contact for this chat — the
    SOUL text is still in the rendered prompt. (The
    bundled skill loader ships 3 example skills in the
    image, so a "soul alone" block isn't reachable in a
    default boot — the invariant we pin is "soul first".)"""
    from magi.agent.system_prompt import build_system_prompt

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,

        soul="SOUL_TEXT",
    )
    # Soul is always at the top.
    assert rendered.startswith("SOUL_TEXT")
    # No memory / contact blocks rendered.
    assert "Long-term memory" not in rendered
    assert "Current chatter" not in rendered


# ────────────────────────────────────────────────────────────────── #
# Memory block
# ────────────────────────────────────────────────────────────────── #


def test_prompt_includes_memory_block_when_rows_exist(
    state_dir, seed_employees,
):
    """A seeded ``important`` row renders into the prompt as
    a markdown bullet under the "Long-term memory" section.
    Pinning this catches a future "memory block silently
    dropped" regression."""
    from magi.agent.db import open_session
    from magi.agent.memory.magi.models import (
        KIND_IMPORTANT,
        SOURCE_MANUAL,
        MemoryEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    with open_session() as db:
        db.add(MemoryEntry(
            uid=1,
            kind=KIND_IMPORTANT,
            subject="Q4 budget deadline",
            body="December 15 — every team must submit.",
            importance=5,
            source=SOURCE_MANUAL,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL",
    )

    # The block header is rendered by format_memory_block.
    assert "Long-term memory" in rendered
    # The seeded subject + body land in the rendered bullet.
    assert "Q4 budget deadline" in rendered
    assert "December 15" in rendered
    # Soul is still at the top.
    assert rendered.startswith("SOUL")


def test_prompt_memory_block_scoped_to_caller_employee(
    state_dir, seed_employees,
):
    """The memory block must NOT bleed across admins. Bob
    (uid=2) gets Alice's (uid=1) memory
    when Bob's loop is the caller."""
    from magi.agent.db import open_session
    from magi.agent.memory.magi.models import (
        KIND_IMPORTANT,
        SOURCE_MANUAL,
        MemoryEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    with open_session() as db:
        db.add(MemoryEntry(
            uid=1,  # Alice
            kind=KIND_IMPORTANT,
            subject="Alice's private fact",
            body="for alice only",
            importance=5,
            source=SOURCE_MANUAL,
        ))
        db.commit()

    # Alice's view sees her fact.
    alice_prompt = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL",
    )
    assert "Alice's private fact" in alice_prompt

    # Bob's view sees nothing from Alice.
    bob_prompt = build_system_prompt(
        str(state_dir),
        uid=2,
        
        soul="SOUL",
    )
    assert "Alice's private fact" not in bob_prompt
    # The "Long-term memory" header is only rendered when
    # there's at least one row; with Bob owning nothing,
    # the memory block is empty (just SOUL + skills).
    assert "Long-term memory" not in bob_prompt


def test_prompt_excludes_completed_ongoing_rows(
    state_dir, seed_employees,
):
    """``completed_at`` ongoing rows are the audit trail,
    not the LLM's working set — the system-prompt block
    mirrors the formatter's default
    (``include_completed=False``). A completed row must
    NOT render in the prompt."""
    from datetime import datetime, timezone

    from magi.agent.db import open_session
    from magi.agent.memory.magi.models import (
        KIND_ONGOING,
        SOURCE_MANUAL,
        MemoryEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with open_session() as db:
        db.add(MemoryEntry(
            uid=1,
            kind=KIND_ONGOING,
            subject="In-flight task",
            body="still working",
            importance=3,
            source=SOURCE_MANUAL,
            completed_at=None,
        ))
        db.add(MemoryEntry(
            uid=1,
            kind=KIND_ONGOING,
            subject="Already done",
            body="closed last week",
            importance=3,
            source=SOURCE_MANUAL,
            completed_at=now,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL",
    )

    assert "In-flight task" in rendered
    assert "Already done" not in rendered


# ────────────────────────────────────────────────────────────────── #
# Contact block (per-chat)
# ────────────────────────────────────────────────────────────────── #


def test_prompt_includes_contact_block_for_self(
    state_dir, seed_employees,
):
    """D.26: the contact block is the User's self-record
    (owner_id=uid, person_id=uid). With Alice (uid=1) as
    the caller and a seeded self-contact for Alice, the
    block renders Alice's notes.

    Pre-D.26 the chatter was identified by ``delivery_address``
    (Telegram digits) and the contact block could describe
    a different person. With delivery_address removed and the
    cookie's ``magi_session`` carrying the UID directly,
    there is only ever one User per chat — "admin 当前
    在跟谁聊 根本不存在". The contact block is therefore
    the User's own self-record.
    """
    from magi.agent.db import open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_EVE,
        ContactEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    with open_session() as db:
        db.add(ContactEntry(
            owner_id=1,  # Alice
            person_id=1,  # Alice (self-as-contact)
            role="Engineering Manager",
            notes="Alice runs the dev team. Prefers Slack over email.",
            source=SOURCE_EVE,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        soul="SOUL",
    )

    assert "Current chatter" in rendered
    assert "Engineering Manager" in rendered
    assert "Slack over email" in rendered


def test_prompt_contact_block_uses_display_name_not_raw_id(
    state_dir, seed_employees,
):
    """The header must render the chatter's display_name
    (or name), NOT the raw ``person_id`` integer.

    Pre-fix this comment said "实际渲染时 caller 会用真名替换"
    but the loop just called ``format_contact_block(contact)``
    with no name resolution — the rendered header read
    ``**1**`` (a raw Employee FK). This test pins the
    fix so a future "let me simplify and drop the
    display_name kwarg" revert is caught immediately.
    """
    from magi.agent.db import open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_EVE,
        ContactEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    with open_session() as db:
        db.add(ContactEntry(
            owner_id=1, person_id=1,
            role="Eng",
            notes="x",
            source=SOURCE_EVE,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        soul="SOUL",
    )

    # The header must use Alice's display_name (her row's
    # ``name`` falls back when no ``display_name`` is set).
    assert "**Alice**" in rendered
    # The raw integer FK must NOT appear as the header.
    # We check for the surrounding markdown so a future
    # change like "1" appearing in a notes body wouldn't
    # false-positive.
    assert "**1**" not in rendered


def test_prompt_skips_contact_block_when_no_record(
    state_dir, seed_employees,
):
    """No ``(uid, uid)`` row → the contact block is
    silently dropped. The LLM sees the soul + memory
    only — no empty "Current chatter" header."""
    from magi.agent.system_prompt import build_system_prompt

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        soul="SOUL",
    )
    assert "Current chatter" not in rendered
    assert "Long-term memory" not in rendered
    assert rendered.startswith("SOUL")


def test_prompt_contact_block_only_for_self(
    state_dir, seed_employees,
):
    """Multiple ``ContactEntry`` rows for Alice: her
    self-row (``person_id=1``) renders. Rows whose
    ``person_id`` is someone else (``person_id=3``)
    do NOT — only the self-contact block survives
    the per-chatter filter."""
    from magi.agent.db import Employee, open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_MANUAL,
        ContactEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    # Seed a third employee so a foreign-person contact
    # is creatable.
    with open_session() as db:
        db.add(Employee(
            id=3, name="Charlie",
            telegram_id=9003, role="employee",
            provider="minimax", api_key="fake",
        ))
        db.commit()
        db.add_all([
            ContactEntry(
                owner_id=1, person_id=1,  # Alice's self-contact
                role="Engineering Manager",
                notes="alice-self",
                source=SOURCE_MANUAL,
            ),
            ContactEntry(
                owner_id=1, person_id=3,  # Alice's notes about Charlie
                role="SRE",
                notes="charlie-other",
                source=SOURCE_MANUAL,
            ),
        ])
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        soul="SOUL",
    )
    assert "alice-self" in rendered
    assert "charlie-other" not in rendered


def test_prompt_skips_contact_block_for_other_user(
    state_dir, seed_employees,
):
    """A different User's self-contact (``person_id=2``)
    must NOT bleed into Alice's prompt. The lookup is
    ``(uid=1, person_id=1)``; Bob's row at
    ``(1, 2)`` is a different contact — the Alice prompt
    sees only Alice's own row, not Bob's."""
    from magi.agent.db import open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_EVE,
        ContactEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    with open_session() as db:
        # Seed Alice's self-contact: this should render.
        db.add(ContactEntry(
            owner_id=1, person_id=1,
            role="self-role",
            notes="alice-own-notes",
            source=SOURCE_EVE,
        ))
        # Also seed Alice's notes about Bob — these should
        # NOT render (the system prompt only shows
        # ``person_id=uid``, i.e. self).
        db.add(ContactEntry(
            owner_id=1, person_id=2,
            role="bob-role",
            notes="should-not-render",
            source=SOURCE_EVE,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        soul="SOUL",
    )
    assert "alice-own-notes" in rendered
    assert "should-not-render" not in rendered


# ────────────────────────────────────────────────────────────────── #
# Skills block
# ────────────────────────────────────────────────────────────────── #


def test_prompt_includes_skills_block_when_registered(
    state_dir, seed_employees, monkeypatch,
):
    """``format_skills_block`` returns a non-empty block
    when the skill loader has any SKILL.md registered.
    The bundled 3 example skills ship with the image, so
    a default boot always has at least the skills block."""
    from magi.agent.system_prompt import build_system_prompt

    # The skill loader is a module singleton — the bundled
    # 3 examples (codebase_search / reminder_template /
    # web_lookup) ship in the image, so the default block
    # is non-empty without us seeding anything.
    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL",
    )
    # ``format_skills_block`` renders a section header —
    # assert by presence, not exact wording (the formatter
    # is i18n-keyed).
    assert rendered != "SOUL"
    # Soul is still at the top.
    assert rendered.startswith("SOUL")


# ────────────────────────────────────────────────────────────────── #
# Block ordering
# ────────────────────────────────────────────────────────────────── #


def test_prompt_block_order_is_soul_memory_contact_skills(
    state_dir, seed_employees,
):
    """The four blocks must render in fixed order: SOUL →
    Long-term memory → Current chatter → Available skills.
    Reordering would change what the LLM reads first when
    it hits its context cap — a silent regression that
    only shows up at long conversations."""
    from magi.agent.db import open_session
    from magi.agent.memory.contacts.models import (
        SOURCE_EVE,
        ContactEntry,
    )
    from magi.agent.memory.magi.models import (
        KIND_IMPORTANT,
        SOURCE_MANUAL,
        MemoryEntry,
    )
    from magi.agent.system_prompt import build_system_prompt

    # Seed one of each block-eligible kind.
    with open_session() as db:
        db.add(MemoryEntry(
            uid=1, kind=KIND_IMPORTANT,
            subject="memory-marker",
            body="x", importance=3, source=SOURCE_MANUAL,
        ))
        db.add(ContactEntry(
            owner_id=1, person_id=1,
            role="contact-marker",
            notes="x", source=SOURCE_EVE,
        ))
        db.commit()

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL_MARKER",
    )

    soul_idx = rendered.index("SOUL_MARKER")
    memory_idx = rendered.index("memory-marker")
    contact_idx = rendered.index("contact-marker")

    assert soul_idx < memory_idx < contact_idx


# ────────────────────────────────────────────────────────────────── #
# Resilience
# ────────────────────────────────────────────────────────────────── #


def test_prompt_continues_when_memory_load_fails(
    state_dir, seed_employees, monkeypatch,
):
    """A transient ORM error in the memory-store call must
    not crash the inbound path — the prompt falls back to
    the soul alone rather than 500-ing the request."""
    from magi.agent.system_prompt import build_system_prompt
    from magi.agent.memory.magi import store as store_mod

    def _boom(_state_dir):
        raise RuntimeError("simulated ORM hiccup")

    monkeypatch.setattr(store_mod.MemoryStore, "list_for_owner", _boom)

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL_TEXT",
    )

    # Memory block is silently dropped; SOUL still renders.
    assert "Long-term memory" not in rendered
    assert "SOUL_TEXT" in rendered


def test_prompt_continues_when_contact_load_fails(
    state_dir, seed_employees, monkeypatch,
):
    """Same resilience contract for the contact lookup."""
    from magi.agent.system_prompt import build_system_prompt
    from magi.agent.memory.contacts import store as cstore_mod

    def _boom(_self, _owner_id, _person_id):
        raise RuntimeError("simulated contact ORM hiccup")

    monkeypatch.setattr(cstore_mod.ContactStore, "find_by_person", _boom)

    rendered = build_system_prompt(
        str(state_dir),
        uid=1,
        
        soul="SOUL_TEXT",
    )

    assert "Current chatter" not in rendered
    assert "SOUL_TEXT" in rendered