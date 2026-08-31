from __future__ import annotations

import pytest

from bus import (
    CallLLMJob,
    ChangeProviderNotify,
    JobStatus,
    ListSettingsResult,
)
from providers.client import connect
from providers.worker import ProvidersWorker


def test_client_applies_provider_configuration_in_place() -> None:
    client = connect("openai", "old-key", "gpt-5.6")

    updated = connect("claude", "new-key", "claude-sonnet-5", client=client)

    assert updated is client
    assert client.host.id == "claude"
    assert client.host.prefix == "anthropic"
    assert client.api_key == "new-key"
    assert client.model == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_invalid_provider_change_is_acknowledged_without_validation() -> None:
    worker = ProvidersWorker()
    submitted = []
    worker.submit = lambda job_type, result: submitted.append((job_type, result))  # type: ignore[method-assign]

    await worker._on_change(ChangeProviderNotify(id=1, provider="not-a-provider"))

    job_type, result = submitted.pop()
    assert job_type is ChangeProviderNotify
    assert result.id == 1
    assert result.status is JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_invalid_provider_configuration_fails_the_call_job() -> None:
    worker = ProvidersWorker()

    async def ask(_job):
        return ListSettingsResult(
            settings={
                "provider.name": "not-a-provider",
                "provider.api_key": "key",
                "provider.model": "model",
            }
        )

    async def call(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    worker.ask = ask  # type: ignore[method-assign]
    worker.call = call  # type: ignore[method-assign]
    result = await worker._call_result(CallLLMJob(id=2, messages=[{"role": "user", "content": "hi"}]))

    assert result.id == 2
    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert "Unknown LLM provider" in result.error
