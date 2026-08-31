"""Provider worker: rebuild the LiteLLM client, or call the model.

Attach seeds ``providers.options`` and builds the first client from
Settings. The loop then only does two things:

- claim ``ChangeProviderNotify`` — persist already happened at publish;
  rebuild the client unless this is a model-only tweak on a live client
- claim ``CallLLMJob`` — ``client.complete`` and submit the result
"""

from __future__ import annotations

import asyncio
import json

from bus import (
    BaseWorker,
    CallLLMJob,
    CallLLMResult,
    ChangeProviderNotify,
    ChangeProviderNotifyResult,
    JobStatus,
    ListSettingsJob,
    go,
)
from providers.client import Client, connect, options

NAME_KEY = "provider.name"
API_KEY = "provider.api_key"
MODEL_KEY = "provider.model"


class ProvidersWorker(BaseWorker):
    worker_name = "providers"

    def __init__(self, *, poll_seconds: float = 0.25) -> None:
        super().__init__(poll_seconds=poll_seconds)
        self._client: Client | None = None
        self._error: str | None = None

    async def on_attached(self) -> None:
        assert self.bus is not None
        worker_name = self.worker_name
        assert worker_name is not None, "providers worker: worker_name is required"
        self.bus.boost_default_settings(
            worker_name=worker_name,
            settings={"options": json.dumps(options(), ensure_ascii=False)},
        )
        await self._rebuild()

    async def on_detached(self) -> None:
        self._client = None
        self._error = None

    async def _poll(self) -> bool:
        change = await self.claim(ChangeProviderNotify)
        if change is not None:
            await self._on_change(change)
            return True
        job = await self.claim(CallLLMJob)
        if job is not None:
            go(self._on_llm(job))
            return True
        return False

    async def _rebuild(self) -> None:
        try:
            listed = await self.ask(ListSettingsJob())
            self._client = await self.call(
                connect,
                listed.settings.get(NAME_KEY),
                listed.settings.get(API_KEY),
                listed.settings.get(MODEL_KEY),
            )
            self._error = None
        except Exception as exc:  # noqa: BLE001 -- missing extras or config must not kill the loop
            self._client = None
            self._error = str(exc) or type(exc).__name__

    async def _on_change(self, job: ChangeProviderNotify) -> None:
        model_only = bool(job.model) and not job.provider and not job.api_key
        if model_only and self._client is not None:
            self._client.model = job.model
        else:
            await self._rebuild()
        if self._client is None:
            result = ChangeProviderNotifyResult(
                id=job.id,
                status=JobStatus.FAILED,
                error=self._error or "unknown provider configuration error",
            )
        else:
            result = ChangeProviderNotifyResult(id=job.id)
        self.submit(ChangeProviderNotify, result)

    async def _on_llm(self, job: CallLLMJob) -> None:
        try:
            if self._client is None:
                self._fail(job, self._error or "MAGI runtime has no LLM provider configured")
                return
            response = await self._client.complete(
                job.messages,
                max_tokens=int(job.max_tokens or 1024),
                tools=job.tools or None,
            )
            self.submit(
                CallLLMJob,
                CallLLMResult(
                    id=job.id,
                    text=response.get("text") or "(empty reply)",
                    thinking=response.get("thinking"),
                    tool_uses=list(response.get("tool_uses") or []),
                    raw_blocks=list(response.get("raw_blocks") or []),
                    finish_reason=response.get("stop_reason"),
                    model=response.get("model") or self._client.model,
                ),
            )
        except asyncio.CancelledError:
            self._fail(job, "providers worker cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 -- no job can kill the worker
            self._fail(job, str(exc))

    def _fail(self, job: CallLLMJob, error: str) -> None:
        self.submit(CallLLMJob, CallLLMResult(id=job.id, status=JobStatus.FAILED, error=error))
