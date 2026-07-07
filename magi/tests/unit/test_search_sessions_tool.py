"""Tests for the ``search_sessions`` tool (D.18+1).

Pins three surfaces:

  1. **Hit formatting** — each FTS5 hit renders as a text
     block with header + surrounding context (the
     ``context_n`` parameter).
  2. **Cross-platform scope** — the tool scopes by the
     calling ``ctx.employee_id``, so an operator with
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

from magi.agent.sessions import SessionStore
from magi.agent.state import init_sqlite
from magi.agent.state.orm import ChatMessage, init_orm, open_session
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

    import magi.agent.state.orm as orm_mod
    orm_mod._engine = None
    orm_mod._SessionLocal = None

    init_sqlite(str(state))
    init_orm(str(state))
    return state, tmp_path


def _seed(
    state_dir: Path,
    *,
    chat_id: str,
    employee_id: int,
    channel: str,
    messages: list[tuple[str, str]],
) -> str:
    """Insert one ChatSession + the given message rows.

    ``messages`` is a list of ``(role, text)`` tuples in
    append order. Returns the auto-generated session_id.
    """
    from datetime import datetime, timezone
    from magi.agent.sessions import SessionMessage, new_session_id

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store = SessionStore(str(state_dir))
    sess = store.create(
        chat_id, employee_id=employee_id, channel=channel,
    )
    msgs = [
        SessionMessage(
            role=role, text=text, ts=now,
            message_id=new_session_id(),
        )
        for role, text in messages
    ]
    store.append_messages(chat_id, sess.session_id, msgs)
    return sess.session_id


def _ctx(state_dir: Path, *, chat_id: str, employee_id: int) -> ToolContext:
    """Build a ToolContext that points at ``state_dir`` and
    scopes to ``(chat_id, employee_id)``."""
    return ToolContext(
        state_dir=str(state_dir),
        workspace=state_dir,  # not used by the tool
        chat_id=chat_id,
        employee_id=employee_id,
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
        chat_id="9001",
        employee_id=1,
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
    ctx = _ctx(state, chat_id="9001", employee_id=1)
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
        chat_id="9001",
        employee_id=1,
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
        _ctx(state, chat_id="9001", employee_id=1),
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
        chat_id="9001",
        employee_id=1,
        channel="webui",
        messages=[
            ("user",      "before the hit"),
            ("user",      "hit with unique-token here"),
            ("user",      "after the hit"),
        ],
    )

    tool = SearchSessionsTool()
    out = await tool.run(
        _ctx(state, chat_id="9001", employee_id=1),
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
        chat_id="9001",
        employee_id=1,
        channel="webui",
        messages=[("user", "nothing matches here")],
    )
    tool = SearchSessionsTool()
    out = await tool.run(
        _ctx(state, chat_id="9001", employee_id=1),
        q="never-gonna-find-this",
    )
    assert not out.is_error
    assert "no matches" in out.content


# ────────────────────────────────────────────────────────────────── #
# cross-platform scope
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_search_sessions_cross_platform_employee_scope(fresh_db):
    """Scope is ``employee_id``, not ``chat_id``. An operator
    who has both a webui and a TG session under the same
    employee row sees hits from BOTH in a single search.
    """
    state, _workspace = fresh_db
    # WebUI session under employee 1, tgid "9001".
    sid_webui = _seed(
        state,
        chat_id="9001",
        employee_id=1,
        channel="webui",
        messages=[("user", "webui unique-token-cross alpha")],
    )
    # TG session under employee 1, tgid "9876543210".
    sid_tg = _seed(
        state,
        chat_id="9876543210",
        employee_id=1,
        channel="tg",
        messages=[("user", "tg unique-token-cross beta")],
    )
    # A different operator (employee 2) with a different tgid
    # value should NOT see employee 1's sessions.
    sid_other = _seed(
        state,
        chat_id="9999",
        employee_id=2,
        channel="webui",
        messages=[("user", "someone-else's unique-token-cross gamma")],
    )

    tool = SearchSessionsTool()
    ctx = _ctx(state, chat_id="9001", employee_id=1)
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
    same ``tgid``.
    """
    state, _workspace = fresh_db
    sid_alice = _seed(
        state,
        chat_id="9001",  # alice's telegram_id
        employee_id=1,   # alice
        channel="webui",
        messages=[("user", "alice unique-scope-test alpha")],
    )
    sid_bob = _seed(
        state,
        chat_id="9001",  # same tgid, different operator
        employee_id=2,   # bob — same tgid, different employee_id
        channel="webui",
        messages=[("user", "bob unique-scope-test beta")],
    )

    tool = SearchSessionsTool()
    ctx = _ctx(state, chat_id="9001", employee_id=1)
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
    ctx = _ctx(state, chat_id="9001", employee_id=1)

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
    ctx = _ctx(state, chat_id="9001", employee_id=1)
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
        chat_id="9001",
        employee_id=1,
        channel="webui",
        messages=[("user", "unique-clamp-test")],
    )
    tool = SearchSessionsTool()
    ctx = _ctx(state, chat_id="9001", employee_id=1)
    out = await tool.run(
        ctx, q="unique-clamp-test", context_n=999,
    )
    # No crash, no error — clamped.
    assert not out.is_error


# ────────────────────────────────────────────────────────────────── #
# tool schema
# ────────────────────────────────────────────────────────────────── #


def test_search_sessions_schema_has_required_fields():
    """The Anthropic-shaped schema advertises the
    ``q`` + optional ``context_n`` / ``limit`` inputs the
    LLM is meant to provide."""
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