"""Tests for :mod:`magi.agent.session.auto_title`.

The worker module imports ``get_provider`` at module top, so
our fake is injected via monkeypatch on the *imported*
binding (``magi.agent.session.auto_title.get_provider``). Same trick
the rest of the codebase uses for dependency injection.

``_summarize_to_title`` calls ``asyncio.sleep(5)`` to let the
inbound append settle; we monkeypatch ``asyncio.sleep`` per
test so the suite stays fast.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

# These imports load after the first fixture that sets
# ``MAGI_STATE_DIR``. We arrange the fixtures to do that
# before any test body runs.


@pytest.fixture
def state_dir(monkeypatch, tmp_path):
    """An isolated ``MAGI_STATE_DIR`` + ``MAGI_WORKSPACE_DIR``.
    Pinning both lets ``SessionStore`` and the worker
    helper (``_state_dir_for_job``) read the same path.
    """
    sd = tmp_path / "state"
    sd.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("MAGI_STATE_DIR", str(sd))
    monkeypatch.setenv("MAGI_WORKSPACE_DIR", str(ws))
    return sd


def _seed_admin():
    """Insert one admin employee row with valid LLM creds.

    Wipes the ``employees`` table first so multiple seeded
    tests in the same pytest run don't trip the UNIQUE
    constraint on ``telegram_id``.
    """
    from magi.agent.db import (
        Employee, init_orm, open_session,
    )
    init_orm(os.environ["MAGI_STATE_DIR"])
    with open_session() as s:
        # Clean slate (cheap; we never need cross-test
        # employee state for these tests).
        s.query(Employee).delete()
        admin = Employee(
            name="TA-test",
            telegram_id=9001,
            role="admin",
            provider="minimax",
            api_key="fake-key",
        )
        s.add(admin)
        s.commit()
        s.refresh(admin)
        return admin


class FakeProvider:
    """Drop-in for ``LLMProvider`` that returns a fixed
    title. Captured by tests to assert call counts and
    inputs.
    """

    name = "fake"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.calls: list[dict] = []

    def default_model(self) -> str:
        return "fake-1"

    async def chat(self, system, messages, max_tokens=1024):
        # Record the call for assertion.
        self.calls.append(
            {
                "system": system,
                "messages": [
                    {"role": m.role, "content": m.content} for m in messages
                ],
                "max_tokens": max_tokens,
            }
        )
        # Slightly different responses so tests can tell if
        # the cleaned-up version matches.
        from magi.agent.llm.provider import ChatResult
        if self._title_text is None:
            text = "Untitled chat"
        else:
            text = self._title_text
        return ChatResult(
            text=text,
            thinking="",
            model="fake-1",
            usage=None,
            raw_blocks=[],
        )


def _install_fake_provider(monkeypatch, *, title_text: str | None = "Untitled chat"):
    """Patch ``magi.agent.session.auto_title.get_provider`` to return a
    fresh :class:`FakeProvider` per call. Returns the proxy
    that captures each instance's ``.calls`` list (one per
    invocation).
    """
    # Late import — so the patch lands after the real symbol
    # is bound at module import time.
    import magi.agent.session.auto_title as at_mod

    instances: list[FakeProvider] = []

    def _factory(name, api_key, model=None):
        inst = FakeProvider(api_key=api_key)
        inst._title_text = title_text
        instances.append(inst)
        return inst

    monkeypatch.setattr(at_mod, "get_provider", _factory)

    # Skip the 5-second sleep inside _summarize_to_title for
    # fast tests. Use a no-op passthrough that ignores the
    # delay entirely.
    sleep_calls: list[float] = []

    async def _fast_sleep(_seconds: float):
        sleep_calls.append(_seconds)
        # Don't actually sleep.
        return

    monkeypatch.setattr(at_mod.asyncio, "sleep", _fast_sleep)

    return instances, sleep_calls


# ────────────────────────────────────────────────────────────────── #
# cleanse_title
# ────────────────────────────────────────────────────────────────── #


def test_cleanse_strips_quotes_and_whitespace():
    from magi.agent.session.auto_title import _cleanse_title

    assert _cleanse_title('  "Acme 会议"  ') == "Acme 会议"
    assert _cleanse_title("'hello world'") == "hello world"
    assert _cleanse_title("`code`") == "code"


def test_cleanse_keeps_first_line_only():
    from magi.agent.session.auto_title import _cleanse_title

    assert _cleanse_title("first line\nsecond line\nthird") == "first line"


def test_cleanse_clamps_to_80_chars():
    from magi.agent.session.auto_title import _cleanse_title

    long = "x" * 200
    out = _cleanse_title(long)
    assert len(out) == 80


def test_cleanse_returns_empty_for_blank():
    from magi.agent.session.auto_title import _cleanse_title

    assert _cleanse_title("") == ""
    assert _cleanse_title("   ") == ""
    assert _cleanse_title("\n\n") == ""


# ────────────────────────────────────────────────────────────────── #
# _summarize_to_title — happy path + bails
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_summarize_happy_path_persists_title(state_dir, monkeypatch):
    """End-to-end: create session → append user message → run
    ``_summarize_to_title`` → title is set on the DB row."""
    from magi.agent.session import (
        SessionMessage,
        SessionStore,
        new_session_id as _mk_id,
    )

    admin = _seed_admin()
    providers, _ = _install_fake_provider(
        monkeypatch, title_text="My first question"
    )

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id
    msg_id = _mk_id()
    store.append_messages(
        "9001",
        sid,
        [SessionMessage(
            role="user",
            text="Help me write a Python CSV parser",
            ts="2026-07-03T10:00:00Z",
            message_id=msg_id,
        )],
    )

    from magi.agent.session.auto_title import _summarize_to_title, TitleJob

    await _summarize_to_title(
        TitleJob(
            chat_id="9001",
            session_id=sid,
            employee_id=admin.id,
            employee_provider="minimax",
            employee_api_key="fake-key",
        )
    )

    # Title was written.
    again = store.get("9001", sid)
    assert again.title == "My first question"
    # D.18: title lives in the chat_sessions row, not a JSON file.
    from magi.agent.db import ChatSession, open_session
    with open_session() as db:
        row = db.get(ChatSession, sid)
    assert row.title == "My first question"


@pytest.mark.asyncio
async def test_summarize_idempotent_second_run_skips(state_dir, monkeypatch):
    """Second invocation sees ``title`` set and bails without
    calling the provider again."""
    from magi.agent.session import SessionMessage, SessionStore

    admin = _seed_admin()
    providers, _ = _install_fake_provider(
        monkeypatch, title_text="First run title"
    )

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    from magi.agent.session import new_session_id as _mk_id
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id

    store.append_messages(
        "9001", sid,
        [SessionMessage(
            role="user", text="hi", ts="2026-07-03T10:00:00Z",
            message_id=_mk_id(),
        )],
    )

    from magi.agent.session.auto_title import _summarize_to_title, TitleJob
    job = TitleJob(
        chat_id="9001", session_id=sid, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="fake-key",
    )

    await _summarize_to_title(job)  # first run → title set
    provider_count_after_first = sum(len(p.calls) for p in providers)
    assert provider_count_after_first == 1

    await _summarize_to_title(job)  # second → bail
    provider_count_after_second = sum(len(p.calls) for p in providers)
    # No additional provider calls.
    assert provider_count_after_second == 1


@pytest.mark.asyncio
async def test_summarize_skipped_when_no_user_message(state_dir, monkeypatch):
    """A session with only assistant messages (or empty)
    shouldn't fire the LLM."""
    from magi.agent.session import SessionStore, new_session_id as _mk_id
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob

    admin = _seed_admin()
    providers, _ = _install_fake_provider(monkeypatch, title_text="x")

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    # Create an empty session (no user messages) — the worker
    # should see no first-user-message and bail without
    # calling the provider. (Match the real id from
    # ``create``.)
    sess = store.create("9001", employee_id=admin.id)

    await _summarize_to_title(TitleJob(
        chat_id="9001", session_id=sess.session_id, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="fake-key",
    ))
    assert store.get("9001", sess.session_id).title is None
    assert sum(len(p.calls) for p in providers) == 0


