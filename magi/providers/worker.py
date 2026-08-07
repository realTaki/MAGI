"""ProvidersWorker — new_bus 上唯一的 LLM 调用执行点。

设计原则
========

- **只依赖 new_bus**。老的 bus store / StreamHub / agent_inbox 一概
  不碰。Agent 暂时不会感知 LLM 完成事件（等它迁到 new_bus 再说）。

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
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from magi.new_bus.guild import (
    CallLLMJob,
    CallLLMResult,
    ChangeProviderConfigJob,
    ChangeProviderConfigResult,
)
from magi.providers.errors import LLMError, LLMNotConfiguredError
from magi.providers.base import LLMProvider, LLMStreamEvent

if TYPE_CHECKING:
    from magi.new_bus import NewBus

logger = logging.getLogger("magi.providers.worker")

# Backpressure cap. Two parallel upstream calls keep latency low
# while leaving room for a streaming job to take a long time
# without starving shorter turns. Override via ``MAGI_PROVIDER_CONCURRENCY``.
_DEFAULT_CONCURRENCY = 2

# Stable short codes for the operator-facing error envelope.
_NOT_CONFIGURED_CODE = "magi.llm_credentials_required"
_PROVIDER_CRASHED_CODE = "chat.provider_crashed"

# Setting key under which the worker publishes the supported-provider
# list (id + human label). WebUI reads this from ``bus.settings`` —
# it never imports :mod:`magi.providers` for the dropdown source.
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


class ProvidersWorker:
    """Consumer that owns every LLM API call in a MAGI process.

    Receives a fully-wired :class:`NewBus` via constructor injection.
    """

    def __init__(
        self,
        bus: "NewBus",
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        self.bus = bus
        self.worker_id = f"provider-{uuid.uuid4().hex}"
        self.poll_seconds = poll_seconds
        if concurrency is None:
            try:
                concurrency = int(
                    os.environ.get("MAGI_PROVIDER_CONCURRENCY", _DEFAULT_CONCURRENCY),
                )
            except ValueError:
                concurrency = _DEFAULT_CONCURRENCY
        self.concurrency = max(1, concurrency)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._slots = asyncio.Semaphore(self.concurrency)
        self._inflight: set[asyncio.Task[None]] = set()

        # Cached LLM client + the typed error that prevented us from
        # building one. The cache is refreshed only on a claimed
        # ``ChangeProviderConfigJob`` — never by drift polling.
        self._provider: LLMProvider | None = None
        self._provider_error: LLMError | None = None

    async def start(self) -> None:
        if self._task is not None:
            return

        # Publish supported-provider list to bus.settings so the
        # WebUI can read it from a Book without importing
        # :mod:`magi.providers`. Fire-and-forget — failure here
        # doesn't block boot.
        self._publish_provider_options()

        # Build the cached provider client from current config.
        self._rebuild_provider()

        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="magi-provider-worker"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
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
            cfg_job = await asyncio.to_thread(
                self.bus.change_provider_config_job_board.claim,
                worker_id=self.worker_id,
            )
            if cfg_job is not None:
                await self._handle_config_job(cfg_job)
                continue

            # 2. Claim one LLM job.
            job = await asyncio.to_thread(
                self.bus.llm_job_board.claim, worker_id=self.worker_id,
            )
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue

            await self._slots.acquire()
            task_obj = asyncio.create_task(
                self._invoke_safe(job),
                name=f"provider-job-{job.job_id}",
            )
            self._inflight.add(task_obj)
            task_obj.add_done_callback(self._inflight.discard)

    # ----- config change -------------------------------------------------

    async def _handle_config_job(self, job: ChangeProviderConfigJob) -> None:
        """Rebuild the cached provider client on a config-change job."""
        logger.info(
            "providers worker: changeProviderConfig received, rebuilding client"
        )
        self._rebuild_provider()
        if self._provider is not None:
            result = ChangeProviderConfigResult(job_id=job.job_id, success=True)
        else:
            result = ChangeProviderConfigResult(
                job_id=job.job_id, success=False,
                error=str(self._provider_error) if self._provider_error else "unknown",
            )
        try:
            self.bus.change_provider_config_job_board.submit_result(
                key=job.job_id, result=result,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "providers worker: failed to submit changeProviderConfig result for %s",
                job.job_id,
            )

    def _rebuild_provider(self) -> None:
        """(Re)build the cached :class:`LLMProvider` from current config.

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
            logger.exception(
                "providers worker: failed to publish known providers"
            )

    # ----- LLM invocation -----------------------------------------------

    async def _invoke_safe(self, job: CallLLMJob) -> None:
        try:
            await self._invoke_provider(job)
        except asyncio.CancelledError:
            self._safe_submit_failure(
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
            self._safe_submit_failure(
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
            self._safe_submit_failure(
                job, error_code=error_code, error_detail=str(exc),
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
            self._safe_submit_failure(
                job,
                error_code=_NOT_CONFIGURED_CODE,
                error_detail=str(exc),
            )
            return
        except LLMError as exc:
            self._safe_submit_failure(
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
            self.bus.llm_job_board.submit_result(key=job.job_id, result=result)
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

    def _safe_submit_failure(
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
            self.bus.llm_job_board.submit_result(key=job.job_id, result=result)
        except Exception:  # noqa: BLE001
            logger.exception(
                "providers worker: failed to submit failure for %s",
                job.job_id,
            )


# ---------------------------------------------------------------------------
# module-level singletons
# ---------------------------------------------------------------------------


_worker: ProvidersWorker | None = None


async def start_provider_worker(
    bus: "NewBus | None" = None,
) -> ProvidersWorker:
    """Start the process-local provider worker.

    ``bus`` is the wired :class:`NewBus` from the composition root.
    It's optional only for backwards-compat with callers that don't
    pass it (legacy tests); the production runtime always supplies it.
    """
    global _worker
    if _worker is None:
        if bus is None:
            raise RuntimeError(
                "start_provider_worker requires a NewBus"
            )
        _worker = ProvidersWorker(bus)
        await _worker.start()
    return _worker


async def stop_provider_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


__all__ = [
    "ProvidersWorker",
    "start_provider_worker",
    "stop_provider_worker",
]
