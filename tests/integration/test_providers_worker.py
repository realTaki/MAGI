"""End-to-end tests for :class:`magi.providers.worker.ProvidersWorker`.

These tests exercise the durable bus queue around the LLM
lifecycle: publish a :class:`CallLLMJob`, watch the worker claim it,
run the (stub) provider, write back a :class:`CallLLMResult`. The
provider is injected via monkey-patching ``get_provider`` on the
factory module (which the worker reaches through at runtime), so the
tests don't depend on real network calls or a real ``settings_book``
configuration.

The integration test stands up a real SQLite-backed :class:`Bus`
in a temp dir so the full round-trip — publish → claim → submit_result
→ get_result — exercises the actual ``BaseJobBoard`` machinery.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from magi.old_bus import Bus, open_bus
from magi.old_bus.firmwares.jobs import (
    CallLLMJob,
    CallLLMResult,
    ChangeProviderConfigJob,
)
from magi.old_bus.bases.job import JobStatus
from magi.old_bus.firmwares.jobs.changeProviderConfigJob import (
    PROVIDER_API_KEY_KEY,
    PROVIDER_MODEL_KEY,
    PROVIDER_NAME_KEY,
)
from magi.old_bus.provision import provision_node_storage
from magi.providers.base import LLMProvider, LLMStreamEvent
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.worker import ProvidersWorker

pytestmark = pytest.mark.skip(
    reason="ProvidersWorker now attaches through magi.launcher and magi.new_bus"
)

_worker: ProvidersWorker | None = None


async def start_provider_worker(bus: Bus) -> ProvidersWorker:
    """Test-local lifecycle helper; production ownership is startup-only."""
    global _worker
    _worker = ProvidersWorker(bus)
    await _worker.start()
    return _worker


async def stop_provider_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


# ---------------------------------------------------------------------------
# Fake provider + helpers
# ---------------------------------------------------------------------------


class FakeProvider(LLMProvider):
    """Minimal provider used by every test in this file.

    Honours ``reply`` (string) when set; otherwise echoes the
    last user message back so the assertion can spot-check the
    round-trip. Raises ``LLMError`` / ``LLMNotConfiguredError`` on
    messages prefixed with ``!raise:`` / ``!notconfigured:`` so a
    test can drive the failure paths deterministically.
    """

    name = "fake"

    def __init__(self, *, reply: str = "", fail_message: str = "") -> None:
        super().__init__(api_key="dummy")
        self.reply = reply
        self.fail_message = fail_message
        self.call_count = 0

    def default_model(self) -> str:
        return "fake-model-1"

    async def chat(
        self,
        *,
        system: str | None,
        messages: list[dict],
        max_tokens: int,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        _ = system, max_tokens, tools
        self.call_count += 1
        last_content = self._last_user_text(messages)
        if self.fail_message and last_content.startswith("!raise"):
            raise LLMError(self.fail_message)
        if last_content.startswith("!notconfigured"):
            raise LLMNotConfiguredError(self.fail_message or "not configured")
        text = self.reply or f"echo:{last_content}"
        return {
            "text": text,
            "thinking": None,
            "tool_uses": [],
            "raw_blocks": [],
            "model": self.default_model(),
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }

    async def stream(
        self,
        *,
        system: str | None,
        messages: list[dict],
        max_tokens: int,
        tools: list[dict] | None = None,
    ) -> Any:
        _ = system, max_tokens, tools
        # Default-stream shape (matches ``LLMProvider.stream`` base impl):
        # one ``text.delta`` per chunk of reply, then a single
        # ``usage.updated`` terminal.
        last_content = self._last_user_text(messages)
        text = self.reply or f"echo:{last_content}"
        yield LLMStreamEvent("text.delta", {"text": text})
        yield LLMStreamEvent(
            "usage.updated",
            {
                "text": text,
                "thinking": None,
                "tool_uses": [],
                "raw_blocks": [],
                "model": self.default_model(),
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        )

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    return content
        return ""


@pytest.fixture
def bus(tmp_path) -> Bus:
    """Stand up a per-test SQLite-backed :class:`Bus`."""
    state_dir = tmp_path / "memories"
    provision_node_storage(state_dir=str(state_dir), magis_url=None)
    return open_bus(workspace_dir=str(tmp_path))


def _seed_provider_config(
    bus: Bus,
    *,
    provider: str = "openai",
    api_key: str = "sk-test",
    model: str = "fake-model-1",
) -> None:
    """Write the three provider-config rows into ``settings_book``."""
    bus.settings_book.set(key=PROVIDER_NAME_KEY, value=provider)
    bus.settings_book.set(key=PROVIDER_API_KEY_KEY, value=api_key)
    bus.settings_book.set(key=PROVIDER_MODEL_KEY, value=model)


def _install_fake(bus: Bus, fake: FakeProvider) -> None:
    """Patch the ``get_provider`` symbol the worker resolves at runtime.

    The worker does ``from magi.providers import get_provider`` (which
    re-exports from :mod:`magi.providers.factory`), so we patch both
    names — whichever symbol the worker resolves first wins, but if
    someone calls the factory module directly they should still see
    the fake.  Rebinding the package attribute also handles legacy
    monkey-patch seams.
    """
    import magi.providers
    import magi.providers.factory as _factory

    def _fake_get(*, bus: Bus, model: str | None = None) -> LLMProvider:
        return fake

    _factory.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]


def _install_counter(bus: Bus, fake: FakeProvider) -> dict[str, int]:
    """Same as ``_install_fake`` but tracks how many times the factory was hit."""
    import magi.providers
    import magi.providers.factory as _factory

    state: dict[str, int] = {"calls": 0}

    def _fake_get(*, bus: Bus, model: str | None = None) -> LLMProvider:
        state["calls"] += 1
        return fake

    _factory.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]
    return state


async def _wait_for_result(
    bus: Bus,
    job_id: int,
    *,
    timeout: float = 5.0,
    poll: float = 0.05,
) -> CallLLMResult | None:
    """Poll ``bus.llm_job_board.get_result`` until the job settles or times out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await asyncio.to_thread(bus.llm_job_board.get_result, job_id=job_id)
        if result is not None:
            return result
        await asyncio.sleep(poll)
    return None


