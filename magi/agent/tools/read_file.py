"""``read_file`` tool — read a UTF-8 file inside the
workspace root.

Path semantics: ``path`` is interpreted **relative to the
workspace root**. Absolute paths and ``..``-escape attempts
are rejected by ``_safe_path.safe_resolve`` before the
read happens. v0 also rejects paths that don't exist or
that are directories; the LLM should call ``list_files``
first when browsing.

Output cap: 8 KB. Anything bigger is truncated and the
truncation note is included so the model knows there's
more and can ``read_file`` again with offsets (future
work — for now the model sees the first 8 KB).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from magi.agent.tools._safe_path import safe_resolve
from magi.agent.tools.base import Tool, ToolContext, ToolResult

_MAX_BYTES = 8 * 1024
_MAX_PATH_LEN_BRIEF = 256  # for the "first N chars" header on truncation


class ReadFileTool(Tool):
    """Read a UTF-8 file in the workspace."""

    name = "read_file"
    description = (
        "Read the contents of a UTF-8 text file. ``path`` is "
        "relative to the workspace root (e.g. ``\"SOUL.md\"`` "
        "or ``\"skills/calc.py\"``). Files larger than 8 KB are "
        "truncated and a note is appended. Use ``list_files`` "
        "to discover files before reading."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to the workspace root. "
                    "Absolute paths and paths containing ``..`` "
                    "are rejected."
                ),
            },
        },
        "required": ["path"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path")
        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                content="read_file: ``path`` is required and must be a non-empty string",
                is_error=True,
            )
        try:
            target = safe_resolve(ctx.workspace, path_arg)
        except ValueError as e:
            return ToolResult(content=f"read_file: {e}", is_error=True)

        try:
            raw = target.read_bytes()
        except OSError as e:
            return ToolResult(
                content=f"read_file: failed to read {path_arg!r}: {e}",
                is_error=True,
            )

        # Sniff encoding. ``read_file`` advertises UTF-8; we
        # accept any encoding the OS can decode but log a
        # warning when the file isn't valid UTF-8 so the
        # LLM doesn't silently corrupt bytes that look
        # like UTF-8 but aren't.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return ToolResult(
                content=(
                    f"read_file: {path_arg!r} is not valid UTF-8 "
                    f"(decoded bytes {e.start}..{e.end}); the "
                    f"tool only reads UTF-8 files."
                ),
                is_error=True,
            )

        truncated_marker = ""
        if len(raw) > _MAX_BYTES:
            # Decode the prefix only — avoid decoding then
            # re-encoding which would error on a partial
            # multi-byte sequence at the cutoff.
            head_bytes = raw[:_MAX_BYTES]
            # Round down to a valid UTF-8 boundary so we
            # never cut a multi-byte char in half.
            while head_bytes and (
                (head_bytes[-1] & 0b11000000) == 0b11000000
            ):
                head_bytes = head_bytes[:-1]
            text = head_bytes.decode("utf-8", errors="replace")
            truncated_marker = (
                f"\n\n…[truncated at {_MAX_BYTES} bytes; "
                f"original was {len(raw)} bytes]"
            )

        return ToolResult(content=text + truncated_marker)