@pytest.mark.asyncio
async def test_summarize_skipped_when_session_missing(state_dir, monkeypatch):
    """A deleted-mid-job session is silently ignored. Title
    worker must not raise into the consumer loop."""
    from magi.agent.session import SessionStore
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob

    admin = _seed_admin()
    providers, _ = _install_fake_provider(monkeypatch)

    # No session created — file doesn't exist.
    await _summarize_to_title(TitleJob(
        chat_id="9001",
        session_id="01ABCDEFGHJKMNPQRSTVWXYZAB",
        employee_id=admin.id,
        employee_provider="minimax",
        employee_api_key="fake-key",
    ))
    assert sum(len(p.calls) for p in providers) == 0


@pytest.mark.asyncio
async def test_summarize_swallowed_llm_error(state_dir, monkeypatch):
    """``provider.chat`` raises ``LLMAuthError``; the worker
    swallows it and never reaches the ``rename`` step."""
    from magi.agent.llm.errors import LLMAuthError
    from magi.agent.llm.provider import ChatMessage as _CM  # noqa: F401
    from magi.agent.session import SessionMessage, SessionStore
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob
    import magi.agent.session.auto_title as at_mod

    admin = _seed_admin()
    providers, _ = _install_fake_provider(monkeypatch, title_text="x")

    class Raising(FakeProvider):
        async def chat(self, system, messages, max_tokens=1024):
            raise LLMAuthError("simulated bad key")

    def _raising_factory(name, api_key, model=None):
        return Raising(api_key=api_key)

    monkeypatch.setattr(at_mod, "get_provider", _raising_factory)

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id
    store.append_messages(
        "9001", sid,
        [SessionMessage(
            role="user", text="hi", ts="2026-07-03T10:00:00Z",
            message_id="01ABCDEFGHJKMNPQRSTVWXYZB",
        )],
    )

    # Should not raise.
    await _summarize_to_title(TitleJob(
        chat_id="9001", session_id=sid, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="bad",
    ))
    assert store.get("9001", sid).title is None  # title wasn't set