def _enqueue_simple(bus: Bus, *, content: str = "hello") -> int:
    """Publish a minimal chat job (no tools, no streaming)."""
    return bus.llm_job_board.publish(
        CallLLMJob(
            messages=[{"role": "user", "content": content}],
            max_tokens=16,
        )
    )


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_then_complete_round_trip(bus: Bus):
    """A successful call settles the row with success=True and the response dict."""
    fake = FakeProvider(reply="hi from provider")
    _install_fake(bus, fake)
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        job_id = _enqueue_simple(bus, content="hello")
        result = await _wait_for_result(bus, job_id)
        assert result is not None, "worker did not settle the job in time"
        assert result.status == JobStatus.COMPLETED
        assert result.response is not None
        assert result.response["text"] == "hi from provider"
        assert result.model == "fake-model-1"
        assert fake.call_count == 1
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_provider_not_configured_envelopes_failure(bus: Bus):
    """A ``LLMNotConfiguredError`` settles with the credentials error_code."""
    fake = FakeProvider(fail_message="no api key in magi row")
    _install_fake(bus, fake)
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        job_id = _enqueue_simple(bus, content="!notconfigured")
        result = await _wait_for_result(bus, job_id)
        assert result is not None
        assert result.status == JobStatus.FAILED
        # The worker special-cases :class:`LLMNotConfiguredError` and
        # surfaces it as the stable ``magi.llm_credentials_required``
        # operator-facing code (so admin UX doesn't depend on the
        # internal Python class name).
        assert result.error_code == "magi.llm_credentials_required"
        assert "no api key" in (result.error or "")
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_provider_crashed_envelopes_generic_failure(bus: Bus):
    """An ``LLMError`` settles with the typed exception name as error_code."""
    fake = FakeProvider(fail_message="upstream auth failed")
    _install_fake(bus, fake)
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        job_id = _enqueue_simple(bus, content="!raise:anything")
        result = await _wait_for_result(bus, job_id)
        assert result is not None
        assert result.status == JobStatus.FAILED
        assert result.error_code == "LLMError"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_concurrency_limit_serialises_two_jobs(bus: Bus):
    """Two queued jobs each get a turn; the semaphore caps parallel calls."""
    fake = FakeProvider(reply="ok")
    _install_fake(bus, fake)
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        ids = [_enqueue_simple(bus, content=f"hello {i}") for i in range(2)]
        for jid in ids:
            result = await _wait_for_result(bus, jid, timeout=10.0)
            assert result is not None and result.status == JobStatus.COMPLETED, result
            assert result.response["text"] == "ok"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_load_llm_job_result_returns_none_on_timeout(bus: Bus):
    """A row that's never settled returns ``None`` after the deadline.

    We don't start the worker here — the queued job stays pending and
    ``get_result`` never sees a terminal status.
    """
    fake = FakeProvider(reply="ignored")
    _install_fake(bus, fake)
    job_id = _enqueue_simple(bus, content="never")
    result = await _wait_for_result(bus, job_id, timeout=0.5)
    assert result is None


