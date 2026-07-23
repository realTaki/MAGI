"""Tests for the ``search_sessions`` tool (D.18+1).

Pins three surfaces:

  1. **Hit formatting** — each FTS5 hit renders as a text
     block with header + surrounding context (the
     ``context_n`` parameter).
  2. **Cross-platform scope** — the tool scopes by the
     calling ``ctx.uid``, so an operator with
     sessions across multiple channels sees all of them
     (WebUI + TG + future IMs).
  3. **Validation** — bad ``q`` / bad ``context_n`` returns
     ``is_error=True`` instead of crashing the loop.

The FTS5 index and trigger sync are tested in
``test_chat_search.py``; here we focus on the tool's
presentation layer (text formatting, scope resolution, error
shapes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi.agent.memory.session import SessionStore
from magi.agent.db import init_sqlite
from magi.agent.db import ChatMessage, init_orm, open_session
from magi.agent.tools.base import ToolContext
from magi.agent.tools.search_sessions import SearchSessionsTool


# ────────────────────────────────────────────────────────────────── #
# fixtures
# ────────────────────────────────────────────────────────────────── #


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """Per-test isolated state dir + fresh ORM engine."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(state))

    import magi.agent.db.engine as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_sqlite(str(state))
    init_orm(str(state))
    return state, tmp_path


def _seed(
    state_dir: Path,
    *,
    delivery_address: str,
    uid: int,
    channel: str,
    messages: list[tuple[str, str]],
) -> str:
    """Insert one ChatSession + the given message rows.

    ``messages`` is a list of ``(role, text)`` tuples in
    append order. Returns the auto-generated session_id.
    """
    from datetime import datetime, timezone
    from magi.agent.memory.session import SessionMessage, new_session_id

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store = SessionStore(str(state_dir))
    # D.23: store key is uid (int); the delivery_address arg
    # is the per-channel delivery address stamped on the
    # row's delivery_address column.
    sess = store.create(
        uid, delivery_address=delivery_address, channel=channel,
    )
    msgs = [
        SessionMessage(
            role=role, text=text, ts=now,
            message_id=new_session_id(),
        )
        for role, text in messages
    ]
    store.append_messages(uid, sess.session_id, msgs)
    return sess.session_id


def _ctx(state_dir: Path, *, delivery_address: str, uid: int) -> ToolContext:
    """Build a ToolContext that points at ``state_dir`` and
    scopes to the calling ``uid``. ``delivery_address`` is part of the
    signature for parity with ``_seed`` but is no longer on
    :class:`ToolContext` (D.26 dropped ``delivery_address``; per-channel
    delivery now reads ``chat_sessions.delivery_address`` directly)."""
    return ToolContext(
        state_dir=str(state_dir),
        workspace=state_dir,  # not used by the tool
        uid=uid,
        channel="webui",
    )


# ────────────────────────────────────────────────────────────────── #
# happy path
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_search_sessions_returns_hit_with_context(fresh_db):
    """A hit returns a text block with header + surrounding
    messages (default ``context_n=5``)."""
    state, _workspace = fresh_db
    sid = _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[
            ("user",      "alpha"),
            ("assistant", "world reply 1"),
            ("user",      "beta"),
            ("assistant", "world reply 2"),
            ("user",      "gamma xyz-marker"),  # unique trigram hit
            ("assistant", "world reply 3"),
            ("user",      "delta"),
        ],
    )

    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)
    result = await tool.run(ctx, q="xyz-marker")

    assert not result.is_error
    # Header text from the tool wrapper.
    assert "search_sessions:" in result.content
    assert "q='xyz-marker'" in result.content
    assert "1 match(es)" in result.content
    # Hit block header carries the role + the session id.
    assert f"[hit] session={sid}" in result.content
    assert "role=user" in result.content
    # The hit message is rendered with the snippet's <mark>.
    assert "<mark>" in result.content
    assert "xyz-marker" in result.content
    # The previous turn is included as context.
    assert "world reply 2" in result.content


@pytest.mark.asyncio
async def test_search_sessions_context_n_controls_window(fresh_db):
    """``context_n`` bounds how many surrounding messages
    are returned. Smaller ``context_n`` → fewer neighbours."""
    state, _workspace = fresh_db
    _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[
            ("user",      "msg A"),
            ("user",      "msg B"),
            ("user",      "msg C unique-token-xyz"),
            ("user",      "msg D"),
            ("user",      "msg E"),
        ],
    )

    tool = SearchSessionsTool()

    # context_n=1 → at most ±1 surrounding message.
    out_1 = await tool.run(
        _ctx(state, delivery_address="9001", uid=1),
        q="unique-token-xyz", context_n=1,
    )
    assert not out_1.is_error
    # The hit (msg C) is rendered. msg B (before) and msg D
    # (after) are within ±1.
    assert "msg B" in out_1.content
    assert "msg D" in out_1.content
    # msg A is two away — outside ±1.
    assert "msg A" not in out_1.content
    # msg E is two away — outside ±1.
    assert "msg E" not in out_1.content


