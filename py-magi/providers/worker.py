"""Provider worker: deliver every provider outcome through ``CallLLMJob``.

Delivery is the sole error boundary for this module. Attaching the worker and
consuming :class:`ChangeProviderNotify` never validate a provider, API key, or
model, and never report them as configuration failures. A notify has already
persisted its values; the worker only invalidates its cached route.

Only a claimed :class:`CallLLMJob` reads that configuration, creates or
updates a client, and calls the SDK. Every resulting failure -- malformed
configuration, missing optional dependency, cancellation, or provider error
-- becomes that Job's terminal ``CallLLMResult(error=...)``. It must not
escape the worker or become a separate control-plane error, so Agent can
deliver the result to the conversation.
"""

from __future__ import annotations

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
        self._route: tuple[str | None, str | None, str | None] | None = None

    async def on_attached(self) -> None:
        self.bus.boost_default_settings(
            worker_name="providers",
            settings={"options": json.dumps(options(), ensure_ascii=False)},
        )

    async def on_detached(self) -> None:
        self._client = None
        self._route = None

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
        """Acknowledge persisted configuration without preflight validation."""
        self._client = None
        self._route = None
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
        """Resolve the persisted route only while serving a ``CallLLMJob``."""
        listed = await self.ask(ListSettingsJob())
        route = (
            listed.settings.get(NAME_KEY),
            listed.settings.get(API_KEY),
            listed.settings.get(MODEL_KEY),
        )
        if self._client is None or self._route != route:
            self._client = await self.call(connect, *route, client=self._client)
            self._route = route
        return self._client
