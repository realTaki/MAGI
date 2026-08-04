"""Channel management API — GET / POST /api/channels.

Channel enable/disable state lives in the ``settings`` table
under key ``channels.enabled`` (a JSON array of
:class:`Channel` values). The onboarding flow and the
Settings → Channels card both write here; the node reads it
on boot and on every toggle.

No more ``MAGI_CHANNELS`` env var — the DB is the single
source of truth for which IM channels are active.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from magi.channels import Channel
from magi.channels.api.auth_gates import AdminGate
from magi.channels.api.errors import MagiHTTPException
from magi.bus import get_bus
from magi.launcher import is_channel_running, start_channel, stop_channel

logger = logging.getLogger("magi.api.channels")

router = APIRouter(tags=["channels"])

_SETTINGS_KEY = "channels.enabled"

# Channel metadata — one entry per known channel.  ``implemented``
# controls whether the toggle is enabled in the UI.  ``has_credentials``
# is checked at render time (TG bot token, etc.).
_CHANNEL_META: list[dict] = [
    {"name": Channel.WEBUI, "label": "WebUI", "implemented": True},
    {"name": Channel.TG, "label": "Telegram", "implemented": True},
    {"name": "wechat", "label": "WeChat", "implemented": False},
    {"name": "lark", "label": "Lark", "implemented": False},
    {"name": "teams", "label": "Teams", "implemented": False},
]

# Channels that cannot be disabled — WebUI is the control plane.
_REQUIRED_CHANNELS: frozenset[str] = frozenset({Channel.WEBUI})


def _read_enabled() -> list[str]:
    raw = get_bus().settings.get(_SETTINGS_KEY)
    if not raw:
        return [Channel.WEBUI]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(isinstance(c, str) for c in parsed):
            # Ensure required channels are present
            result = [c for c in parsed if c in Channel]
            for req in _REQUIRED_CHANNELS:
                if req not in result:
                    result.append(req)
            return result
    except (json.JSONDecodeError, TypeError):
        pass
    return [Channel.WEBUI]


def _write_enabled(channels: list[str]) -> None:
    get_bus().settings.set(_SETTINGS_KEY, json.dumps(channels))


def _has_credentials(channel: str) -> bool:
    if channel == Channel.TG:
        return bool(get_bus().settings.get("telegram.bot_token"))
    return True  # webui always has creds; unimplemented channels defer


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
    # ``min_length=0`` so an operator who disables every
    # non-required channel doesn't trip Pydantic V2's
    # built-in body validator with a 422 — the view
    # function re-adds ``WEBUI`` server-side so a 0-length
    # list is functionally equivalent to ``["webui"]``.
    enabled: list[str] = Field(min_length=0)


# -- endpoints ------------------------------------------------------------


@router.get("/channels", response_model=ChannelsResponse)
def list_channels(
    _admin: AdminGate,
    request: Request,
) -> ChannelsResponse:
    enabled = _read_enabled()
    available: list[ChannelInfo] = []
    for meta in _CHANNEL_META:
        name = meta["name"]
        available.append(ChannelInfo(
            name=name,
            label=meta["label"],
            implemented=meta["implemented"],
            has_credentials=_has_credentials(name),
            enabled=name in enabled,
            running=is_channel_running(name),
        ))
    return ChannelsResponse(enabled=enabled, available=available)


@router.post("/channels", response_model=ChannelsResponse)
def update_channels(
    payload: ChannelsUpdateRequest,
    _admin: AdminGate,
    request: Request,
) -> ChannelsResponse:

    # Validate all names are known Channel values
    unknown = [c for c in payload.enabled if c not in Channel]
    if unknown:
        raise MagiHTTPException(
            status_code=400,
            code="channels.unknown",
            detail=f"unknown channel(s): {unknown!r}",
        )

    # Required channels must stay enabled. We
    # auto-insert any missing required channel (rather
    # than 400-ing the operator) so a stale-dashboard
    # toggle that drops ``webui`` from ``enabled`` can't
    # accidentally disable the control plane. The
    # required channels are always added server-side
    # before persistence and are returned in the
    # response so the operator sees the corrected list.
    effective_enabled = list(payload.enabled)
    for req in _REQUIRED_CHANNELS:
        if req not in effective_enabled:
            effective_enabled.append(req)

    # Persist
    _write_enabled(effective_enabled)
    enabled_list = _read_enabled()

    # Start / stop channels to match the new enabled list.
    # Already-running channels are left alone; newly-enabled ones
    # are started; disabled ones are stopped.
    available: list[ChannelInfo] = []
    for meta in _CHANNEL_META:
        name = meta["name"]
        should_run = name in enabled_list and meta["implemented"]
        currently_running = is_channel_running(name)

        if should_run and not currently_running:
            logger.info("channels: starting %r (toggled on)", name)
            start_channel(name, get_bus().settings.require_state_dir())
        elif not should_run and currently_running:
            logger.info("channels: stopping %r (toggled off)", name)
            stop_channel(name)

        available.append(ChannelInfo(
            name=name,
            label=meta["label"],
            implemented=meta["implemented"],
            has_credentials=_has_credentials(name),
            enabled=name in enabled_list,
            running=is_channel_running(name),
        ))

    return ChannelsResponse(enabled=enabled_list, available=available)
