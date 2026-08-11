"""Runtime-registry behaviour of the channel management API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from magi.channels import Channel
from magi.channels.api.channels import list_channels


@pytest.mark.asyncio
async def test_channel_list_keeps_unimplemented_channels_stopped() -> None:
    bus = MagicMock()
    bus.settings_book.get.return_value = '["webui"]'
    registry = MagicMock()
    registry.is_running.return_value = False
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workers=registry)))

    response = await list_channels(request, None, bus)

    by_name = {item.name: item for item in response.available}
    assert by_name["wechat"].running is False
    assert by_name["lark"].running is False
    assert by_name["teams"].running is False
    assert {call.args[0] for call in registry.is_running.call_args_list} == {
        Channel.TG,
        Channel.WEBUI,
    }
