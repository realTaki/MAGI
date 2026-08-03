"""BUS DTOs for operator-configured MCP servers."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class McpServerView:
    name: str; connection_type: str; command: str | None; args: tuple[str, ...]
    url: str | None; enabled: bool; connect_timeout: float | None
    execute_timeout: float | None; sse_read_timeout: float | None

@dataclass(frozen=True, slots=True)
class McpServerConfig(McpServerView):
    env: dict[str, str]; headers: dict[str, str]