@pytest.mark.asyncio
async def test_search_sessions_context_n_zero_returns_snippet_only(fresh_db):
    """``context_n=0`` returns just the matching message
    (with the snippet) and no surrounding messages."""
    state, _workspace = fresh_db
    _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[
            ("user",      "before the hit"),
            ("user",      "hit with unique-token here"),
            ("user",      "after the hit"),
        ],
    )

    tool = SearchSessionsTool()
    out = await tool.run(
        _ctx(state, delivery_address="9001", uid=1),
        q="unique-token", context_n=0,
    )
    assert not out.is_error
    # The snippet shows the hit...
    assert "unique-token" in out.content
    # ...but neither neighbour is included.
    assert "before the hit" not in out.content
    assert "after the hit" not in out.content


@pytest.mark.asyncio
async def test_search_sessions_no_match_returns_clean_message(fresh_db):
    """No matches → a clean message (not an error)."""
    state, _workspace = fresh_db
    _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[("user", "nothing matches here")],
    )
    tool = SearchSessionsTool()
    out = await tool.run(
        _ctx(state, delivery_address="9001", uid=1),
        q="never-gonna-find-this",
    )
    assert not out.is_error
    assert "no matches" in out.content


# ────────────────────────────────────────────────────────────────── #
# cross-platform scope
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_search_sessions_cross_platform_employee_scope(fresh_db):
    """Scope is ``uid``, not ``delivery_address``. An operator
    who has both a webui and a TG session under the same
    employee row sees hits from BOTH in a single search.
    """
    state, _workspace = fresh_db
    # WebUI session under employee 1, delivery_address "9001".
    sid_webui = _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[("user", "webui unique-token-cross alpha")],
    )
    # TG session under employee 1, delivery_address "9876543210".
    sid_tg = _seed(
        state,
        delivery_address="9876543210",
        uid=1,
        channel="tg",
        messages=[("user", "tg unique-token-cross beta")],
    )
    # A different operator (employee 2) with a different delivery_address
    # value should NOT see employee 1's sessions.
    sid_other = _seed(
        state,
        delivery_address="9999",
        uid=2,
        channel="webui",
        messages=[("user", "someone-else's unique-token-cross gamma")],
    )

    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)
    out = await tool.run(ctx, q="unique-token-cross")

    assert not out.is_error
    # Employee 1 sees both their own sessions (webui + tg)...
    assert sid_webui in out.content
    assert sid_tg in out.content
    assert "channel=webui" in out.content
    assert "channel=tg" in out.content
    # ...but NOT employee 2's session.
    assert sid_other not in out.content


@pytest.mark.asyncio
async def test_search_sessions_employee_scope_isolates_other_admins(fresh_db):
    """Employee A's search doesn't return employee B's
    messages, even when both employees' sessions share the
    same ``delivery_address``.
    """
    state, _workspace = fresh_db
    sid_alice = _seed(
        state,
        delivery_address="9001",  # alice's telegram_id
        uid=1,   # alice
        channel="webui",
        messages=[("user", "alice unique-scope-test alpha")],
    )
    sid_bob = _seed(
        state,
        delivery_address="9001",  # same delivery_address, different operator
        uid=2,   # bob — same delivery_address, different uid
        channel="webui",
        messages=[("user", "bob unique-scope-test beta")],
    )

    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)
    out = await tool.run(ctx, q="unique-scope-test")

    assert not out.is_error
    assert sid_alice in out.content
    assert sid_bob not in out.content


# ────────────────────────────────────────────────────────────────── #
# validation
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_search_sessions_requires_q(fresh_db):
    """Empty / missing ``q`` → ``is_error=True``."""
    state, _workspace = fresh_db
    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)

    out_empty = await tool.run(ctx, q="")
    assert out_empty.is_error
    assert "q" in out_empty.content

    out_missing = await tool.run(ctx)  # no q at all
    assert out_missing.is_error


@pytest.mark.asyncio
async def test_search_sessions_rejects_non_int_context_n(fresh_db):
    """``context_n`` must be an integer (the SDK rejects
    non-int upstream, but the tool defends anyway)."""
    state, _workspace = fresh_db
    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)
    out = await tool.run(ctx, q="anything", context_n="five")
    assert out.is_error
    assert "context_n" in out.content


@pytest.mark.asyncio
async def test_search_sessions_clamps_context_n_to_max(fresh_db):
    """``context_n`` over the max is clamped to the max, not
    rejected — operators don't need to know the limit."""
    state, _workspace = fresh_db
    _seed(
        state,
        delivery_address="9001",
        uid=1,
        channel="webui",
        messages=[("user", "unique-clamp-test")],
    )
    tool = SearchSessionsTool()
    ctx = _ctx(state, delivery_address="9001", uid=1)
    out = await tool.run(
        ctx, q="unique-clamp-test", context_n=999,
    )
    # No crash, no error — clamped.
    assert not out.is_error