# ---------------------------------------------------------------------------
# Cache / rebuild semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_caches_provider_across_jobs(bus: Bus):
    """One ``get_provider`` call covers every job until a rebuild signal."""
    state = _install_counter(bus, FakeProvider(reply="ok"))
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        ids = [_enqueue_simple(bus) for _ in range(3)]
        for jid in ids:
            result = await _wait_for_result(bus, jid)
            assert result is not None and result.status == JobStatus.COMPLETED
        assert state["calls"] == 1, (
            f"expected one cached provider, got {state['calls']} get_provider calls"
        )
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_starts_without_config_and_fails_jobs(bus: Bus):
    """Missing config does NOT block boot; jobs settle with the credentials code."""
    import magi.providers
    import magi.providers.factory as _factory

    def _raise_not_configured(*_a, **_k):
        raise LLMNotConfiguredError("MAGI runtime has no LLM provider / API key configured")

    _factory.get_provider = _raise_not_configured
    magi.providers.get_provider = _raise_not_configured  # type: ignore[attr-defined]
    # No settings_book writes — the worker reads and finds nothing.
    await start_provider_worker(bus)  # MUST NOT raise
    try:
        job_id = _enqueue_simple(bus)
        result = await _wait_for_result(bus, job_id)
        assert result is not None
        assert result.status == JobStatus.FAILED
        assert result.error_code == "magi.llm_credentials_required"
        assert "MAGI" in (result.error or "")
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_starts_without_config_then_rebuilds_on_signal(bus: Bus):
    """A drained ``ChangeProviderConfigJob`` triggers a rebuild."""
    import magi.providers
    import magi.providers.factory as _factory

    state: dict[str, Any] = {"provider": None}
    calls = {"n": 0}

    def _switch(*, bus: Bus, model: str | None = None) -> LLMProvider | None:
        calls["n"] += 1
        return state["provider"]  # may be None on the first claim

    _factory.get_provider = _switch
    magi.providers.get_provider = _switch  # type: ignore[attr-defined]

    await start_provider_worker(bus)
    try:
        # 1. No provider configured → first job settles with credentials code.
        jid1 = _enqueue_simple(bus, content="first")
        r1 = await _wait_for_result(bus, jid1)
        assert r1 is not None
        assert r1.status == JobStatus.FAILED
        assert r1.error_code == "magi.llm_credentials_required"

        # 2. Publish a ChangeProviderConfigJob — the board writes
        #    ``settings_book`` and enqueues the rebuild job in one
        #    self-contained write.
        state["provider"] = FakeProvider(reply="rebuilt-ok")
        bus.change_provider_config_job_board.publish(
            ChangeProviderConfigJob(
                provider="openai",
                api_key="sk-test",
                model="fake-model-1",
            )
        )

        # 3. Next job should now use the rebuilt provider.
        jid2 = _enqueue_simple(bus, content="second")
        r2 = await _wait_for_result(bus, jid2)
        assert r2 is not None and r2.status == JobStatus.COMPLETED, r2
        assert r2.response["text"] == "rebuilt-ok"
        assert calls["n"] >= 2, (
            f"expected at least 2 get_provider calls (start + rebuild), got {calls['n']}"
        )
        # The config-change job was drained (status advanced past pending).
        from sqlalchemy import select

        from magi.old_bus.firmwares.jobs.changeProviderConfigJob import _ChangeProviderConfigRow

        with bus._local_factory.session() as s:
            leftovers = s.scalar(
                select(_ChangeProviderConfigRow.status).where(
                    _ChangeProviderConfigRow.status.in_([JobStatus.PENDING, JobStatus.PROCESSING])
                )
            )
        assert leftovers is None, "config-change job was not drained"
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_rebuilds_only_when_control_signal_present(bus: Bus):
    """A second job with no signal between still uses the cached provider."""
    import magi.providers
    import magi.providers.factory as _factory

    calls = {"n": 0}

    def _fake_get(*, bus: Bus, model: str | None = None) -> LLMProvider:
        calls["n"] += 1
        return FakeProvider(reply=f"call#{calls['n']}")

    _factory.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]
    _seed_provider_config(bus)

    await start_provider_worker(bus)
    try:
        for expected in ("call#1", "call#1"):
            jid = _enqueue_simple(bus)
            r = await _wait_for_result(bus, jid)
            assert r is not None and r.status == JobStatus.COMPLETED
            assert r.response["text"] == expected
        # Only one build across both jobs.
        assert calls["n"] == 1
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_updates_model_in_place_when_only_model_changes(bus: Bus):
    """Provider / api_key trigger a rebuild; a model-only change does not.

    The SDK clients (Anthropic / OpenAI) only read ``model`` per call,
    so a model change is effectively a string swap on the cached
    provider — there is no reason to tear down the SDK client
    (and its HTTP connection pool) just to flip a model id.

    The worker must:

    1. Apply the new model to the cached provider in place.
    2. Skip ``get_provider`` entirely (no rebuild).
    3. Subsequent ``CallLLMJob`` calls observe the new model.
    """
    import magi.providers
    import magi.providers.factory as _factory

    class ModelReportingProvider(FakeProvider):
        """Fake whose reply + response.model reflect the live ``self.model``."""

        async def chat(
            self,
            *,
            system: str | None,
            messages: list[dict],
            max_tokens: int,
            tools: list[dict] | None = None,
        ) -> dict[str, Any]:
            _ = system, messages, max_tokens, tools
            self.call_count += 1
            text = f"model={self.model}"
            return {
                "text": text,
                "thinking": None,
                "tool_uses": [],
                "raw_blocks": [],
                "model": self.model,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            }

    calls = {"n": 0}

    def _fake_get(*, bus: Bus, model: str | None = None) -> LLMProvider:
        calls["n"] += 1
        return ModelReportingProvider()

    _factory.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]
    _seed_provider_config(bus, model="fake-model-1")

    worker = await start_provider_worker(bus)
    try:
        # 1. First job warms the cache with the initial model.
        r1 = await _wait_for_result(bus, _enqueue_simple(bus, content="first"))
        assert r1 is not None and r1.status == JobStatus.COMPLETED, r1
        assert r1.response["text"] == "model=fake-model-1"
        assert calls["n"] == 1
        assert worker._provider is not None
        assert worker._provider.model == "fake-model-1"

        # 2. Publish a model-only ChangeProviderConfigJob — ``provider``
        #    and ``api_key`` are both None, so the worker must NOT
        #    rebuild the SDK client.
        bus.change_provider_config_job_board.publish(ChangeProviderConfigJob(model="fake-model-2"))

        # 3. Wait for the worker to drain the config job and swap
        #    ``provider.model`` in place.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            cached = getattr(worker, "_provider", None)
            if cached is not None and cached.model == "fake-model-2":
                break
            await asyncio.sleep(0.05)
        else:  # pragma: no cover — explicit failure path
            pytest.fail("model was not updated in place on cached provider")

        # 4. Next LLM job must observe the new model without rebuild.
        r2 = await _wait_for_result(bus, _enqueue_simple(bus, content="second"))
        assert r2 is not None and r2.status == JobStatus.COMPLETED, r2
        assert r2.response["text"] == "model=fake-model-2", (
            f"model-only change should propagate in place; got {r2.response['text']!r}"
        )

        # 5. get_provider must NOT be called again — model-only skips rebuild.
        assert calls["n"] == 1, (
            f"model-only change should not rebuild; got {calls['n']} get_provider calls"
        )
    finally:
        await stop_provider_worker()


