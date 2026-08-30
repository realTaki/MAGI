"""``write_file`` tool — atomically write a UTF-8 file
inside the workspace root.

Path semantics: same as ``read_file`` — relative to the
workspace root.

Atomicity: write is via ``tempfile.mkstemp`` in the same
directory, ``fsync``, then ``os.replace`` — matching the
PromptBook-backed persona editor ([`magi/channels/api/soul.py`]).
A crash mid-write leaves the old file intact.

Content cap: 256 KB. Larger writes are rejected — the
LLM shouldn't be writing huge blobs anyway, and a
``write_file`` call with a 50 MB ``content`` field would
spend the LLM's output budget on the next turn in the
loop instead of producing a useful reply.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

_MAX_CONTENT_BYTES = 256 * 1024


class WriteFileTool(BaseTool):
    """Atomically write a file in the workspace."""

    name = "write_file"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The agent worker resolves the operator's role from the
    # Contact row and filters the tool menu so non-eligible
    # callers never see these tools in the LLM's menu.
    # ``MCPTool`` is intentionally permissive
    # (operator-configured at the MCP server level).
    ALLOWED_ROLES = frozenset({"admin", "assigned"})
    description = (
        "Write ``content`` to ``path`` (relative to the "
        "workspace root). Overwrites the file if it exists. "
        "Atomic: a crash mid-write leaves the previous "
        "content intact. Use this to update notes, configs, "
        "or any workspace-resident file the model needs to "
        "produce."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Destination path relative to the workspace "
                    "root. Parent directories are created if "
                    "they don't exist."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Full file contents. UTF-8. Limited to 256 KB to keep tool calls cheap."
                ),
            },
        },
        "required": ["path", "content"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path")
        content_arg = kwargs.get("content")

        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult.err(
                "write_file: ``path`` is required and must be a non-empty string",
            )
        if not isinstance(content_arg, str):
            return ToolResult.err(
                "write_file: ``content`` is required and must be a string",
            )
        if len(content_arg.encode("utf-8")) > _MAX_CONTENT_BYTES:
            return ToolResult.err(
                f"write_file: content is {len(content_arg.encode('utf-8'))} "
                f"bytes; v0 limit is {_MAX_CONTENT_BYTES}."
            )

        target = Path(self.bus.workspace) / path_arg

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult.err(
                f"write_file: failed to create parent dirs: {e}",
            )

        try:
            # Atomic write: mkstemp in target's dir, write,
            # fsync, rename. Matches PromptBook's atomic persona write.
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content_arg)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, target)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
                raise
        except OSError as e:
            return ToolResult.err(
                f"write_file: failed to write {path_arg!r}: {e}",
            )

        bytes_written = len(content_arg.encode("utf-8"))
        return ToolResult(content=(f"write_file: wrote {bytes_written} bytes to {path_arg!r}"))
