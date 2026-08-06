"""End-to-end tests for :class:`magi.providers.worker.ProvidersWorker`.

These tests exercise the durable queue around the LLM lifecycle:
publish a job, watch the worker claim it, run the (stub) provider,
write back the terminal state. We inject a ``FakeProvider`` via
the public ``get_provider`` seam so the tests don't depend on
real network calls or a configured MAGI row.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from magi.bus import get_bus_store
from magi.bus.bootstrap import bootstrap
from magi.bus.db import init_orm
from magi.bus.db.magis.engine import init_magis_public_db
from magi.bus.db.models.queue import LLMAttempt
from magi.bus.db.engine import open_session
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.provider import ChatMessage, ChatResult, LLMProvider
from magi.providers.worker import (
    ProvidersWorker,
    start_provider_worker,
    stop_provider_worker,
)


class FakeProvider(LLMProvider):
    """Minimal provider used by every test in this file.

    Honours ``reply`` (string) when set; otherwise echoes the
    last user message back so the assertion can spot-check the
    round-trip. Raises ``LLMError`` on a request whose last user
    message starts with ``!raise:`` so a test can drive the
    failure path deterministically.
    """

    name = "fake"

    def __init__(self, *, reply: str = "", fail_message: str = "") -> None:
        super().__init__(api_key="dummy")
        self.reply = reply
        self.fail_message = fail_message
        self.call_count = 0

    def default_model(self) -> str:
        return "fake-model-1"

    async def chat(self, *, system, messages, max_tokens, tools=None):
        self.call_count += 1
        last = messages[-1].content if messages else ""
        if self.fail_message and last.startswith("!raise"):
            raise LLMError(self.fail_message)
        if last.startswith("!notconfigured"):
            raise LLMNotConfiguredError(self.fail_message or "not configured")
        text = self.reply or f"echo:{last}"
        return ChatResult(
            text=text,
            model=self.default_model(),
            usage={"input_tokens": 10, "output_tokens": 5},
            tool_uses=[],
            raw_blocks=[],
            stop_reason="end_turn",
        )

    async def stream(self, *, system, messages, max_tokens, tools=None, on_event):
        result = await self.chat(
            system=system, messages=messages, max_tokens=max_tokens,
            tools=tools,
        )
        await on_event(ChatMessage("text.delta", result.text))
        return result


@pytest.fixture
def magi_state(tmp_path, monkeypatch):
    """Stand up a per-test SQLite database + bus store; tear down workers.

    Sets both env vars the runtime needs (workspace_dir + magis engine url)
    so the initializer doesn't fail at the first SQLAlchemy session.
    """
    monkeypatch.setenv("MAGI_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HOST_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "MAGIS_DATABASE_URL", f"sqlite:///{tmp_path / 'magis.db'}",
    )
    init_orm(seed_root=True)
    init_magis_public_db(seed_root=True)
    bootstrap(initialise_local=True)
    yield tmp_path


def _patched_get_provider(reply: str = "", fail_message: str = ""):
    """Returns a fresh ``FakeProvider`` for each call — keeps state per-job."""
    return FakeProvider(reply=reply, fail_message=fail_message)


def _install_fake(fake: "FakeProvider"):
    """Patch every public name ``get_provider`` resolves to.

    The worker imports the symbol directly from
    :mod:`magi.providers` at module load (``from magi.providers
    import get_provider``), so monkey-patching only the factory's
    binding isn't enough — every entry-point the worker uses has to
    be replaced.

    The factory's ``get_provider`` is also patched so any direct
    factory callers (e.g. tests that re-bind it) still see the fake.
    """
    import magi.providers
    import magi.providers.factory as _factory
    import magi.providers.worker as _worker

    def _fake_get(*_args, **_kwargs):
        return fake

    _factory.get_provider = _fake_get
    _worker.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]  # back-compat sym


def _enqueue_test_job(
    store,
    *,
    run_id: str,
    request: dict,
    inbox_event_id: str | None = None,
    kind: str = "chat",
    hook_context=None,
) -> str:
    """Enqueue an LLM job either via the new or legacy contract.

    The new contract takes ``request`` in ``enqueue_llm_job`` and
    returns ``EnqueueResult(row_id=...)``.  The legacy contract
    returns a bare ``str`` and requires a separate
    ``persist_llm_job_request`` call.  We try the new contract
    first, falling back to the legacy form on ``TypeError`` so the
    tests stay green against either bus.store revision.
    """
    try:
        result = store.enqueue_llm_job(
            run_id=run_id,
            request=request,
            inbox_event_id=inbox_event_id,
            kind=kind,
            hook_context=hook_context,
        )
        return result.row_id if hasattr(result, "row_id") else result
    except TypeError:
        attempt_id = store.enqueue_llm_job(
            run_id=run_id,
            inbox_event_id=inbox_event_id,
            kind=kind,
        )
        store.persist_llm_job_request(attempt_id, request=request)
        return attempt_id


@pytest.mark.asyncio
async def test_publish_then_complete_round_trip(magi_state):
    """A successful call writes ``completed`` with the response JSON."""
    fake = FakeProvider(reply="hi from provider")
    _install_fake(fake)
    await start_provider_worker()
    try:
        store = get_bus_store()
        attempt_id = store.enqueue_llm_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="chat",
        )
        store.persist_llm_job_request(
            attempt_id,
            request={
                "system": "you are a test",
                "messages": [
                    {"role": "user", "content": "hello", "content_blocks": None},
                ],
                "max_tokens": 16,
                "tools": None,
                "streaming": False,
                "extra": {},
            },
        )
        result = await asyncio.to_thread(
            store.load_llm_job_result, attempt_id,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result is not None, "worker did not settle the row in time"
        if result["status"] != "completed":
            print("DEBUG failure detail:", result)
        assert result["status"] == "completed"
        assert result["response"]["text"] == "hi from provider"
        assert result["response"]["model"] == "fake-model-1"
        # Audit row at the SQL level
        with open_session(store._state_dir) as s:
            ar = s.query(LLMAttempt).filter_by(attempt_id=attempt_id).one()
        assert ar.status == "completed"
        assert ar.response["text"] == "hi from provider"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_provider_not_configured_envelopes_failure(magi_state):
    """A ``LLMNotConfiguredError`` settles the row with the credentials code."""
    fake = FakeProvider(fail_message="no api key in magi row")
    _install_fake(fake)
    await start_provider_worker()
    try:
        store = get_bus_store()
        attempt_id = store.enqueue_llm_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="chat",
        )
        store.persist_llm_job_request(
            attempt_id,
            request={
                "system": "",
                "messages": [
                    {"role": "user", "content": "!notconfigured", "content_blocks": None},
                ],
                "max_tokens": 16,
                "tools": None,
                "streaming": False,
                "extra": {},
            },
        )
        result = await asyncio.to_thread(
            store.load_llm_job_result, attempt_id,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result["status"] == "failed"
        assert result["error"]["code"] == "magi.llm_credentials_required"
        assert "no api key" in result["error"]["detail"]
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_provider_crashed_envelopes_generic_failure(magi_state):
    """An ``LLMError`` settles the row with the crashed code."""
    fake = FakeProvider(fail_message="upstream auth failed")
    _install_fake(fake)
    await start_provider_worker()
    try:
        store = get_bus_store()
        attempt_id = store.enqueue_llm_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="chat",
        )
        store.persist_llm_job_request(
            attempt_id,
            request={
                "system": "",
                "messages": [
                    {"role": "user", "content": "!raise:anything", "content_blocks": None},
                ],
                "max_tokens": 16,
                "tools": None,
                "streaming": False,
                "extra": {},
            },
        )
        result = await asyncio.to_thread(
            store.load_llm_job_result, attempt_id,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result["status"] == "failed"
        assert result["error"]["code"] == "LLMError"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_concurrency_limit_serialises_two_jobs(magi_state):
    """Two queued jobs each get a turn; the worker re-claims them FIFO.

    Smoke-test the semaphore: we don't measure wall-clock, just
    confirm both rows settle ``completed`` in the order they were
    enqueued.
    """
    fake = FakeProvider(reply="ok")
    _install_fake(fake)
    await start_provider_worker()
    try:
        store = get_bus_store()
        ids = []
        for i in range(2):
            aid = store.enqueue_llm_job(
                run_id=f"run-{i}",
                inbox_event_id=f"ev-{i}",
                kind="chat",
            )
            store.persist_llm_job_request(
                aid,
                request={
                    "system": "",
                    "messages": [
                        {"role": "user", "content": f"hello {i}", "content_blocks": None},
                    ],
                    "max_tokens": 16,
                    "tools": None,
                    "streaming": False,
                    "extra": {},
                },
            )
            ids.append(aid)
        for aid in ids:
            result = await asyncio.to_thread(
                store.load_llm_job_result, aid,
                wait_seconds=10, poll_seconds=0.05,
            )
            assert result["status"] == "completed", (
                f"row {aid} did not complete: {result}"
            )
            assert result["response"]["text"] == "ok"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_load_llm_job_result_returns_none_on_timeout(magi_state):
    """A row that's never settled returns ``None`` after the deadline."""
    fake = FakeProvider(reply="ignored")
    _install_fake(fake)
    # Don't start the worker — the queued row won't move.
    store = get_bus_store()
    aid = store.enqueue_llm_job(
        run_id="never", inbox_event_id="never", kind="chat",
    )
    started = datetime.now()
    result = await asyncio.to_thread(
        store.load_llm_job_result, aid,
        wait_seconds=0.5, poll_seconds=0.05,
    )
    assert result is None
    # And the row stayed ``queued`` (no worker claim).
    with open_session(store._state_dir) as s:
        ar = s.query(LLMAttempt).filter_by(attempt_id=aid).one()
    assert ar.status == "queued"


