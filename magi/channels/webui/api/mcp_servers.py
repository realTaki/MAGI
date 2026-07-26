"""MCP server CRUD API — the operator-facing surface for
configuring Model-Context-Protocol servers.

The :class:`McpServer` table is the single source of
truth for which MCP servers the agent loop connects to
(replacing the legacy ``mcp.json`` flow). Operators add /
edit / toggle / delete rows from the WebUI Settings → MCP
card; this router backs that card with admin-only HTTP
endpoints.

Lazy-reload semantics
---------------------

Edits made via these endpoints do NOT immediately
reconnect the subprocess. The agent loop calls
:func:`magi.agent.tools.registry.maybe_reload_mcp_tools`
on every chat turn; that compares the table's
``max(updated_at)`` against the in-memory cache stamp
and re-bootstraps on a mismatch. The operator sees the
new tool list on the next user message.

That means a freshly-created / toggled / deleted row is
"pending" until the next chat turn. The endpoint layer
deliberately does not poke the cache — keeping the
endpoints fast (no subprocess I/O on the request path)
and the reload behaviour centralised in one place.

Auth
----

Same ``AdminGate`` every other Adam endpoint uses
(:mod:`magi.channels.webui.api.auth_gates`). MCP server
config is admin-only by design: an operator with
write access to a "fetch" or "github" MCP server can
arbitrarily extend the LLM's tool menu, and that's
not a surface we want to expose to non-admin operators.

Masking
-------

``env`` and ``headers`` carry the operator's API keys
and Authorization tokens. The response shape never
echoes a secret value back — only a per-key ``is_set``
flag (``env_set`` / ``headers_set``). The frontend
renders an unset key as a "not set" hint and a set key
as ``••••••`` (the actual value is never returned to
the WebUI). Edits use the same shape: a blank value
sends ``""`` and the server treats that as "clear".
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from magi.agent.db import McpServer, get_session
from magi.channels.webui.api.auth_gates import AdminGate
from magi.channels.webui.api.errors import MagiHTTPException

logger = logging.getLogger("magi.api.mcp_servers")

router = APIRouter(tags=["mcp-servers"])


# -- response / payload shapes ----------------------------------------------

ConnectionType = Literal["stdio", "sse", "streamable_http"]
_VALID_CONNECTION_TYPES: tuple[str, ...] = ("stdio", "sse", "streamable_http")


def _is_set(value: str | None) -> bool:
    """Mirror of the :func:`_mask_key` policy in
    :mod:`magi.channels.webui.api.magis` — a value is
    "set" when it's a non-empty string. ``None`` and
    ``""`` both count as unset; the loader treats both
    identically."""
    return bool(value) and value != ""


class McpServerOut(BaseModel):
    """One row from the ``mcp_servers`` table.

    ``env`` / ``headers`` carry the secret values the
    operator entered — **never** returned to the WebUI.
    ``env_set`` / ``headers_set`` mirror the keys with
    a boolean the UI uses to render "set" vs "not set"
    hints without ever receiving the secret itself.
    """

    name: str
    connection_type: ConnectionType
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    enabled: bool = True
    connect_timeout: float | None = None
    execute_timeout: float | None = None
    sse_read_timeout: float | None = None
    env: dict[str, str] = {}
    env_set: dict[str, bool] = {}
    headers: dict[str, str] = {}
    headers_set: dict[str, bool] = {}
    created_at: str
    updated_at: str


class McpServerIn(BaseModel):
    """Request body for ``POST /api/mcp-servers`` and
    ``PATCH /api/mcp-servers/{name}``."""

    model_config = ConfigDict(extra="forbid")

    # ``name`` is the operator-facing id and the tool-name
    # prefix the LLM sees (``minimax__foo``). URL-safe,
    # ASCII, max 64 chars. PATCH carries the same field
    # only as a no-op echo (rename is not supported — to
    # rename, delete + create).
    name: str = Field(
        min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    connection_type: ConnectionType
    command: str | None = Field(default=None, max_length=256)
    args: list[str] = []
    url: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    connect_timeout: float | None = Field(default=None, ge=0)
    execute_timeout: float | None = Field(default=None, ge=0)
    sse_read_timeout: float | None = Field(default=None, ge=0)
    env: dict[str, str] = {}
    headers: dict[str, str] = {}

    @field_validator("args")
    @classmethod
    def _strip_args(cls, v: list[str]) -> list[str]:
        return [s for s in (a.strip() for a in v) if s]

    @field_validator("env", "headers")
    @classmethod
    def _no_blank_keys(cls, v: dict[str, str]) -> dict[str, str]:
        # The Pydantic shape already rejects non-string
        # keys; the operator must give a name to every
        # env / header they set. A blank key is a typo,
        # not a legitimate "ignore this" signal.
        for k in v:
            if not k.strip():
                raise ValueError("env / header keys must not be blank")
        return v


def _serialize(row: McpServer) -> McpServerOut:
    """Build the response shape from a row, masking env /
    headers values.

    The response NEVER returns a secret value — not even
    the per-key value the operator just typed. The
    ``env`` / ``headers`` fields carry the key names with
    empty string values so the frontend's
    ``Object.keys(env)`` lookup still works (the form
    needs the keys to render the row). The actual
    set-vs-unset signal lives in ``env_set`` /
    ``headers_set`` — the UI renders ``••••••`` for
    set keys and uses the operator's input only on
    edit, never on read.
    """
    env_pairs = _decode_json_dict(row.env_json)
    headers_pairs = _decode_json_dict(row.headers_json)
    return McpServerOut(
        name=row.name,
        connection_type=row.connection_type,  # type: ignore[arg-type]
        command=row.command,
        args=_decode_json_list(row.args_json),
        url=row.url,
        enabled=row.enabled,
        connect_timeout=row.connect_timeout,
        execute_timeout=row.execute_timeout,
        sse_read_timeout=row.sse_read_timeout,
        # ``env`` carries keys only — values are masked
        # (set keys → empty string; unset keys → empty
        # string for symmetry). The frontend distinguishes
        # via ``env_set``.
        env={k: "" for k in env_pairs},
        env_set={k: _is_set(v) for k, v in env_pairs.items()},
        headers={k: "" for k in headers_pairs},
        headers_set={k: _is_set(v) for k, v in headers_pairs.items()},
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _decode_json_dict(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        # Defensive: a hand-edited / corrupted row should
        # not crash the list endpoint. Log and treat the
        # column as empty; the loader is also defensive.
        logger.warning("mcp_servers row has malformed JSON dict column")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _decode_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("mcp_servers row has malformed JSON list column")
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _validate_payload_for_connection_type(payload: McpServerIn) -> None:
    """Cross-field check that the right endpoint fields
    are populated for the chosen transport. STDIO needs
    ``command``; URL-based needs ``url``. The Pydantic
    model accepts both because some fields are nullable
    by Pydantic's own constraints, but a row that
    satisfies neither invariant is a 400."""
    if payload.connection_type == "stdio":
        if not (payload.command and payload.command.strip()):
            raise MagiHTTPException(
                status_code=400,
                code="validation.mcp_stdio_requires_command",
                detail=(
                    "connection_type='stdio' requires a non-empty 'command'"
                ),
            )
    else:  # sse / streamable_http
        if not (payload.url and payload.url.strip()):
            raise MagiHTTPException(
                status_code=400,
                code="validation.mcp_http_requires_url",
                detail=(
                    f"connection_type={payload.connection_type!r} "
                    "requires a non-empty 'url'"
                ),
            )


# -- endpoints --------------------------------------------------------------


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> list[McpServerOut]:
    """Return every row in the ``mcp_servers`` table.

    Sorted by ``name`` so the operator sees a stable list
    view. The response never echoes a secret value back;
    only the ``env_set`` / ``headers_set`` per-key booleans
    are surfaced to the WebUI.
    """
    rows = session.query(McpServer).order_by(McpServer.name.asc()).all()
    return [_serialize(r) for r in rows]


@router.get("/mcp-servers/{name}", response_model=McpServerOut)
def get_mcp_server(
    name: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> McpServerOut:
    row = session.get(McpServer, name)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.mcp_server",
            detail=f"mcp server {name!r} not found",
        )
    return _serialize(row)


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
def create_mcp_server(
    payload: McpServerIn,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> McpServerOut:
    """Create a new row.

    ``name`` is the primary key — POSTing a duplicate
    name returns 409 so the operator doesn't silently
    overwrite an existing server.
    """
    _validate_payload_for_connection_type(payload)

    if session.get(McpServer, payload.name) is not None:
        raise MagiHTTPException(
            status_code=409,
            code="conflict.mcp_server_name",
            detail=(
                f"mcp server {payload.name!r} already exists; "
                "use PATCH to update it, or delete + create to rename"
            ),
        )

    row = McpServer(
        name=payload.name,
        connection_type=payload.connection_type,
        command=payload.command,
        args_json=json.dumps(payload.args),
        env_json=json.dumps(payload.env),
        url=payload.url,
        headers_json=json.dumps(payload.headers),
        enabled=payload.enabled,
        connect_timeout=payload.connect_timeout,
        execute_timeout=payload.execute_timeout,
        sse_read_timeout=payload.sse_read_timeout,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    logger.info("MCP server %r created", row.name)
    return _serialize(row)


@router.patch("/mcp-servers/{name}", response_model=McpServerOut)
def update_mcp_server(
    name: str,
    payload: McpServerIn,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> McpServerOut:
    """Partial update — PATCH carries the same shape as
    POST but the ``name`` on the path is the source of
    truth. Sending a different ``name`` in the body
    returns 400 to make the rename-not-supported
    contract explicit.

    Blank env / header values are treated as "clear this
    key" (writes ``""`` to the JSON column). The loader
    reads ``""`` as "operator explicitly set this to
    empty, do not inherit from parent env".
    """
    row = session.get(McpServer, name)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.mcp_server",
            detail=f"mcp server {name!r} not found",
        )

    if payload.name != name:
        raise MagiHTTPException(
            status_code=400,
            code="validation.mcp_name_immutable",
            detail=(
                f"cannot rename mcp server via PATCH "
                f"(path={name!r}, body={payload.name!r}); "
                "delete + create to rename"
            ),
        )

    _validate_payload_for_connection_type(payload)

    row.connection_type = payload.connection_type
    row.command = payload.command
    row.args_json = json.dumps(payload.args)
    row.env_json = json.dumps(payload.env)
    row.url = payload.url
    row.headers_json = json.dumps(payload.headers)
    row.enabled = payload.enabled
    row.connect_timeout = payload.connect_timeout
    row.execute_timeout = payload.execute_timeout
    row.sse_read_timeout = payload.sse_read_timeout
    session.commit()
    session.refresh(row)
    logger.info("MCP server %r updated", row.name)
    return _serialize(row)


@router.delete("/mcp-servers/{name}", status_code=204)
def delete_mcp_server(
    name: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Hard delete the row. The next chat turn's
    :func:`maybe_reload_mcp_tools` will detect the
    missing row and drop the cached connection.
    """
    row = session.get(McpServer, name)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.mcp_server",
            detail=f"mcp server {name!r} not found",
        )
    session.delete(row)
    session.commit()
    logger.info("MCP server %r deleted", name)
    return Response(status_code=204)


@router.post("/mcp-servers/{name}/toggle", response_model=McpServerOut)
def toggle_mcp_server(
    name: str,
    _admin: AdminGate,
    session: Annotated[Session, Depends(get_session)],
) -> McpServerOut:
    """Flip the row's ``enabled`` flag. The loader
    filters on ``enabled=True`` so a disabled server
    doesn't connect on the next reload."""
    row = session.get(McpServer, name)
    if row is None:
        raise MagiHTTPException(
            status_code=404,
            code="not_found.mcp_server",
            detail=f"mcp server {name!r} not found",
        )
    row.enabled = not row.enabled
    session.commit()
    session.refresh(row)
    logger.info("MCP server %r toggled → enabled=%s", row.name, row.enabled)
    return _serialize(row)
