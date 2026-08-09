"""Channel management API — GET / POST /api/channels.

Channel enable/disable state lives in the ``settings`` table
under key ``channels.enabled`` (a JSON array of
:class:`Channel` values). Settings read/write now prefers
bus via :mod:`magi.channels.api._bus`.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels import Channel
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.dependencies import BusDep
from magi.channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.channels")

router = APIRouter(tags=["channels"])

_SETTINGS_KEY = "channels.enabled"

# Channel metadata
_CHANNEL_META: list[dict] = [
    {"name": Channel.WEBUI, "label": "WebUI", "implemented": True},
    {"name": Channel.TG, "label": "Telegram", "implemented": True},
    {"name": "wechat", "label": "WeChat", "implemented": False},
    {"name": "lark", "label": "Lark", "implemented": False},
    {"name": "teams", "label": "Teams", "implemented": False},
]

_REQUIRED_CHANNELS: frozenset[str] = frozenset({Channel.WEBUI})


def _read_enabled(bus) -> list[str]:
    raw = bus.settings_book.get(key=_SETTINGS_KEY)
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list) and all(isinstance(c, str) for c in parsed):
                result = [c for c in parsed if c in Channel]
                for req in _REQUIRED_CHANNELS:
                    if req not in result:
                        result.append(req)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
    return [Channel.WEBUI]


def _write_enabled(bus, channels: list[str]) -> None:
    bus.settings_book.set(key=_SETTINGS_KEY, value=json.dumps(channels))


def _has_credentials(bus, channel: str) -> bool:
    if channel == Channel.TG:
        return bool(bus.settings_book.get(key="telegram.bot_token"))
    return True


# -- response / request shapes --------------------------------------------

class ChannelInfo(BaseModel):
    name: str
    label: str
    implemented: bool
    has_credentials: bool
    enabled: bool
    running: bool


class ChannelsResponse(BaseModel):
    enabled: list[str]
    available: list[ChannelInfo]


class ChannelsUpdateRequest(BaseModel):
    enabled: list[str] = Field(min_length=0)


# -- endpoints ------------------------------------------------------------

@router.get("/channels", response_model=ChannelsResponse)
async def list_channels(
    request: Request, _admin: AdminGate, bus: BusDep,
) -> ChannelsResponse:
    enabled = _read_enabled(bus)
    registry = getattr(request.app.state, "workers", None)
    available: list[ChannelInfo] = []
    for meta in _CHANNEL_META:
        name = meta["name"]
        available.append(ChannelInfo(
            name=name,
            label=meta["label"],
            implemented=meta["implemented"],
            has_credentials=_has_credentials(bus, name),
            enabled=name in enabled,
            running=bool(meta["implemented"] and registry and registry.is_running(name)),
        ))
    return ChannelsResponse(enabled=enabled, available=available)


@router.post("/channels", response_model=ChannelsResponse)
async def update_channels(
    payload: ChannelsUpdateRequest,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ChannelsResponse:
    unknown = [c for c in payload.enabled if c not in Channel]
    if unknown:
        raise MagiHTTPException(
            status_code=400,
            code="channels.unknown",
            detail=f"unknown channel(s): {unknown!r}",
        )

    effective_enabled = list(payload.enabled)
    for req in _REQUIRED_CHANNELS:
        if req not in effective_enabled:
            effective_enabled.append(req)

    _write_enabled(bus, effective_enabled)
    enabled_list = _read_enabled(bus)
    registry = getattr(request.app.state, "workers", None)

    available: list[ChannelInfo] = []
    for meta in _CHANNEL_META:
        name = meta["name"]
        should_run = name in enabled_list and meta["implemented"]
        currently_running = bool(
            meta["implemented"] and registry and registry.is_running(name)
        )

        if registry is not None and should_run and not currently_running:
            logger.info("channels: starting %r (toggled on)", name)
            await registry.start_worker(name)
        elif registry is not None and not should_run and currently_running:
            logger.info("channels: stopping %r (toggled off)", name)
            await registry.stop_worker(name)

        available.append(ChannelInfo(
            name=name,
            label=meta["label"],
            implemented=meta["implemented"],
            has_credentials=_has_credentials(bus, name),
            enabled=name in enabled_list,
            running=bool(meta["implemented"] and registry and registry.is_running(name)),
        ))

    return ChannelsResponse(enabled=enabled_list, available=available)
