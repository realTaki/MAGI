"""vNext provider worker — the sole owner of vendor SDK calls.

The launcher attaches the worker to a ``BusForWorker`` slice. It never sees a
Book, ORM session, engine, or the old Runtime BUS. Configuration and accounting
cross the boundary through vNext Firmware Jobs; its static capability defaults
use the dedicated ``BusForWorker.boost_default_settings`` API.

Provider SDKs are async, so vNext ``BaseWorker`` supplies its event loop,
task lifetime, and bounded in-process concurrency are shared worker mechanics,
not provider-specific lifecycle code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from magi.bus import (
    BaseWorker,
    CallLLMJob,
    CallLLMResult,
    ChangeProviderJob,
    ChangeProviderResult,
    GetSettingJob,
    JobStatus,
    LLMErrorCode,
    RecordTokenUsageJob,
)
from magi.providers.base import LLMProvider
from magi.providers.errors import (
    LLMAuthError,
    LLMContextLengthError,
    LLMError,
    LLMNetworkError,
    LLMNotConfiguredError,
    LLMRateLimitError,
)
from magi.providers.requiredSlots import REQUIRED_SLOTS

logger = logging.getLogger("magi.providers.worker")

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"

_PROVIDER_OPTIONS: list[dict[str, str]] = [
    {"provider": "claude", "model": "claude-fable-5"},
    {"provider": "claude", "model": "claude-opus-5"},
    {"provider": "minimax-global", "model": "MiniMax-M3"},
    {"provider": "minimax-cn", "model": "MiniMax-M3"},
    {"provider": "openai", "model": "gpt-5.6-sol"},
    {"provider": "openai", "model": "gpt-5.6-terra"},
    {"provider": "openai", "model": "gpt-5.6-luna"},
]


def _map_exception_to_code(exc: BaseException) -> LLMErrorCode:
    """Translate provider-private errors to the public Firmware enum."""
    if isinstance(exc, LLMNotConfiguredError):
        return LLMErrorCode.CREDENTIALS_REQUIRED
    if isinstance(exc, LLMAuthError):
        return LLMErrorCode.AUTH_FAILED
    if isinstance(exc, LLMRateLimitError):
        return LLMErrorCode.RATE_LIMITED
    if isinstance(exc, LLMNetworkError):
        return LLMErrorCode.NETWORK_ERROR
    if isinstance(exc, LLMContextLengthError):
        return LLMErrorCode.CONTEXT_TOO_LONG
    return LLMErrorCode.UNKNOWN


class ProvidersWorker(BaseWorker):
    """Claim vNext provider jobs and invoke the configured SDK client.

    A Runtime initially has exactly one provider worker. ``concurrency``
    bounds simultaneously-running LLM calls inside that one worker.
    """

    worker_name = "providers"
    required_slots = REQUIRED_SLOTS

    def __init__(
        self,
        *,
        poll_seconds: float = 0.25,
        concurrency: int | None = None,
    ) -> None:
        super().__init__(poll_seconds=poll_seconds, concurrency=concurrency)
        self._provider: LLMProvider | None = None
        self._provider_error: LLMError | None = None

    async def on_attached(self) -> None:
        self._boost_default_settings()
        self._rebuild_provider()

    async def on_detached(self) -> None:
        self._provider = None
        self._provider_error = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            reserved = False
            try:
                config_job = await self.call(self._change_provider_board().claim)
                if config_job is not None:
                    await self._handle_config_job(config_job)
                    continue
                await self.reserve_capacity()
                reserved = True
                llm_job = await self.call(self._llm_board().claim)
                if llm_job is not None:
                    self.spawn_reserved(
                        self._invoke_safe(llm_job),
                        name=f"provider-job-{llm_job.id}",
                    )
                    reserved = False
                    continue
                self.release_capacity()
                reserved = False
            except Exception:  # noqa: BLE001 -- retry after a BUS failure
                if reserved:
                    self.release_capacity()
                logger.exception("providers worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    # -- BUS helpers -----------------------------------------------------

    def _llm_board(self):
        assert self.bus is not None
        return self.bus.board(CallLLMJob)

    def _change_provider_board(self):
        assert self.bus is not None
        return self.bus.board(ChangeProviderJob)

    def _setting_value(self, key: str) -> str | None:
        """Read one setting only through the Firmware query Job."""
        assert self.bus is not None
        board = self.bus.board(GetSettingJob)
        job_id = board.publish(GetSettingJob(key=key))
        result = board.get_result(job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            return None
        return result.value

    # -- configuration ---------------------------------------------------

    def _boost_default_settings(self) -> None:
        assert self.bus is not None
        self.bus.boost_default_settings(
            worker_name=self.worker_name,
            settings={
                "options": json.dumps(_PROVIDER_OPTIONS, ensure_ascii=False),
            },
        )

    def _rebuild_provider(self) -> None:
        """Build the cached SDK client from vNext Settings Firmware."""
        try:
            from magi.providers.factory import get_provider

            provider = get_provider(
                provider_name=self._setting_value(PROVIDER_NAME_KEY),
                api_key=self._setting_value(PROVIDER_API_KEY_KEY),
                model=self._setting_value(PROVIDER_MODEL_KEY),
            )
        except LLMNotConfiguredError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning("providers worker: no LLM configured (%s)", exc)
        except LLMError as exc:
            self._provider = None
            self._provider_error = exc
            logger.warning("providers worker: cannot build LLM (%s)", exc)
        except Exception as exc:  # noqa: BLE001 -- missing extras must not kill the loop
            self._provider = None
            self._provider_error = LLMError(str(exc) or type(exc).__name__)
            logger.warning("providers worker: cannot build LLM (%s)", exc)
        else:
            self._provider = provider
            self._provider_error = None
            logger.info("providers worker: cached LLM client (%s)", type(provider).__name__)

    async def _handle_config_job(self, job: ChangeProviderJob) -> None:
        if job.provider or job.api_key:
            self._rebuild_provider()
        elif job.model and self._provider is not None:
            self._update_model(job.model)
        else:
            self._rebuild_provider()
        if self._provider is None:
            result = ChangeProviderResult(
                id=job.id,
                status=JobStatus.FAILED,
                error=str(self._provider_error or "unknown provider configuration error"),
            )
        else:
            result = ChangeProviderResult(id=job.id)
        if not await self.call(self._change_provider_board().submit_result, result):
            logger.warning("providers worker: failed to submit config result for %s", job.id)

    def _update_model(self, model: str) -> None:
        """Apply a model-only configuration change without rebuilding the SDK."""
        assert self._provider is not None
        self._provider.model = model
        logger.info("providers worker: updated cached model to %r", model)

    # -- inference -------------------------------------------------------

    async def _invoke_safe(self, job: CallLLMJob) -> None:
        try:
            await self._invoke_provider(job)
        except asyncio.CancelledError:
            await self._submit_llm_failure(
                job,
                error_code=LLMErrorCode.RUN_CANCELLED,
                error_detail="providers worker cancelled",
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- no job can kill the worker
            logger.exception("providers worker: unhandled exception on job %s", job.id)
            await self._submit_llm_failure(
                job,
                error_code=LLMErrorCode.PROVIDER_CRASHED,
                error_detail=str(exc),
            )

    async def _invoke_provider(self, job: CallLLMJob) -> None:
        provider = self._provider
        if provider is None:
            missing = self._provider_error or LLMNotConfiguredError(
                "MAGI runtime has no LLM provider configured"
            )
            await self._submit_llm_failure(
                job,
                error_code=_map_exception_to_code(missing),
                error_detail=str(missing),
            )
            return

        system, messages = self._split_system_message(job.messages)
        try:
            response = await provider.chat(
                system=system,
                messages=messages,
                max_tokens=int(job.max_tokens or 1024),
                tools=job.tools or None,
            )
        except LLMError as exc:
            await self._submit_llm_failure(
                job,
                error_code=_map_exception_to_code(exc),
                error_detail=str(exc),
            )
            return

        result = CallLLMResult(
            id=job.id,
            text=response.get("text") or "(empty reply)",
            thinking=response.get("thinking"),
            tool_uses=list(response.get("tool_uses") or []),
            raw_blocks=list(response.get("raw_blocks") or []),
            finish_reason=response.get("stop_reason"),
            model=response.get("model") or provider.model,
        )
        if not await self.call(self._llm_board().submit_result, result):
            logger.warning("providers worker: failed to submit LLM result for %s", job.id)
            return
        await self._record_token_usage(
            job,
            provider=provider,
            model=result.model,
            usage=response.get("usage"),
        )

    @staticmethod
    def _split_system_message(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system: str | None = None
        non_system: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            if system is None and message.get("role") == "system":
                system = str(message.get("content") or "")
            else:
                non_system.append(message)
        return system, non_system

    async def _submit_llm_failure(
        self,
        job: CallLLMJob,
        *,
        error_code: LLMErrorCode,
        error_detail: str,
    ) -> None:
        result = CallLLMResult(
            id=job.id,
            status=JobStatus.FAILED,
            error=error_detail,
            error_code=error_code,
        )
        if not await self.call(self._llm_board().submit_result, result):
            logger.warning("providers worker: failed to submit failure for %s", job.id)

    async def _record_token_usage(
        self,
        job: CallLLMJob,
        *,
        provider: LLMProvider,
        model: str,
        usage: dict[str, Any] | None,
    ) -> None:
        if not usage:
            return
        assert self.bus is not None
        board = self.bus.board(RecordTokenUsageJob)
        job_id = await self.call(
            board.publish,
            RecordTokenUsageJob(
                llm_job_id=job.id,
                contact_id=job.contact_id,
                provider=provider.name or "unknown",
                model=model,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                cache_hit_tokens=int(usage.get("cache_hit_tokens", 0) or 0),
                cache_miss_tokens=int(usage.get("cache_miss_tokens", 0) or 0),
                cache_write_tokens=int(usage.get("cache_write_tokens", 0) or 0),
                thinking_tokens=int(usage.get("thinking_tokens", 0) or 0),
                response_tokens=int(usage.get("response_tokens", 0) or 0),
            ),
        )
        result = await self.call(board.get_result, job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            logger.warning("providers worker: failed to record token usage for %s", job.id)


__all__ = ["ProvidersWorker"]
