"""ORM table ``mcp_servers`` — operator-configured MCP server rows.

Each row is one Model-Context-Protocol server the operator
opted to surface. The ``MCP loader``
(:mod:`magi.agent.tools.mcp_loader`) reads these rows on
demand and turns each into a :class:`MCPServerConnection`.

Why env / headers live as JSON columns
--------------------------------------

MCP server config is small (a handful of vars per server)
and tightly coupled to the row — there's no value in
splitting it across a secrets sub-table. The API layer
(:mod:`magi.channels.webui.api.mcp_servers`) validates the
shape via Pydantic before serialising into the JSON
column, so a malformed row can only land if someone
hand-writes a row outside the API (which is fine —
``mcp_loader`` is defensive and skips bad rows).

Operator-controlled env semantics
---------------------------------

``env_json`` and ``headers_json`` are dicts the operator
keys+values. They are authoritative for the keys they
define; ``PATH``, ``HOME`` etc. still come from the
container env so stdio servers that depend on a binary
in ``PATH`` keep working. See
:func:`MCPServerConnection._open_streams` for the merge
in the loader.

The ``name`` column is the primary key. The ``name`` is
the operator-facing id (e.g. ``"minimax"``) and is also
the tool-name prefix surfaced to the LLM (``minimax__foo``).
Rename = delete + create, not PATCH.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from magi.agent.db.base import Base, utcnow_naive


# Defaults baked into the JSON columns so a freshly-created
# row has well-formed ``[]`` / ``{}`` content before the
# first edit. Keeping them at the model layer means the
# API endpoint can rely on ``r.args_json`` always being
# JSON-parseable without a ``None`` guard at the loader.
_DEFAULT_ARGS_JSON = "[]"
_DEFAULT_ENV_JSON = "{}"
_DEFAULT_HEADERS_JSON = "{}"


class McpServer(Base):
    """One operator-configured MCP server."""

    __tablename__ = "mcp_servers"

    # ``name`` is the operator-facing id and the tool-name
    # prefix for tools surfaced by this server
    # (e.g. ``minimax__echo``). URL-safe, ASCII,
    # max 64 chars to keep the prefix legible in the LLM's
    # tool list. Pattern is enforced at the Pydantic layer;
    # not a CHECK constraint because SQLite would store the
    # reject path and the UI already does the validation.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # STDIO
    command: Mapped[str | None] = mapped_column(String(256), nullable=True)
    args_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=_DEFAULT_ARGS_JSON,
        server_default=_DEFAULT_ARGS_JSON,
    )
    env_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=_DEFAULT_ENV_JSON,
        server_default=_DEFAULT_ENV_JSON,
    )

    # URL-based (sse / streamable_http)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    headers_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=_DEFAULT_HEADERS_JSON,
        server_default=_DEFAULT_HEADERS_JSON,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1",
    )

    # Per-server timeouts. ``None`` → loader uses
    # ``MCPTimeoutConfig`` defaults.
    connect_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    execute_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)
    sse_read_timeout: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False,
    )

    def to_env_dict(self) -> dict[str, str]:
        """Return ``env_json`` parsed. ``None`` on parse error so the
        loader can skip the row with a log instead of crashing the
        whole load on one bad entry."""
        return _parse_json_dict(self.env_json)

    def to_headers_dict(self) -> dict[str, str]:
        return _parse_json_dict(self.headers_json)

    def to_args_list(self) -> list[str]:
        return _parse_json_list(self.args_json)

    def __repr__(self) -> str:
        # Never include the env / headers — they may carry
        # API keys / Authorization tokens.
        return (
            f"McpServer(name={self.name!r}, "
            f"connection_type={self.connection_type!r}, "
            f"enabled={self.enabled!r})"
        )


def _parse_json_dict(raw: str | None) -> dict[str, Any]:
    """Best-effort parse of a JSON object column.

    Returns an empty dict on any error so the loader
    doesn't crash the whole load on one corrupted row —
    a missing/empty env doesn't fail the server, it just
    means no env overrides.
    """
    import json
    import logging

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning(
            "mcp_servers row has malformed JSON column; treating as empty"
        )
        return {}
    if not isinstance(parsed, dict):
        logging.getLogger(__name__).warning(
            "mcp_servers row JSON column is not an object; treating as empty"
        )
        return {}
    # Coerce non-string values to ``str`` so downstream
    # code can rely on ``dict[str, str]``. We don't
    # silently drop unknown types — coercing is the
    # least-surprising option.
    return {str(k): str(v) for k, v in parsed.items()}


def _parse_json_list(raw: str | None) -> list[str]:
    """Best-effort parse of a JSON array column.

    Same defensive shape as :func:`_parse_json_dict` —
    logs a warning and returns ``[]`` on a malformed row.
    """
    import json
    import logging

    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning(
            "mcp_servers row has malformed JSON column; treating as empty"
        )
        return []
    if not isinstance(parsed, list):
        logging.getLogger(__name__).warning(
            "mcp_servers row JSON column is not an array; treating as empty"
        )
        return []
    return [str(item) for item in parsed]


__all__ = ["McpServer"]
