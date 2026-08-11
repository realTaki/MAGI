"""ProvidersWorker — bus 上唯一的 LLM 调用执行点。

设计原则
========

- **只依赖 bus**。老的 bus store / StreamHub / chat_jobs 一概
  不碰。Agent 暂时不会感知 LLM 完成事件（等它迁到 bus 再说）。

- **配置变更单一触发**：worker 只在 claim 到
  ``bus.change_provider_config_job_board`` 上的 job 时才重建
  SDK client。``publish()`` 已经 self-contained write 了
  ``settings_book``，调用方 publish 一次 = 改 settings + 排队。
  worker 不做漂移轮询 —— 避免 DB 一变就热 build、紧接着
  claim 到 job 又 rebuild 的双重重置。

- **dumb invoker**。Worker 不知道这次调用来自 agent turn /
  compaction / auto_title —— 全部统一走 :class:`CallLLMJob` → SDK →
  :class:`CallLLMResult`。调用方需要区分由调用方在自己的层做。

- **stream = async iterator**。``provider.stream()`` 返回
  ``AsyncIterator[LLMStreamEvent]``，worker 内部 iterate 聚合文本
  和 tool_use，最后写一个 :class:`CallLLMResult`。不需要任何外部
  消息队列（StreamHub 已退役）。

- **provider 列表 publish**：startup 时 worker 把当前代码支持的
  provider id 列表写到 ``bus.settings``（key ``providers.options``），
  WebUI 从 Book 读，不 import :mod:`magi.providers`。

入队 helper
===========

旧的 :func:`enqueue_llm_job` helper 已删除。调用方直接
``bus.llm_job_board.publish(CallLLMJob(...))``。Agent / tool 等模块
目前还在老 bus 上，会暂时坏掉——按 user 指示"以后再修"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from magi.bus.guild import (
    CallLLMJob,
    CallLLMResult,
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
)
from magi.providers.base import LLMProvider, LLMStreamEvent
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.runtime_worker import RuntimeWorker

if TYPE_CHECKING:
    from magi.bus import Bus

logger = logging.getLogger("magi.providers.worker")

# Backpressure cap. Two parallel upstream calls keep latency low
# while leaving room for a streaming job to take a long time
# without starving shorter turns. Injected via the ``concurrency``
# constructor parameter; there is no environment-variable knob.
_DEFAULT_CONCURRENCY = 2

# Stable short codes for the operator-facing error envelope.
_NOT_CONFIGURED_CODE = "magi.llm_credentials_required"
_PROVIDER_CRASHED_CODE = "chat.provider_crashed"

# Setting key under which the worker publishes the supported-provider
# list (id + human label). WebUI reads this from ``bus.settings_book``
# — it never imports :mod:`magi.providers` for the dropdown source.
_PROVIDERS_KEY = "providers.options"

# The provider-list payload is code-defined: it never changes between
# worker reloads, so writing it once at startup is enough. If a new
# provider ships, the next worker start re-publishes.
_PROVIDER_OPTIONS: list[dict[str, str]] = [
    {"value": "claude", "label": "Anthropic (Claude)"},
    {"value": "minimax-global", "label": "Minimax (Global)"},
    {"value": "minimax-cn", "label": "Minimax (China)"},
    {"value": "openai", "label": "OpenAI"},
]


class ProvidersWorker(RuntimeWorker):
    """Consumer that owns every LLM API call in a MAGI process.

    Receives a fully-wired :class:`Bus` via constructor injection.
    """

    worker_name = "providers"

    def __init__(
        self,
        bus: Bus,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        # Concurrency is constructor-injected only — this worker does
        # no env reads. A worker reaching into ``os.environ`` makes its
        # behaviour untestable and invisible to the composition root,
        # so the knob is a constructor parameter and nothing else.
        # Mirrors :class:`~magi.tools.worker.ToolsWorker`.
        self.concurrency = max(1, concurrency or _DEFAULT_CONCURRENCY)
        self._slots = asyncio.Semaphore(self.concurrency)
        self._inflight: set[asyncio.Task[None]] = set()

        # Cached LLM client + the typed error that prevented us from
        # building one. The cache is refreshed only on a claimed
        # ``ChangeProviderConfigJob`` — never by drift polling.
        self._provider: LLMProvider | None = None
        self._provider_error: LLMError | None = None

    async def on_start(self) -> None:
        # Publish supported-provider list to bus.settings so the
        # WebUI can read it from a Book without importing
        # :mod:`magi.providers`. Fire-and-forget — failure here
        # doesn't block boot.
        await self.call(self._publish_provider_options)

        # Build the cached provider client from current config.
        await self.call(self._rebuild_provider)

    async def on_stopped(self) -> None:
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()
        self._provider = None
        self._provider_error = None

    # ----- main loop ----------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            # 1. Drain any explicitly-published config-change job.
            #    The api channel doesn't publish yet (still on old
            #    bus), but this is the future hook.
            cfg_job = await self.call(
                self.bus.change_provider_config_job_board.claim,
            )
            if cfg_job is not None:
                await self._handle_config_job(cfg_job)
                continue

            # 2. Claim one LLM job.
            job = await self.call(
                self.bus.llm_job_board.claim,
            )
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            await self._slots.acquire()
            task_obj = self.spawn(
                self._invoke_safe(job),
                name=f"provider-job-{job.job_id}",
            )
            self._inflight.add(task_obj)
            task_obj.add_done_callback(self._inflight.discard)

    # ----- config change -------------------------------------------------

    async def _handle_config_job(self, job: ChangeProviderConfigJob) -> None:
        """Apply a config-change job to the cached provider.

        ``provider`` / ``api_key`` change the SDK client (vendor,
        base_url, auth) and require a full rebuild via
        :meth:`_rebuild_provider`. ``model`` is only a per-call
        parameter on the SDK — the SDK clients (Anthropic / OpenAI)
        read it from ``self.model`` at every call, so a model-only
        change can be propagated in place via :meth:`_update_model`,
        skipping the cost of tearing down the HTTP connection pool.
        """
        if job.provider is not None or job.api_key is not None:
            logger.info(
                "providers worker: changeProviderConfig (provider/auth) — rebuilding client"
            )
            await self.call(self._rebuild_provider)
        elif job.model is not None and self._provider is not None:
            # Pure model change: the SDK client is unaffected.
            await self.call(self._update_model, job.model)
        else:
            # Either model came in without a cached provider (try
            # to bootstrap from the freshly-written settings_book),
            # or no fields are set at all. Rebuild either way —
            # it's cheap and matches the previously-missing-then-
            # fixed state.
            logger.info(
                "providers worker: changeProviderConfig (bootstrap / no-op) — rebuilding client"
            )
            await self.call(self._rebuild_provider)

        if self._provider is not None:
            result = ChangeProviderConfigResult(job_id=job.job_id, success=True)
        else:
            result = ChangeProviderConfigResult(
                job_id=job.job_id,
                success=False,
                error=str(self._provider_error) if self._provider_error else "unknown",
            )
        try:
            await self.call(
                self.bus.change_provider_config_job_board.submit_result,
                key=job.job_id,
                result=result,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "providers worker: failed to submit changeProviderConfig result for %s",
                job.job_id,
            )

    def _rebuild_provider(self) -> None:
        """(Re)build the cached :class:`LLMProvider` from current config.

        Only invoked when the incoming config-change job touches
        ``provider`` or ``api_key`` — both seal the SDK client
        (vendor, base_url, auth).  A ``model``-only change is
        fast-pathed through :meth:`_update_model` because the SDK
        clients only read ``model`` per call.

        Never raises: a missing / invalid config logs once and leaves
        ``self._provider = None`` so the next claimed job settles
        with the operator-facing envelope instead of crashing the
        worker.
        """
        from magi.providers import get_provider

        try:
            provider = get_provider(bus=self.bus)
        except LLMNotConfiguredError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning(
                "providers worker: no LLM configured (%s); jobs will fail-fast",
                exc,
            )
        except LLMError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning(
                "providers worker: cannot build LLM (%s); jobs will fail-fast",
                exc,
            )
        else:
            self._provider = provider
            self._provider_error = None
            logger.info(
                "providers worker: cached LLM client (%s)",
                type(provider).__name__,
            )

    def _update_model(self, model: str) -> None:
        """Swap ``provider.model`` on the cached provider in place.

        The SDK clients (Anthropic / OpenAI) only read ``model`` per
        call, so a model change does not require destroying the
        client — that would tear down the HTTP connection pool
        for what's effectively a string swap.

        The caller is expected to have already verified
        ``self._provider is not None``; the assert exists so a
        future refactor that breaks that invariant fails loudly
        instead of silently AttributeError-ing.
        """
        assert self._provider is not None, (
            "_update_model requires a cached provider; caller must guard before calling"
        )
        self._provider.model = model
        logger.info(
            "providers worker: updated cached model to %r (no rebuild)",
            model,
        )

    # ----- provider-options publishing ----------------------------------

    def _publish_provider_options(self) -> None:
        """Write supported-provider list to ``bus.settings_book``.

        The data is code-defined: a new provider ships when this
        module is updated, so re-publishing on every worker start is
        enough — no live reload needed. WebUI reads this key from
        the same ``settings_book`` and never imports
        :mod:`magi.providers`.
        """
        sb = getattr(self.bus, "settings_book", None)
        if sb is None:
            return
        try:
            sb.set(
                key=_PROVIDERS_KEY,
                value=json.dumps(_PROVIDER_OPTIONS, ensure_ascii=False),
            )
            logger.info(
                "providers worker: published known providers to bus.settings_book[%s]",
                _PROVIDERS_KEY,
            )
        except Exception:  # noqa: BLE001
            logger.exception("providers worker: failed to publish known providers")

    # ----- LLM invocation -----------------------------------------------

    async def _invoke_safe(self, job: CallLLMJob) -> None:
        try:
            await self._invoke_provider(job)
        except asyncio.CancelledError:
            await self._safe_submit_failure(
                job,
                error_code="magi.run_cancelled",
                error_detail="providers worker cancelled",
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- worker MUST NOT crash
            logger.exception(
                "providers worker: unhandled exception on job %s",
                job.job_id,
            )
            await self._safe_submit_failure(
                job,
                error_code=_PROVIDER_CRASHED_CODE,
                error_detail=str(exc),
            )
        finally:
            self._slots.release()

    async def _invoke_provider(self, job: CallLLMJob) -> None:
        """Deserialize the job, call the provider, submit the result."""
        # Resolve cached provider.
        provider = self._provider
        if provider is None:
            exc = self._provider_error or LLMNotConfiguredError(
                "MAGI runtime has no LLM provider configured"
            )
            error_code = (
                _NOT_CONFIGURED_CODE
                if isinstance(exc, LLMNotConfiguredError)
                else type(exc).__name__
            )
            await self._safe_submit_failure(
                job,
                error_code=error_code,
                error_detail=str(exc),
            )
            return

        messages = list(job.messages or [])
        max_tokens = int(job.max_tokens or 1024)
        tools = job.tools or None
        streaming = bool(job.streaming)

        # Wire format is ``list[dict]``; system prompt is the first
        # message with ``role="system"`` (caller's convention).
        system: str | None = None
        chat_messages: list[dict] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            if system is None and m.get("role") == "system":
                system = m.get("content") or ""
                continue
            chat_messages.append(m)

        try:
            if streaming:
                result_dict = await self._consume_stream(
                    provider=provider,
                    system=system,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    tools=tools,
                )
            else:
                result_dict = await provider.chat(
                    system=system,
                    messages=chat_messages,
                    max_tokens=max_tokens,
                    tools=tools,
                )
        except LLMNotConfiguredError as exc:
            await self._safe_submit_failure(
                job,
                error_code=_NOT_CONFIGURED_CODE,
                error_detail=str(exc),
            )
            return
        except LLMError as exc:
            await self._safe_submit_failure(
                job,
                error_code=type(exc).__name__,
                error_detail=str(exc),
            )
            return

        result = CallLLMResult(
            job_id=job.job_id,
            success=True,
            response={
                "text": result_dict.get("text") or "(empty reply)",
                "thinking": result_dict.get("thinking"),
                "tool_uses": list(result_dict.get("tool_uses") or []),
                "raw_blocks": list(result_dict.get("raw_blocks") or []),
            },
            finish_reason=result_dict.get("stop_reason"),
            token_usage=result_dict.get("usage"),
            model=result_dict.get("model") or "",
            stream_key=result_dict.get("stream_key") or "",
        )
        try:
            await self.call(self.bus.llm_job_board.submit_result, key=job.job_id, result=result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "providers worker: failed to submit llm result for %s",
                job.job_id,
            )

    async def _consume_stream(
        self,
        *,
        provider: LLMProvider,
        system: str | None,
        messages: list[dict],
        max_tokens: int,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        """Drain ``provider.stream()``, push text deltas to StreamHub.

        Returns a dict with the complete result plus ``stream_key``
        so the caller can read incremental text from
        ``bus.stream_hub.get(stream_key)``.

        Fallback semantics
        ------------------

        If the provider stream yields *no* ``usage.updated`` terminal
        event (e.g. an SDK bug returns an empty stream, or the iterator
        is closed before the trailing payload), we transparently fall
        back to a non-streaming ``provider.chat()``. The caller is
        billed for exactly one request — the failed stream attempt is
        not separately charged — and the resulting dict carries no
        ``stream_key`` because no incremental text was ever published.

        Two caveats worth noting in the audit log:

        - No timeout protection on this fallback today. The SDK call
          uses its own ``timeout=30s`` (see provider base classes),
          which bounds the wait but doesn't surface a distinct error
          envelope for the fallback path.
        - Callers relying on ``stream_key`` for live UX *will not see
          anything* in that case. If that becomes a problem, swap the
          fallback for a hard error instead.
        """
        import uuid as _uuid

        stream_key = _uuid.uuid4().hex
        q: asyncio.Queue[Any] = self.bus.stream_hub.create(stream_key)

        iterator: AsyncIterator[LLMStreamEvent] = provider.stream(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            tools=tools,
        )
        terminal: dict[str, Any] | None = None
        try:
            async for event in iterator:
                if event.kind == "text.delta":
                    text = event.payload.get("text")
                    if text:
                        q.put_nowait(text)
                elif event.kind == "usage.updated":
                    terminal = event.payload
        finally:
            q.put_nowait(None)  # sentinel — consumer stops
            self.bus.stream_hub.close(stream_key)

        if terminal is None:
            logger.warning(
                "providers worker: stream yielded no usage.updated for stream_key=%s; "
                "falling back to non-streaming chat()",
                stream_key,
            )
            return await provider.chat(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
            )
        return {
            "text": terminal.get("text") or "(empty reply)",
            "thinking": terminal.get("thinking"),
            "tool_uses": list(terminal.get("tool_uses") or []),
            "raw_blocks": list(terminal.get("raw_blocks") or []),
            "model": terminal.get("model") or "",
            "usage": terminal.get("usage"),
            "stop_reason": terminal.get("stop_reason"),
            "stream_key": stream_key,
        }

    # ----- helpers ------------------------------------------------------

    async def _safe_submit_failure(
        self,
        job: CallLLMJob,
        *,
        error_code: str,
        error_detail: str,
    ) -> None:
        """Submit a failed :class:`CallLLMResult`. Swallows errors so
        the worker loop never crashes on a transient DB blip."""
        result = CallLLMResult(
            job_id=job.job_id,
            success=False,
            error=error_detail,
            error_code=error_code,
        )
        try:
            await self.call(self.bus.llm_job_board.submit_result, key=job.job_id, result=result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "providers worker: failed to submit failure for %s",
                job.job_id,
            )


__all__ = ["ProvidersWorker"]
