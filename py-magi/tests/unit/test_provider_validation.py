"""A provider change becomes active only after the selected route responds."""

import asyncio

import pytest

from bus import Bus, ChangeProviderNotify, JobStatus
from bus.firmware.books.settingsBook import Setting, SettingsBook
from providers.worker import ProvidersWorker


async def _claimed(board):
    for _ in range(100):
        job = board.claim()
        if job is not None:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("provider change was not claimable")


@pytest.mark.asyncio
async def test_provider_verification_keeps_old_settings_on_failure(tmp_path, monkeypatch) -> None:
    calls = []

    async def completion(**params):
        calls.append(params)
        if params["api_key"] == "bad-key":
            raise ValueError("invalid API key bad-key")
        return object()

    monkeypatch.setattr("providers.client.litellm.acompletion", completion)
    monkeypatch.setattr("providers.client.litellm.get_model_info", lambda **_: {})

    with Bus("@provider-test.magi", workspace=tmp_path) as bus:
        settings = SettingsBook(bus._memories)
        for key, value in (
            ("provider.name", "openai"),
            ("provider.api_key", "old-key"),
            ("provider.model", "gpt-5.6"),
        ):
            settings.upsert(Setting(key=key, value=value))
        worker = ProvidersWorker(bus)
        worker._client.configure(provider_name="openai", api_key="old-key", model="gpt-5.6")
        board = bus.board(ChangeProviderNotify)
        assert board is not None

        failed_id = board.publish(ChangeProviderNotify(
            publisher="test", provider="claude", api_key="bad-key", model="claude-opus-5",
        ))
        await worker._on_change(await _claimed(board))
        failed = board.get_result(failed_id)
        assert failed is not None and failed.status is JobStatus.FAILED
        assert "bad-key" not in (failed.error or "")
        assert settings.get_by_key("provider.api_key").value == "old-key"
        assert worker._client.host.id == "openai"
        assert worker._client.api_key == "old-key"

        good_id = board.publish(ChangeProviderNotify(
            publisher="test", provider="claude", api_key="good-key", model="claude-opus-5",
        ))
        await worker._on_change(await _claimed(board))
        good = board.get_result(good_id)
        assert good is not None and good.status is JobStatus.COMPLETED
        assert settings.get_by_key("provider.name").value == "claude"
        assert settings.get_by_key("provider.api_key").value == "good-key"
        assert worker._client.host.id == "claude"
        assert calls[0]["model"] == calls[1]["model"] == "anthropic/claude-opus-5"
        assert calls[1]["max_tokens"] == 32
