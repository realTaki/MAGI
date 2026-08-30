"""Provider worker: rebuild the LiteLLM client, or call the model.

Attach seeds ``providers.options`` and builds the first client from
Settings. The loop then only does two things:

- claim ``ChangeProviderJob`` — persist already happened at publish;
  rebuild the client unless this is a model-only tweak on a live client
- claim ``CallLLMJob`` — ``client.complete`` and submit the result
"""

from __future__ import annotations

import asyncio
import json
import logging

from bus import (
    BaseWorker,
    CallLLMJob,
    CallLLMResult,
    ChangeProviderJob,
    ChangeProviderResult,
    JobStatus,
    ListSettingsJob,
)
from providers.client import Client, connect, options

logger = logging.getLogger("providers.worker")

NAME_KEY = "provider.name"
API_KEY = "provider.api_key"
MODEL_KEY = "provider.model"


class ProvidersWorker(BaseWorker):
    worker_name = "providers"

    def __init__(self, *, poll_seconds: float = 0.25, concurrency: int | None = None) -> None:
        super().__init__(poll_seconds=poll_seconds, concurrency=concurrency)
        self._client: Client | None = None
        self._error: str | None = None

    async def on_attached(self) -> None:
        assert self.bus is not None
        self.bus.boost_default_settings(
            worker_name=self.worker_name,
            settings={"options": json.dumps(options(), ensure_ascii=False)},
        )
        self._rebuild()

    async def on_detached(self) -> None:
        self._client = None
        self._error = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            reserved = False
            try:
                change = await self.call(self._board(ChangeProviderJob).claim)
                if change is not None:
                    await self._on_change(change)
                    continue
                await self.reserve_capacity()
                reserved = True
                job = await self.call(self._board(CallLLMJob).claim)
                if job is not None:
                    self.spawn_reserved(self._on_llm(job), name=f"provider-job-{job.id}")
                    reserved = False
                    continue
                self.release_capacity()
                reserved = False
            except Exception:  # noqa: BLE001 -- a BUS blip must not kill the loop
                if reserved:
                    self.release_capacity()
                logger.exception("providers worker: BUS operation failed")
            await asyncio.sleep(self.poll_seconds)

    def _board(self, job_type):
        assert self.bus is not None
        return self.bus.board(job_type)

    def _rebuild(self) -> None:
        try:
            settings = self._settings()
            self._client = connect(settings.get(NAME_KEY), settings.get(API_KEY), settings.get(MODEL_KEY))
            self._error = None
            logger.info("providers worker: client ready (%s / %s)", self._client.name, self._client.model)
        except Exception as exc:  # noqa: BLE001 -- missing extras or config must not kill the loop
            self._client = None
            self._error = str(exc) or type(exc).__name__
            logger.warning("providers worker: cannot build client (%s)", exc)

    def _settings(self) -> dict[str, str]:
        board = self._board(ListSettingsJob)
        job_id = board.publish(ListSettingsJob())
        result = board.get_result(job_id)
        if result is None or result.status is not JobStatus.COMPLETED:
            return {}
        return result.settings or {}

    async def _on_change(self, job: ChangeProviderJob) -> None:
        model_only = bool(job.model) and not job.provider and not job.api_key
        if model_only and self._client is not None:
            self._client.model = job.model
            logger.info("providers worker: model -> %r", job.model)
        else:
            self._rebuild()
        if self._client is None:
            result = ChangeProviderResult(
                id=job.id,
                status=JobStatus.FAILED,
                error=self._error or "unknown provider configuration error",
            )
        else:
            result = ChangeProviderResult(id=job.id)
        if not await self.call(self._board(ChangeProviderJob).submit_result, result):
            logger.warning("providers worker: failed to submit config result for %s", job.id)

    async def _on_llm(self, job: CallLLMJob) -> None:
        try:
            if self._client is None:
                await self._fail(job, self._error or "MAGI runtime has no LLM provider configured")
                return
            response = await self._client.complete(
                job.messages,
                max_tokens=int(job.max_tokens or 1024),
                tools=job.tools or None,
            )
            result = CallLLMResult(
                id=job.id,
                text=response.get("text") or "(empty reply)",
                thinking=response.get("thinking"),
                tool_uses=list(response.get("tool_uses") or []),
                raw_blocks=list(response.get("raw_blocks") or []),
                finish_reason=response.get("stop_reason"),
                model=response.get("model") or self._client.model,
            )
            if not await self.call(self._board(CallLLMJob).submit_result, result):
                logger.warning("providers worker: failed to submit LLM result for %s", job.id)
                return
        except asyncio.CancelledError:
            await self._fail(job, "providers worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- no job can kill the worker
            logger.exception("providers worker: unhandled exception on job %s", job.id)
            await self._fail(job, str(exc))

    async def _fail(self, job: CallLLMJob, error: str) -> None:
        result = CallLLMResult(id=job.id, status=JobStatus.FAILED, error=error)
        if not await self.call(self._board(CallLLMJob).submit_result, result):
            logger.warning("providers worker: failed to submit failure for %s", job.id)
