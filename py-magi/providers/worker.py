"""Provider worker: deliver every provider outcome through ``CallLLMJob``.

The client is constructed empty and updated from Settings on attach, then
from :class:`ChangeProviderNotify` in place. Neither path validates a
provider, API key, or model.

Every failure on a claimed :class:`CallLLMJob` -- malformed configuration,
missing optional dependency, cancellation, or provider error -- becomes
that Job's terminal ``CallLLMResult(error=...)``.
"""

from __future__ import annotations

import json

from bus import (
    BaseWorker,
    Bus,
    CallLLMJob,
    CallLLMResult,
    ChangeProviderNotify,
    ChangeProviderNotifyResult,
    DeleteSettingJob,
    JobStatus,
    ListSettingsJob,
    SetSettingJob,
    go,
)
from providers.client import LiteLLMClient, options

NAME_KEY = "provider.name"
API_KEY = "provider.api_key"
MODEL_KEY = "provider.model"
CONTEXT_WINDOW_KEY = "provider.context_window"


class ProvidersWorker(BaseWorker):
    worker_name = "providers"
    default_settings = {"options": json.dumps(options(), ensure_ascii=False)}

    def __init__(self, bus: Bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._client = LiteLLMClient()

    async def on_attached(self) -> None:
        listed = await self.ask(ListSettingsJob(publisher=self.worker_name))
        settings = listed.settings or {} if listed is not None else {}
        await self._configure(
            provider_name=settings.get(NAME_KEY),
            api_key=settings.get(API_KEY),
            model=settings.get(MODEL_KEY),
        )

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

    async def _on_change(self, job: ChangeProviderNotify) -> None:
        await self._configure(
            provider_name=job.provider,
            api_key=job.api_key,
            model=job.model,
        )
        self.submit(ChangeProviderNotify, ChangeProviderNotifyResult(id=job.id))

    async def _configure(
        self,
        *,
        provider_name: str | None,
        api_key: str | None,
        model: str | None,
    ) -> None:
        self._client.configure(provider_name=provider_name, api_key=api_key, model=model)
        window = self._client.context_window()
        if window is None:
            await self.ask(DeleteSettingJob(publisher=self.worker_name, key=CONTEXT_WINDOW_KEY))
            return
        await self.ask(
            SetSettingJob(
                publisher=self.worker_name,
                key=CONTEXT_WINDOW_KEY,
                value=str(window),
            )
        )

    async def _on_llm(self, job: CallLLMJob) -> None:
        try:
            self.submit(CallLLMJob, await self._client.complete(job))
        except Exception as exc:  # noqa: BLE001 -- LLM failure belongs on CallLLMResult
            self.submit(CallLLMJob, CallLLMResult(id=job.id, status=JobStatus.FAILED, error=str(exc)))
