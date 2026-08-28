"""vNext provider worker — the sole owner of vendor SDK calls.

The launcher attaches the worker to a ``BusForWorker`` slice. It never sees a
Book, ORM session, engine, or the old Runtime BUS. Configuration and accounting
cross the boundary through vNext Firmware Jobs; its static capability defaults
use the dedicated ``BusForWorker.boost_default_settings`` API.

Provider SDKs are async while vNext BaseWorker is synchronous, so this
worker owns one event-loop thread for its claim loop. That thread starts
on attach and stops on detach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from magi.new_bus import (
    BaseWorker,
    BusForWorker,
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

logger = logging.getLogger("magi.providers.worker")

PROVIDER_NAME_KEY = "provider.name"
PROVIDER_API_KEY_KEY = "provider.api_key"
PROVIDER_MODEL_KEY = "provider.model"

_PROVIDER_OPTIONS: list[dict[str, str]] = [
    {"value": "claude", "label": "Anthropic (Claude)"},
    {"value": "minimax-global", "label": "Minimax (Global)"},
    {"value": "minimax-cn", "label": "Minimax (China)"},
    {"value": "openai", "label": "OpenAI"},
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

    A Runtime initially has exactly one provider worker. Multiple workers
    require a durable configuration revision so every cached client sees a
    ``ChangeProviderJob``; Launcher must not attach more than one until then.
    """

    worker_name = "providers"

    def __init__(self, *, poll_seconds: float = 0.25) -> None:
        super().__init__()
        self._poll_seconds = poll_seconds
        self._provider: LLMProvider | None = None
        self._provider_error: LLMError | None = None
        self._loop_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._ready = threading.Event()

    def attach(self, bus_for_worker: BusForWorker) -> bool:
        if not super().attach(bus_for_worker):
            return False
        self._boost_default_settings()
        self._stop_requested.clear()
        self._ready.clear()
        self._loop_thread = threading.Thread(
            target=self._thread_main,
            name=f"magi-{self.worker_id}-providers",
            daemon=True,
        )
        self._loop_thread.start()
        if not self._ready.wait(timeout=5.0) or not self._loop_thread.is_alive():
            self.detach()
            return False
        return True

    def detach(self) -> None:
        self._stop_requested.set()
        thread = self._loop_thread
        self._loop_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._poll_seconds + 1.0)
        self._provider = None
        self._provider_error = None
        super().detach()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:  # noqa: BLE001 -- a worker must not kill Launcher
            logger.exception("providers worker stopped unexpectedly")
        finally:
            self._ready.set()

    async def _run(self) -> None:
        self._rebuild_provider()
        self._ready.set()
        while not self._stop_requested.is_set():
            try:
                config_job = self._change_provider_board().claim()
                if config_job is not None:
                    self._handle_config_job(config_job)
                    continue
                llm_job = self._llm_board().claim()
                if llm_job is not None:
                    await self._invoke_safe(llm_job)
                    continue
            except Exception:  # noqa: BLE001 -- retry after a BUS failure
                logger.exception("providers worker: BUS operation failed")
            await asyncio.sleep(self._poll_seconds)

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

    def _handle_config_job(self, job: ChangeProviderJob) -> None:
        self._rebuild_provider()
        if self._provider is None:
            result = ChangeProviderResult(
                id=job.id,
                status=JobStatus.FAILED,
                error=str(self._provider_error or "unknown provider configuration error"),
            )
        else:
            result = ChangeProviderResult(id=job.id)
        if not self._change_provider_board().submit_result(result):
            logger.warning("providers worker: failed to submit config result for %s", job.id)

    # -- inference -------------------------------------------------------

    async def _invoke_safe(self, job: CallLLMJob) -> None:
        try:
            await self._invoke_provider(job)
        except asyncio.CancelledError:
            self._submit_llm_failure(
                job,
                error_code=LLMErrorCode.RUN_CANCELLED,
                error_detail="providers worker cancelled",
            )
            raise
        except Exception as exc:  # noqa: BLE001 -- no job can kill the worker
            logger.exception("providers worker: unhandled exception on job %s", job.id)
            self._submit_llm_failure(
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
            self._submit_llm_failure(
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
            self._submit_llm_failure(
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
        if not self._llm_board().submit_result(result):
            logger.warning("providers worker: failed to submit LLM result for %s", job.id)
            return
        self._record_token_usage(job, provider=provider, model=result.model, usage=response.get("usage"))

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

    def _submit_llm_failure(
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
        if not self._llm_board().submit_result(result):
            logger.warning("providers worker: failed to submit failure for %s", job.id)

    def _record_token_usage(
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
        job_id = board.publish(
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
            )
        )
        result = board.get_result(job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            logger.warning("providers worker: failed to record token usage for %s", job.id)


__all__ = ["ProvidersWorker"]
