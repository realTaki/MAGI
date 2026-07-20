"""``edit_file`` tool — exact-string replacement in a workspace file.

Why this exists as a separate tool from ``write_file``:

- LLM doesn't have to repeat the full file content
  on every edit. The LLM reads once, then sends
  only the chunk it's changing + the replacement.
  Saves significant output tokens on a 1000-line
  file with a 5-line change.
- The ``old_str must match uniquely`` constraint is
  a safety rail: if the LLM guesses wrong about the
  current file content (it ran read_file in a
  previous turn, then made other edits), the call
  fails rather than silently patching a wrong
  occurrence. This matches the Claude Code
  reference tool's behaviour.

The tool **always** uses the same workspace-root
containment as ``read_file`` / ``write_file`` via
``safe_resolve`` — absolute paths and ``..``
escapes are rejected before the read.

Atomicity: reads the current file, performs the
substitution in memory, then writes back via the
same atomic ``tempfile.mkstemp`` + ``os.replace``
pattern as ``write_file``. A crash mid-edit leaves
the previous file intact.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from magi.agent.tools._safe_path import safe_resolve
from magi.agent.tools.base import Tool, ToolContext, ToolResult


# Cap on the size of the matched ``old_str`` string the
# LLM can send. A multi-megabyte "old" string is almost
# always a sign of a confused tool call (the LLM pasted
# the whole file rather than a 5-line chunk). 64 KB
# matches the ``read_file`` cap-with-margin.
_MAX_OLD_STR_BYTES = 64 * 1024


class EditFileTool(Tool):
    """Replace an exact substring in a workspace file."""

    name = "edit_file"

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
        "Replace an exact substring in a file. The ``old_str`` "
        "argument must match **uniquely** in the file — if the "
        "string appears more than once the call fails rather "
        "than silently patching the first occurrence.\n\n"
        "Use this for small, targeted edits. For full-file "
        "rewrites use ``write_file`` instead.\n\n"
        "You must call ``read_file`` on the file first so the "
        "``old_str`` you send matches the current content "
        "(whitespace, indentation, line endings). The call "
        "fails with a clear error message if the string is "
        "not found or appears more than once.\n\n"
        "The path is interpreted relative to the workspace "
        "root; absolute paths and ``..`` escapes are "
        "rejected."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to the workspace root. "
                    "Absolute paths and ``..``-escapes are "
                    "rejected."
                ),
            },
            "old_str": {
                "type": "string",
                "description": (
                    "Exact substring to find. Must appear "
                    "uniquely in the file (whitespace, "
                    "indentation, and line endings must "
                    "match). If the file has been edited "
                    "since the last read_file, this string "
                    "may not match — re-read first."
                ),
            },
            "new_str": {
                "type": "string",
                "description": (
                    "Replacement string. Use ``\"\"`` (empty) "
                    "to delete the matched chunk."
                ),
            },
        },
        "required": ["path", "old_str", "new_str"],
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path")
        old_str = kwargs.get("old_str")
        new_str = kwargs.get("new_str")

        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult(
                content="edit_file: ``path`` is required and must be a non-empty string",
                is_error=True,
            )
        if not isinstance(old_str, str) or not old_str:
            return ToolResult(
                content="edit_file: ``old_str`` is required and must be a non-empty string",
                is_error=True,
            )
        if not isinstance(new_str, str):
            return ToolResult(
                content="edit_file: ``new_str`` is required and must be a string",
                is_error=True,
            )
        if len(old_str.encode("utf-8")) > _MAX_OLD_STR_BYTES:
            return ToolResult(
                content=(
                    f"edit_file: ``old_str`` is "
                    f"{len(old_str.encode('utf-8'))} bytes; v0 "
                    f"limit is {_MAX_OLD_STR_BYTES}. Pass a "
                    f"smaller chunk (5-10 lines usually)."
                ),
                is_error=True,
            )

        # Path must exist (edit_file is for existing files
        # — for new files use write_file). safe_resolve
        # also enforces workspace containment.
        try:
            target = safe_resolve(ctx.workspace, path_arg)
        except ValueError as e:
            return ToolResult(content=f"edit_file: {e}", is_error=True)

        # Read the current file. UTF-8 only — matches
        # read_file / write_file's contract.
        try:
            original = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            return ToolResult(
                content=(
                    f"edit_file: {path_arg!r} is not valid UTF-8 "
                    f"({e}). Use a binary-safe workflow instead."
                ),
                is_error=True,
            )
        except OSError as e:
            return ToolResult(
                content=f"edit_file: failed to read {path_arg!r}: {e}",
                is_error=True,
            )

        # ``count`` is a fast str method: counts
        # non-overlapping occurrences. The unique-match
        # rule is a safety rail, not a performance
        # optimisation.
        occurrences = original.count(old_str)
        if occurrences == 0:
            return ToolResult(
                content=(
                    f"edit_file: ``old_str`` not found in "
                    f"{path_arg!r}. The file may have been "
                    f"edited since you last called read_file — "
                    f"re-read it and try again."
                ),
                is_error=True,
            )
        if occurrences > 1:
            return ToolResult(
                content=(
                    f"edit_file: ``old_str`` appears {occurrences} "
                    f"times in {path_arg!r}. The tool requires a "
                    f"unique match — include more surrounding "
                    f"context to disambiguate."
                ),
                is_error=True,
            )

        # Substitute and write atomically. We reuse the
        # write_file pattern (mkstemp + os.replace) so a
        # crash mid-write leaves the previous content
        # intact.
        new_content = original.replace(old_str, new_str, 1)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, target)
            except BaseException:
                # Clean up the tmp file on any failure so
                # we don't leave orphans on the workspace.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            return ToolResult(
                content=f"edit_file: failed to write {path_arg!r}: {e}",
                is_error=True,
            )

        # Tell the LLM what just happened in a way that
        # the next read_file / edit_file can verify.
        # ``diff`` is impractical here; a one-liner
        # summary is enough.
        old_lines = old_str.count("\n")
        new_lines = new_str.count("\n")
        return ToolResult(
            content=(
                f"edit_file: replaced {old_lines + 1} line(s) "
                f"with {new_lines + 1} line(s) in {path_arg!r}."
            ),
        )


__all__ = ["EditFileTool"]