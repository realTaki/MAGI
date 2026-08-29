"""Channel management API — GET / POST /api/channels.

Channel enable/disable state lives in the ``settings`` table
under key ``channels.enabled``.  The selectable channel names themselves
live in ``settings.channels.available`` and are registered by BUS/Workers.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from channels.api.auth_gates import AdminGate
from channels.api.dependencies import BusDep
from channels.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.channels")

router = APIRouter(tags=["channels"])

_SETTINGS_KEY = "channels.enabled"

#: Channels the operator-facing toggle API refuses to disable.
#: Mirrors :data:`startup.workers._REQUIRED_CHANNELS` — both
#: The WebUI dashboard is the one mandatory human-facing channel. A2A is a
#: MAGIS-shared durable board, so it is deliberately absent from this API.
_REQUIRED_CHANNELS: frozenset[str] = frozenset({"webui"})


def _read_enabled(bus) -> list[str]:
    raw = bus.settings_book.get_value(key=_SETTINGS_KEY)
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list) and all(isinstance(c, str) for c in parsed):
                result = list(dict.fromkeys(c for c in parsed if c))
                for req in _REQUIRED_CHANNELS:
                    if req not in result:
                        result.append(req)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
    return ["webui"]


def _write_enabled(bus, channels: list[str]) -> None:
    bus.settings_book.set(key=_SETTINGS_KEY, value=json.dumps(channels))


def _has_credentials(bus, channel: str) -> bool:
    if channel == "tg":
        return bool(bus.settings_book.get_value(key="telegram.bot_token"))
    return True


def _label(name: str) -> str:
    return {"a2a": "A2A", "task": "Task", "tg": "Telegram", "webui": "WebUI"}.get(
        name, name
    )


def _is_implemented(registry, name: str) -> bool:
    """A2A is BUS-owned; all other implementations have a runtime worker."""
    return name == "a2a" or bool(registry and registry.get_worker(name))


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
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ChannelsResponse:
    enabled = _read_enabled(bus)
    registry = getattr(request.app.state, "workers", None)
    available: list[ChannelInfo] = []
    for name in bus.settings_book.channel_options():
        implemented = _is_implemented(registry, name)
        available.append(
            ChannelInfo(
                name=name,
                label=_label(name),
                implemented=implemented,
                has_credentials=_has_credentials(bus, name),
                enabled=name in enabled,
                running=bool(name != "a2a" and implemented and registry and registry.is_running(name)),
            )
        )
    return ChannelsResponse(enabled=enabled, available=available)


@router.post("/channels", response_model=ChannelsResponse)
async def update_channels(
    payload: ChannelsUpdateRequest,
    request: Request,
    _admin: AdminGate,
    bus: BusDep,
) -> ChannelsResponse:
    options = bus.settings_book.channel_options()
    unknown = [c for c in payload.enabled if c not in options]
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
    for name in options:
        implemented = _is_implemented(registry, name)
        should_run = name in enabled_list and name != "a2a" and implemented
        currently_running = bool(name != "a2a" and implemented and registry and registry.is_running(name))

        if registry is not None and should_run and not currently_running:
            logger.info("channels: starting %r (toggled on)", name)
            await registry.start_worker(name)
        elif registry is not None and not should_run and currently_running:
            logger.info("channels: stopping %r (toggled off)", name)
            await registry.stop_worker(name)

        available.append(
            ChannelInfo(
                name=name,
                label=_label(name),
                implemented=implemented,
                has_credentials=_has_credentials(bus, name),
                enabled=name in enabled_list,
                running=bool(name != "a2a" and implemented and registry and registry.is_running(name)),
            )
        )

    return ChannelsResponse(enabled=enabled_list, available=available)
