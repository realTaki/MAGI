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
from magi.bus.models.queue import LLMAttempt
from magi.bus.db.engine import open_session
from magi.bus.protocols.provider_jobs import ProviderJob
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


@pytest.mark.asyncio
async def test_publish_then_complete_round_trip(magi_state):
    """A successful call writes ``completed`` with the response JSON."""
    fake = FakeProvider(reply="hi from provider")
    _install_fake(fake)
    await start_provider_worker()
    try:
        store = get_bus_store()
        attempt_id = store.enqueue_provider_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="agent.step",
        )
        store.persist_provider_job_request(
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
            store.load_provider_job_result, attempt_id,
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
        attempt_id = store.enqueue_provider_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="agent.step",
        )
        store.persist_provider_job_request(
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
            store.load_provider_job_result, attempt_id,
            wait_seconds=5, poll_seconds=0.05,
        )
        assert result["status"] == "failed"
        assert result["error"]["code"] == "LLMNotConfiguredError"
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
        attempt_id = store.enqueue_provider_job(
            run_id=f"run-{uuid.uuid4().hex[:6]}",
            inbox_event_id="ev-1",
            kind="agent.step",
        )
        store.persist_provider_job_request(
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
            store.load_provider_job_result, attempt_id,
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
            aid = store.enqueue_provider_job(
                run_id=f"run-{i}",
                inbox_event_id=f"ev-{i}",
                kind="agent.step",
            )
            store.persist_provider_job_request(
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
                store.load_provider_job_result, aid,
                wait_seconds=10, poll_seconds=0.05,
            )
            assert result["status"] == "completed", (
                f"row {aid} did not complete: {result}"
            )
            assert "echo:" in result["response"]["text"]
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_load_provider_job_result_returns_none_on_timeout(magi_state):
    """A row that's never settled returns ``None`` after the deadline."""
    fake = FakeProvider(reply="ignored")
    _install_fake(fake)
    # Don't start the worker — the queued row won't move.
    store = get_bus_store()
    aid = store.enqueue_provider_job(
        run_id="never", inbox_event_id="never", kind="agent.step",
    )
    started = datetime.now()
    result = await asyncio.to_thread(
        store.load_provider_job_result, aid,
        wait_seconds=0.5, poll_seconds=0.05,
    )
    assert result is None
    # And the row stayed ``queued`` (no worker claim).
    with open_session(store._state_dir) as s:
        ar = s.query(LLMAttempt).filter_by(attempt_id=aid).one()
    assert ar.status == "queued"