@pytest.mark.asyncio
async def test_worker_rebuilds_when_provider_field_changes(bus: Bus):
    """Switching provider must rebuild the SDK client (different base_url / SDK).

    Counterpart to the model-only fast path: any non-None
    ``provider`` or ``api_key`` field should fall back to the
    full rebuild, even if ``model`` is also set in the same job.
    """
    import magi.providers
    import magi.providers.factory as _factory

    calls = {"n": 0}

    def _fake_get(*, bus: Bus, model: str | None = None) -> LLMProvider:
        calls["n"] += 1
        return FakeProvider(reply=f"call#{calls['n']}")

    _factory.get_provider = _fake_get
    magi.providers.get_provider = _fake_get  # type: ignore[attr-defined]
    _seed_provider_config(bus)

    await start_provider_worker(bus)
    try:
        # Baseline: first job warms the cache.
        r1 = await _wait_for_result(bus, _enqueue_simple(bus, content="first"))
        assert r1 is not None and r1.status == JobStatus.COMPLETED
        assert r1.response["text"] == "call#1"
        assert calls["n"] == 1

        # Switch provider — model field is also set, but the
        # presence of ``provider`` is enough to force a rebuild.
        bus.change_provider_config_job_board.publish(
            ChangeProviderConfigJob(
                provider="openai",
                api_key="sk-test",
                model="fake-model-1",
            )
        )

        # Wait for the new provider to take effect.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            r2 = await _wait_for_result(
                bus,
                _enqueue_simple(bus, content="second"),
            )
            if r2 is not None and r2.response["text"] == "call#2":
                break
        else:  # pragma: no cover
            pytest.fail("provider change did not rebuild the SDK client")

        # Two get_provider calls: initial build + rebuild.
        assert calls["n"] == 2, (
            f"provider change should rebuild; got {calls['n']} get_provider calls"
        )
    finally:
        await stop_provider_worker()


