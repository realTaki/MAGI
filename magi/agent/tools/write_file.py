"""``write_file`` tool — atomically write a UTF-8 file
inside the workspace root.

Path semantics: same as ``read_file`` — relative to the
workspace root, absolute paths and ``..`` escapes are
rejected before the write happens.

Atomicity: write is via ``tempfile.mkstemp`` in the same
directory, ``fsync``, then ``os.replace`` — mirroring the
SOUL.md editor ([`magi/channels/webui/api/soul.py`]).
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
from typing import Any

from magi.agent.tools._safe_path import safe_resolve
from magi.agent.tools.base import Tool, ToolContext, ToolResult

_MAX_CONTENT_BYTES = 256 * 1024


class WriteFileTool(Tool):
    """Atomically write a file in the workspace."""

    name = "write_file"

    # Visible only to ``admin`` and ``assigned``
    # operators — same gate as the WebUI dashboard and
    # as ``ScheduleTaskTool`` / the action-item trio.
    # The chat path always passes the operator's role
    # through to ``handle_message(caller_role=...)`` so
    # non-eligible callers never see these tools in the
    # LLM's menu. ``MCPTool`` is intentionally permissive
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
                    "Full file contents. UTF-8. Limited to "
                    "256 KB to keep tool calls cheap."
                ),
            },
        },
        "required": ["path", "content"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path")
        content_arg = kwargs.get("content")

        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                content="write_file: ``path`` is required and must be a non-empty string",
                is_error=True,
            )
        if not isinstance(content_arg, str):
            return ToolResult(
                content="write_file: ``content`` is required and must be a string",
                is_error=True,
            )
        if len(content_arg.encode("utf-8")) > _MAX_CONTENT_BYTES:
            return ToolResult(
                content=(
                    f"write_file: content is {len(content_arg.encode('utf-8'))} "
                    f"bytes; v0 limit is {_MAX_CONTENT_BYTES}."
                ),
                is_error=True,
            )

        # Resolve the path WITHOUT ``must_be_file=True`` —
        # write_file creates the file. We still need the
        # workspace-containment check, which safe_resolve
        # always performs.
        try:
            target = safe_resolve(ctx.workspace, path_arg, must_be_file=False)
        except ValueError as e:
            return ToolResult(content=f"write_file: {e}", is_error=True)

        # Auto-create parent dirs. ``mkdir(parents=True, exist_ok=True)``
        # is idempotent — if the parent already exists it's a
        # no-op. We don't pre-check parent containment;
        # ``safe_resolve`` only checks the leaf, which is
        # fine because the leaf's resolved path is
        # guaranteed under workspace regardless of how the
        # path got there (a malicious ``a/b/c`` can't
        # produce a leaf outside ``a`` once we resolve).
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(
                content=f"write_file: failed to create parent dirs: {e}",
                is_error=True,
            )

        try:
            # Atomic write: mkstemp in target's dir, write,
            # fsync, rename. Mirrors SOUL.md editor.
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
            return ToolResult(
                content=f"write_file: failed to write {path_arg!r}: {e}",
                is_error=True,
            )

        bytes_written = len(content_arg.encode("utf-8"))
        return ToolResult(
            content=(
                f"write_file: wrote {bytes_written} bytes to {path_arg!r}"
            )
        )