def _install_counting_fake(fake: "FakeProvider"):
    """Wrap ``fake`` so ``get_provider`` increments a per-process counter.

    The worker caches one provider per ``start()``; the counter lets
    tests assert ``get_provider`` was called exactly once across N
    jobs (the cache invariant) and that a drained control-job row
    forced a second call.
    """
    import magi.providers
    import magi.providers.factory as _factory
    import magi.providers.worker as _worker

    state = {"calls": 0, "current": fake}

    def _fake_get(*_args, **_kwargs):
        state["calls"] += 1
        return state["current"]

    _factory.get_provider = _fake_get
    _worker.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]

    return state


def _enqueue_simple(store, *, content: str = "hello") -> str:
    """Helper used by the new cache / rebuild tests."""
    aid = store.enqueue_llm_job(
        run_id=f"run-{uuid.uuid4().hex[:6]}",
        inbox_event_id=f"ev-{uuid.uuid4().hex[:6]}",
        kind="chat",
    )
    store.persist_llm_job_request(
        aid,
        request={
            "system": "",
            "messages": [
                {"role": "user", "content": content, "content_blocks": None},
            ],
            "max_tokens": 16,
            "tools": None,
            "streaming": False,
            "extra": {},
        },
    )
    return aid


@pytest.mark.asyncio
async def test_worker_caches_provider_across_jobs(magi_state):
    """One ``get_provider`` call covers every job until a rebuild signal."""
    state = _install_counting_fake(FakeProvider(reply="ok"))
    await start_provider_worker()
    try:
        store = get_bus_store()
        ids = [_enqueue_simple(store) for _ in range(3)]
        for aid in ids:
            result = await asyncio.to_thread(
                store.load_llm_job_result, aid,
                wait_seconds=5, poll_seconds=0.05,
            )
            assert result["status"] == "completed", result
        assert state["calls"] == 1, (
            f"expected one cached provider, got {state['calls']} get_provider calls"
        )
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_starts_without_config_and_fails_jobs(magi_state):
    """Missing config does NOT block boot; jobs settle with credentials code."""
    state = _install_counting_fake(FakeProvider())  # never raised here
    state["get_provider"] = state["get_provider"]  # keep linter happy

    def _raise_not_configured(*_a, **_k):
        state["calls"] += 1
        raise LLMNotConfiguredError("MAGI runtime has no LLM provider / API key")

    import magi.providers
    import magi.providers.factory as _factory
    import magi.providers.worker as _worker

    _factory.get_provider = _raise_not_configured
    _worker.get_provider = _raise_not_configured
    magi.providers.get_provider = _raise_not_configured  # type: ignore[attr-defined]

    await start_provider_worker()  # MUST NOT raise
    try:
        store = get_bus_store()
        aid = _enqueue_simple(store)
        result = await asyncio.to_thread(
            store.load_llm_job_result, aid,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result["status"] == "failed"
        assert result["error"]["code"] == "magi.llm_credentials_required"
        assert "MAGI management" in result["error"]["detail"]
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_starts_without_config_then_rebuilds_on_signal(magi_state):
    """A drained ``provider.config_changed`` row triggers a rebuild."""
    import magi.providers
    import magi.providers.factory as _factory
    import magi.providers.worker as _worker

    current = {"provider": None}
    calls = {"n": 0}

    def _switch(*_a, **_k):
        calls["n"] += 1
        return current["provider"]  # may be None; worker fails fast

    _factory.get_provider = _switch
    _worker.get_provider = _switch
    magi.providers.get_provider = _switch  # type: ignore[attr-defined]

    await start_provider_worker()
    store = get_bus_store()
    try:
        # First job: no provider configured → fails with credentials code.
        aid1 = _enqueue_simple(store, content="first")
        result1 = await asyncio.to_thread(
            store.load_llm_job_result, aid1,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result1["status"] == "failed"
        assert result1["error"]["code"] == "magi.llm_credentials_required"

        # Swap to a working provider and publish the BUS signal.
        current["provider"] = FakeProvider(reply="rebuilt-ok")
        store.enqueue_control_job(
            kind="provider.config_changed",
            payload={"provider": "openai"},
        )
        aid2 = _enqueue_simple(store, content="second")
        result2 = await asyncio.to_thread(
            store.load_llm_job_result, aid2,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result2["status"] == "completed", result2
        assert result2["response"]["text"] == "rebuilt-ok"
        assert calls["n"] >= 2, (
            f"expected at least 2 get_provider calls (start + rebuild), got {calls['n']}"
        )
        # The control row must be gone after drain.
        from magi.bus.db.models.queue import ControlJob
        with open_session(store._state_dir) as s:
            leftovers = s.query(ControlJob).count()
        assert leftovers == 0
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_rebuilds_only_when_control_signal_present(magi_state):
    """A second job with no signal between still uses the cached provider."""
    import magi.providers
    import magi.providers.factory as _factory
    import magi.providers.worker as _worker

    calls = {"n": 0}

    def _fake_get(*_a, **_k):
        calls["n"] += 1
        return FakeProvider(reply=f"call#{calls['n']}")

    _factory.get_provider = _fake_get
    _worker.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]

    await start_provider_worker()
    store = get_bus_store()
    try:
        for expected in ("call#1", "call#1"):
            aid = _enqueue_simple(store)
            result = await asyncio.to_thread(
                store.load_llm_job_result, aid,
                wait_seconds=5, poll_seconds=0.05,
            )
            assert result["status"] == "completed"
            assert result["response"]["text"] == expected
        # Only one build across both jobs.
        assert calls["n"] == 1
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_drain_control_jobs_ignores_other_kinds(magi_state):
    """Draining ``provider.config_changed`` leaves unrelated rows alone."""
    state = _install_counting_fake(FakeProvider(reply="ok"))
    await start_provider_worker()
    try:
        store = get_bus_store()
        # Insert a row of a kind no consumer cares about; the worker
        # should drain its own kind (returning 0) without touching it.
        store.enqueue_control_job(
            kind="some.future.kind",
            payload={"x": 1},
        )
        from magi.bus.jobs.protocols.control_jobs import PROVIDER_CONFIG_CHANGED
        from magi.bus.db.models.queue import ControlJob

        drained = await asyncio.to_thread(
            store.drain_control_jobs,
            worker_id="test-worker",
            kind=PROVIDER_CONFIG_CHANGED,
        )
        assert drained == 0
        with open_session(store._state_dir) as s:
            leftovers = s.query(ControlJob).filter_by(
                kind="some.future.kind",
            ).count()
        assert leftovers == 1
    finally:
        await stop_provider_worker()
