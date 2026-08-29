"""Runtime-registry behaviour of the channel management API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from channels.api.channels import list_channels


@pytest.mark.asyncio
async def test_channel_list_keeps_unimplemented_channels_stopped() -> None:
    bus = MagicMock()
    bus.settings_book.get_value.return_value = '["webui"]'
    bus.settings_book.channel_options.return_value = ["a2a", "task", "tg", "webui"]
    registry = MagicMock()
    registry.is_running.return_value = False
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workers=registry)))

    response = await list_channels(request, None, bus)

    by_name = {item.name: item for item in response.available}
    assert by_name["a2a"].running is False
    assert by_name["task"].running is False
    assert {call.args[0] for call in registry.is_running.call_args_list} == {
        "task",
        "tg",
        "webui",
    }