# ────────────────────────────────────────────────────────────────── #
# tool schema
# ────────────────────────────────────────────────────────────────── #


def test_search_sessions_schema_has_required_fields(tmp_path, monkeypatch):
    """The Anthropic-shaped schema advertises the
    ``q`` + optional ``context_n`` / ``limit`` inputs the
    LLM is meant to provide. ``MAGI_STATE_DIR`` is set so
    the registry import (which loads the tool loader) does
    not bounce off the ``_MissingStateDirError`` guard."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    from magi.agent.tools.registry import get_tool_schemas

    schemas = {s["name"]: s for s in get_tool_schemas()}
    assert "search_sessions" in schemas
    schema = schemas["search_sessions"]
    props = schema["input_schema"]["properties"]
    assert "q" in props
    assert props["q"]["type"] == "string"
    assert "context_n" in props
    assert props["context_n"]["type"] == "integer"
    assert schema["input_schema"]["required"] == ["q"]


# ────────────────────────────────────────────────────────────────── #
# truncation footer
# ────────────────────────────────────────────────────────────────── #
#
# The tool caps the rendered output at 8 KB. When the cap
# is reached the response carries a footer summarising how
# many hits were omitted. When nothing was truncated the
# footer must be ABSENT — the previous code initialised the
# sentinel to ``len(hits)`` and printed "N additional hits
# omitted" on every successful search.


@pytest.mark.asyncio
async def test_search_sessions_no_truncation_omits_footer(fresh_db):
    """A search with hits that fit comfortably under the 8 KB
    cap must NOT include the truncation footer."""
    state_dir, _ = fresh_db
    _seed(
        state_dir, delivery_address="9001", uid=1, channel="webui",
        messages=[
            ("user", "hello world"),
            ("assistant", "hi there"),
        ],
    )
    ctx = _ctx(state_dir, delivery_address="9001", uid=1)
    result = await SearchSessionsTool().run(
        ctx, q="hello", context_n=2,
    )
    assert result.is_error is False
    # The footer text must be absent — nothing was truncated.
    assert "additional hit" not in result.content
    assert "omitted" not in result.content


@pytest.mark.asyncio
async def test_search_sessions_truncation_footer_counts_correctly(
    fresh_db,
):
    """When truncation fires the footer must report
    ``len(hits) - len(blocks)`` hits omitted — not the
    1-indexed ``i-1`` the buggy version used, and not
    ``len(hits)`` (which would say "all hits were omitted"
    even when half of them rendered)."""
    state_dir, _ = fresh_db
    # Seed MANY long messages per session, with a unique
    # marker so the FTS query hits all of them. The tool's
    # internal hit limit caps at 20; with 20 hits and a fat
    # ``context_n`` each block easily crosses 8 KB.
    from magi.agent.memory.session import (
        SessionMessage, new_session_id,
    )
    from datetime import datetime, timezone

    store = SessionStore(str(state_dir))
    n_sessions = 20
    for k in range(n_sessions):
        sess = store.create(
            1, delivery_address="9001", channel="webui",
        )
        marker = f"uniquetoken-{k:04d}"
        # Each session carries 200 padding messages so a
        # ``context_n=200`` window inflates every block past
        # 8 KB / 20 = ~400 bytes. Together they overflow
        # the 8 KB output cap well before all 20 hits render.
        padding = [
            SessionMessage(
                role="user",
                text=("padding line " * 8),
                ts=datetime.now(timezone.utc).isoformat(),
                message_id=new_session_id(),
            )
            for _ in range(200)
        ]
        padding.append(SessionMessage(
            role="user", text=f"hit {marker}",
            ts=datetime.now(timezone.utc).isoformat(),
            message_id=new_session_id(),
        ))
        store.append_messages(1, sess.session_id, padding)

    ctx = _ctx(state_dir, delivery_address="9001", uid=1)
    result = await SearchSessionsTool().run(
        ctx, q="uniquetoken", context_n=200, limit=20,
    )
    assert result.is_error is False
    # Truncation must have fired.
    assert "additional hit" in result.content, (
        f"expected truncation footer; got content starting with "
        f"{result.content[:200]!r}"
    )
    import re
    m = re.search(r"…\((\d+) additional hit", result.content)
    assert m is not None
    omitted = int(m.group(1))
    header_m = re.search(
        r"returning (\d+) of (\d+)", result.content,
    )
    assert header_m is not None
    rendered = int(header_m.group(1))
    total = int(header_m.group(2))
    assert total == n_sessions
    assert rendered > 0
    assert rendered < total, (
        "test setup must trigger truncation; rendered == total"
    )
    assert omitted == total - rendered, (
        f"footer says {omitted} omitted; expected {total - rendered} "
        f"(total {total} - rendered {rendered})"
    )