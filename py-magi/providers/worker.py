"""Provider worker: deliver every provider outcome through ``CallLLMJob``.

Delivery is the sole error boundary for this module. Attaching the worker and
consuming :class:`ChangeProviderNotify` never validate a provider, API key, or
model, and never report them as configuration failures. A notify has already
persisted its values; the worker applies them to its existing client in place.

Only a claimed :class:`CallLLMJob` creates a client when needed and calls the
SDK. Every resulting failure -- malformed
configuration, missing optional dependency, cancellation, or provider error
-- becomes that Job's terminal ``CallLLMResult(error=...)``. It must not
escape the worker or become a separate control-plane error, so Agent can
deliver the result to the conversation.
"""

from __future__ import annotations

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
from providers.client import Client

NAME_KEY = "provider.name"
API_KEY = "provider.api_key"
MODEL_KEY = "provider.model"


class ProvidersWorker(BaseWorker):
    worker_name = "providers"

    def __init__(self, bus, *, poll_seconds: float = 0.25) -> None:
        super().__init__(bus, poll_seconds=poll_seconds)
        self._client: Client | None = None

    async def on_detached(self) -> None:
        self._client = None

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
        """Apply a persisted change in place, without preflight validation."""
        if self._client is not None:
            self._client.configure(
                provider_name=job.provider,
                api_key=job.api_key,
                model=job.model,
            )
        self.submit(ChangeProviderNotify, ChangeProviderNotifyResult(id=job.id))

    async def _on_llm(self, job: CallLLMJob) -> None:
        self.submit(CallLLMJob, await self._call_result(job))

    async def _call_result(self, job: CallLLMJob) -> CallLLMResult:
        """Return one terminal result; no provider exception leaves this boundary."""
        try:
            client = await self._client_for_call()
            response = await client.complete(
                job.messages,
                max_tokens=int(job.max_tokens or 1024),
                tools=job.tools or None,
            )
            return CallLLMResult(
                id=job.id,
                text=response.get("text") or "(empty reply)",
                thinking=response.get("thinking"),
                tool_uses=list(response.get("tool_uses") or []),
                raw_blocks=list(response.get("raw_blocks") or []),
                finish_reason=response.get("stop_reason"),
                model=response.get("model") or client.model,
            )
        except BaseException as exc:  # noqa: BLE001 -- the Job result is the error boundary
            return CallLLMResult(id=job.id, status=JobStatus.FAILED, error=str(exc))

    async def _client_for_call(self) -> Client:
        """Create a client from persisted settings only for a ``CallLLMJob``."""
        if self._client is not None:
            return self._client
        listed = await self.ask(ListSettingsJob())
        self._client = Client(
            provider_name=listed.settings.get(NAME_KEY),
            api_key=listed.settings.get(API_KEY),
            model=listed.settings.get(MODEL_KEY),
        )
        return self._client