# ---------------------------------------------------------------------------
# providers.options publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_publishes_provider_options_to_settings_book(bus: Bus):
    """On boot the worker writes the supported-provider list to ``settings_book``."""
    await start_provider_worker(bus)
    try:
        import json

        raw = bus.settings_book.get_value(key="providers.options")
        assert raw is not None
        options = json.loads(raw)
        pairs = {(row["provider"], row["model"]) for row in options}
        assert {
            ("claude", "claude-fable-5"),
            ("claude", "claude-opus-5"),
            ("minimax-cn", "MiniMax-M3"),
            ("minimax-global", "MiniMax-M3"),
            ("openai", "gpt-5.6-sol"),
            ("openai", "gpt-5.6-terra"),
            ("openai", "gpt-5.6-luna"),
        } <= pairs
    finally:
        await stop_provider_worker()


# ---------------------------------------------------------------------------
# Stream mode round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_job_publishes_deltas_and_terminal(bus: Bus):
    """A streaming job publishes text deltas to StreamHub and yields a final result."""
    fake = FakeProvider(reply="hello there")
    _install_fake(bus, fake)
    _seed_provider_config(bus)
    await start_provider_worker(bus)
    try:
        job_id = bus.llm_job_board.publish(
            CallLLMJob(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
                streaming=True,
            )
        )
        result = await _wait_for_result(bus, job_id)
        assert result is not None and result.status == JobStatus.COMPLETED
        assert result.response["text"] == "hello there"
        # ``stream_key`` is non-empty in streaming mode — the consumer
        # can pull incremental deltas from ``bus.stream_hub.get(key)``.
        assert result.stream_key, "streaming result should carry a stream_key"
        # The StreamHub pipe exists and was closed after the worker drained it.
        pipe = bus.stream_hub.get(result.stream_key)
        assert pipe is None, "stream hub pipe should be cleaned up after drain"
    finally:
        await stop_provider_worker()