@pytest.mark.asyncio
async def test_summarize_swallowed_unknown_provider_error(state_dir, monkeypatch):
    """If the worker fails to construct a provider (some
    ad-hoc bug), the worker still survives."""
    from magi.agent.session import SessionMessage, SessionStore
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob
    import magi.agent.session.auto_title as at_mod

    admin = _seed_admin()

    def _boom(name, api_key, model=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(at_mod, "get_provider", _boom)

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id
    store.append_messages(
        "9001", sid,
        [SessionMessage(
            role="user", text="hi", ts="2026-07-03T10:00:00Z",
            message_id="01ABCDEFGHJKMNPQRSTVWXYZB",
        )],
    )

    # Must not raise.
    await _summarize_to_title(TitleJob(
        chat_id="9001", session_id=sid, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="fake",
    ))
    assert store.get("9001", sid).title is None


@pytest.mark.asyncio
async def test_summarize_clamps_long_reply(state_dir, monkeypatch):
    from magi.agent.session import SessionMessage, SessionStore
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob

    admin = _seed_admin()
    _install_fake_provider(monkeypatch, title_text="x" * 200)

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id
    store.append_messages(
        "9001", sid,
        [SessionMessage(
            role="user", text="hi", ts="2026-07-03T10:00:00Z",
            message_id="01ABCDEFGHJKMNPQRSTVWXYZB",
        )],
    )

    await _summarize_to_title(TitleJob(
        chat_id="9001", session_id=sid, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="fake",
    ))
    assert len(store.get("9001", sid).title) == 80


@pytest.mark.asyncio
async def test_summarize_swallowed_empty_reply(state_dir, monkeypatch):
    """Empty / cleansed-empty responses don't set a title."""
    from magi.agent.session import SessionMessage, SessionStore
    from magi.agent.session.auto_title import _summarize_to_title, TitleJob

    admin = _seed_admin()
    _install_fake_provider(monkeypatch, title_text="")

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    sess = store.create("9001", employee_id=admin.id)
    sid = sess.session_id
    store.append_messages(
        "9001", sid,
        [SessionMessage(
            role="user", text="hi", ts="2026-07-03T10:00:00Z",
            message_id="01ABCDEFGHJKMNPQRSTVWXYZB",
        )],
    )

    await _summarize_to_title(TitleJob(
        chat_id="9001", session_id=sid, employee_id=admin.id,
        employee_provider="minimax", employee_api_key="fake",
    ))
    assert store.get("9001", sid).title is None


# ────────────────────────────────────────────────────────────────── #
# worker loop + lifecycle
# ────────────────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_worker_loop_drains_queue(state_dir, monkeypatch):
    """The worker loop processes enqueued jobs."""
    from magi.agent.session import SessionMessage, SessionStore
    from magi.agent.session.auto_title import (
        TitleJob,
        enqueue_title_job,
        start_title_worker,
        stop_title_worker,
    )

    admin = _seed_admin()
    instances, _ = _install_fake_provider(monkeypatch)

    await start_title_worker()

    store = SessionStore(os.environ["MAGI_STATE_DIR"])
    from magi.agent.session import new_session_id as _mk_id
    for _ in range(2):
        sess = store.create("9001", employee_id=admin.id)
        sid = sess.session_id
        store.append_messages(
            "9001", sid,
            [SessionMessage(
                role="user", text="hi", ts="2026-07-03T10:00:00Z",
                message_id=_mk_id(),
            )],
        )
        await enqueue_title_job(
            chat_id="9001",
            session_id=sid,
            employee_id=admin.id,
            employee_provider="minimax",
            employee_api_key="fake",
        )

    # Wait for the two jobs to land — each takes essentially
    # 0s thanks to the no-op sleep in ``_install_fake_provider``.
    from magi.agent.db import ChatSession, open_session
    for _ in range(50):
        await asyncio.sleep(0.01)
        # Both sessions titled?
        with open_session() as db:
            count = db.query(ChatSession).filter_by(
                tgid="9001",
            ).filter(ChatSession.title.isnot(None)).count()
        if count >= 2:
            break

    await stop_title_worker()


@pytest.mark.asyncio
async def test_start_stop_worker_lifecycle(state_dir, monkeypatch):
    """``start`` is idempotent; ``stop`` clears the task.

    Note: we read the module-level ``_worker_task`` via
    attribute access (not ``from ... import _worker_task``)
    so we observe the live binding at test time. ``from
    ... import`` would capture the value at collection time,
    which is always ``None`` (the module default).
    """
    import magi.agent.session.auto_title as at_mod

    await at_mod.start_title_worker()
    assert at_mod._worker_task is not None
    assert not at_mod._worker_task.done()

    await at_mod.start_title_worker()  # second call is a no-op
    assert at_mod._worker_task is not None

    await at_mod.stop_title_worker()
    assert at_mod._worker_task is None

    await at_mod.stop_title_worker()  # idempotent on already-stopped


@pytest.mark.asyncio
async def test_enqueue_does_not_block(state_dir, monkeypatch):
    """``enqueue_title_job`` returns immediately even if the
    worker is not running."""
    from magi.agent.session.auto_title import enqueue_title_job, _title_jobs

    # Drain any backlog from earlier tests.
    while not _title_jobs.empty():
        _title_jobs.get_nowait()

    await enqueue_title_job(
        chat_id="9001",
        session_id="01ABCDEFGHJKMNPQRSTVWXYZAB",
        employee_id=1,
        employee_provider="minimax",
        employee_api_key="k",
    )
    assert _title_jobs.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_with_provider_captures_credentials(state_dir, monkeypatch):
    """The job struct carries the credentials verbatim so a
    later key rotation doesn't affect the worker."""
    from magi.agent.session.auto_title import enqueue_title_job, _title_jobs, TitleJob

    while not _title_jobs.empty():
        _title_jobs.get_nowait()

    await enqueue_title_job(
        chat_id="9001",
        session_id="01ABCDEFGHJKMNPQRSTVWXYZAB",
        employee_id=42,
        employee_provider="minimax-cn",
        employee_api_key="captured-key-xyz",
    )
    job: TitleJob = _title_jobs.get_nowait()
    assert job.employee_provider == "minimax-cn"
    assert job.employee_api_key == "captured-key-xyz"
    assert job.employee_id == 42
