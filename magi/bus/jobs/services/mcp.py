"""BUS-owned persistence for MCP configuration; never opens connections."""
from __future__ import annotations
import json
from typing import TYPE_CHECKING
from sqlalchemy import select
from magi.bus.jobs.protocols.mcp import McpServerConfig, McpServerView

if TYPE_CHECKING:
    from magi.mcp.loader import MCPTool

def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z") if value else ""

def _config(row) -> McpServerConfig:
    return McpServerConfig(row.name, row.connection_type, row.command, tuple(row.to_args_list()), row.url,
        bool(row.enabled), row.connect_timeout, row.execute_timeout, row.sse_read_timeout,
        row.to_env_dict(), row.to_headers_dict())
def _view(row) -> McpServerView:
    cfg = _config(row)
    return McpServerView(
        cfg.name, cfg.connection_type, cfg.command, cfg.args, cfg.url,
        cfg.enabled, cfg.connect_timeout, cfg.execute_timeout, cfg.sse_read_timeout,
        {key: bool(value) for key, value in cfg.env.items()},
        {key: bool(value) for key, value in cfg.headers.items()},
        _iso(row.created_at), _iso(row.updated_at),
    )

class McpService:
    def __init__(self, state_dir: str) -> None: self._state_dir = state_dir
    def list(self) -> list[McpServerView]:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s: return [_view(r) for r in s.scalars(select(McpServer).order_by(McpServer.name))]
    def enabled_configs(self) -> list[McpServerConfig]:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s: return [_config(r) for r in s.scalars(select(McpServer).where(McpServer.enabled.is_(True)))]
    def get(self, name: str) -> McpServerView | None:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s:
            row = s.get(McpServer, name)
            return _view(row) if row else None
    def get_config(self, name: str) -> McpServerConfig | None:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s:
            row=s.get(McpServer,name); return _config(row) if row else None
    def upsert(self, *, name: str, connection_type: str, command: str | None=None, args: list[str] | None=None, url: str | None=None, enabled: bool=True, env: dict | None=None, headers: dict | None=None, connect_timeout: float | None=None, execute_timeout: float | None=None, sse_read_timeout: float | None=None) -> McpServerView:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s:
            row=s.get(McpServer,name)
            if row is None:
                row=McpServer(name=name, connection_type=connection_type); s.add(row)
            row.connection_type=connection_type; row.command=command; row.args_json=json.dumps(args or []); row.url=url; row.enabled=enabled; row.env_json=json.dumps(env or {}); row.headers_json=json.dumps(headers or {}); row.connect_timeout=connect_timeout; row.execute_timeout=execute_timeout; row.sse_read_timeout=sse_read_timeout
            if connection_type == "stdio" and not (command or "").strip(): raise ValueError("stdio servers require command")
            if connection_type != "stdio" and not (url or "").strip(): raise ValueError(f"{connection_type} servers require url")
            s.commit(); s.refresh(row); return _view(row)
    def delete(self, name: str) -> bool:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s:
            row=s.get(McpServer,name)
            if row is None: return False
            s.delete(row); s.commit(); return True
    def toggle(self, name: str) -> McpServerView | None:
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s:
            row = s.get(McpServer, name)
            if row is None:
                return None
            row.enabled = not row.enabled
            s.commit(); s.refresh(row)
            return _view(row)
    def revision_stamp(self):
        from magi.bus.db.models.local.mcp_server import McpServer
        from magi.bus.db import open_session
        with open_session(self._state_dir) as s: return s.scalar(select(McpServer.updated_at).order_by(McpServer.updated_at.desc()).limit(1))

    def list_live_tools(self, name: str) -> "list[MCPTool] | None":
        """BUS-side query for the live tool list of one MCP server.

        Delegates to :func:`magi.mcp.loader.list_tools_for_server`,
        which prefers the active in-process connection (cheap)
        and falls back to a one-shot connect → list → disconnect
        when the operator opens the detail before the next chat
        turn has triggered ``maybe_reload_mcp_tools``.

        Returns:
          - ``None`` when the server name isn't in the table.
          - ``[]`` when the row exists but the subprocess failed
            to connect or returned no tools.
          - ``list[MCPTool]`` on success.

        Per ``docs/MAGI_MODULE_RESPONSIBILITIES_AND_DEPENDENCIES.md``
        §5.5 + §6, the WebUI API (``magi.channels.api``) MUST NOT
        import :mod:`magi.mcp` directly — MCP is a tools-adapter
        that the BUS orchestrates. This method is the one Python
        entry point for ``channels.api.mcp_servers``.
        """
        # Lazy import: keep the bus-side module side-effect free
        # for callers that only need the persistence methods.
        from magi.mcp.loader import list_tools_for_server

        return list_tools_for_server(name)
