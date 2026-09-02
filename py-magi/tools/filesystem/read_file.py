"""``read_file`` tool — read a UTF-8 file inside the
workspace root.

Path semantics: ``path`` is interpreted **relative to the
workspace root**. v0 also rejects paths that don't exist or
that are directories; the LLM should call ``list_files``
first when browsing.

Two read modes:

  - **Full file** (no ``offset``/``limit``): returns the
    file as a single text block, truncated at
    ``_MAX_BYTES`` (8 KB) with a marker so the LLM
    knows there's more.
  - **Window** (``offset`` and/or ``limit``): returns
    only the requested line range, prefixed with
    1-indexed line numbers in the format
    ``"     N|<content>"``. This is the same
    line-numbered output the reference bash / edit
    tools use, so the LLM can cross-reference
    ``"line 47"`` against ``grep`` / ``edit_file``
    output.

The line-numbered output is 7-char-wide for the
prefix (right-aligned). Files with very long lines
(> 1000 chars) still get one number per line, just
taller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.BaseTool import BaseTool, ToolResult

# 8 KB cap. Anything bigger is truncated and the
# truncation note is included so the model knows
# there's more. The LLM can re-call with
# ``offset``/``limit`` to read further.
_MAX_BYTES = 8 * 1024

# Width of the line-number column in the windowed
# output. 6 chars fit 999_999 lines; we use 6 to
# stay tight while leaving headroom for the
# truncation note.
_LINE_NUMBER_WIDTH = 6

# Cap on the offset/limit window. Without this the
# LLM could ask for a 1M-line slice and the tool
# would dutifully walk every line. The cap matches
# ``_MAX_BYTES`` semantics — the LLM should page
# through in chunks.
_MAX_LIMIT = 10_000


class ReadFileTool(BaseTool):
    """Read a UTF-8 file in the workspace."""

    name = "read_file"

    description = (
        "Read the contents of a UTF-8 text file. ``path`` is "
        'relative to the workspace root (e.g. ``"prompts/agent/AGENT.md"`` '
        'or ``"skills/calc.py"``). Use ``list_files`` first '
        "to discover files.\n\n"
        "Two read modes:\n"
        "  - No ``offset``/``limit``: returns the whole file. "
        "Files larger than 8 KB are truncated with a "
        "``[truncated at N bytes]`` marker.\n"
        "  - With ``offset`` and/or ``limit``: returns a "
        "**window** of lines (1-indexed). Output is "
        '``"     N|<line content>"`` per line so the LLM '
        "can cross-reference line numbers against "
        "``grep`` / ``edit_file`` output.\n\n"
        "Use ``edit_file`` for small targeted edits, "
        "``write_file`` for full rewrites."
    )

    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to the workspace root "
                    "(``bus.workspace``)."
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Starting line number (1-indexed). When "
                    "set, the tool returns the windowed "
                    "read mode instead of the full file. "
                    "Pair with ``limit`` to page through a "
                    "large file."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
                "description": (
                    "Number of lines to read from "
                    "``offset``. Defaults to "
                    "``_MAX_LIMIT`` when ``offset`` is "
                    "given without ``limit``."
                ),
            },
        },
        "required": ["path"],
    }

    @BaseTool.require_bus
    async def run(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path")
        offset_arg = kwargs.get("offset")
        limit_arg = kwargs.get("limit")

        if not isinstance(path_arg, str) or not path_arg:
            return ToolResult.err(
                "read_file: ``path`` is required and must be a non-empty string",
            )

        # Type-validate the optional ints. We don't
        # accept floats, strings that look like ints,
        # etc. — the SDK already does this for us, but
        # the tool belt-and-suspenders pattern matches
        # the other tools in this package.
        if offset_arg is not None and not isinstance(offset_arg, int):
            return ToolResult.err("read_file: ``offset`` must be an integer")
        if limit_arg is not None and not isinstance(limit_arg, int):
            return ToolResult.err("read_file: ``limit`` must be an integer")

        target = Path(self.bus.workspace) / path_arg

        try:
            raw = target.read_bytes()
        except OSError as e:
            return ToolResult.err(
                f"read_file: failed to read {path_arg!r}: {e}",
            )

        # Sniff encoding. ``read_file`` advertises UTF-8; we
        # accept any encoding the OS can decode but log a
        # warning when the file isn't valid UTF-8 so the
        # LLM doesn't silently corrupt bytes that look
        # like UTF-8 but aren't.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return ToolResult.err(
                f"read_file: {path_arg!r} is not valid UTF-8 "
                f"(decoded bytes {e.start}..{e.end}); the "
                f"tool only reads UTF-8 files."
            )

        # Windowed mode: line-numbered output. The LLM
        # gets the exact slice it asked for with no
        # truncation at the byte cap (a window of
        # <limit> lines is well under 8 KB even for
        # very wide lines).
        if offset_arg is not None or limit_arg is not None:
            return self._render_windowed(text, path_arg, offset_arg, limit_arg)

        # Full-file mode: byte-capped, with a truncation
        # marker if the file is bigger than 8 KB.
        truncated_marker = ""
        if len(raw) > _MAX_BYTES:
            # Decode the prefix only — avoid decoding then
            # re-encoding which would error on a partial
            # multi-byte sequence at the cutoff.
            head_bytes = raw[:_MAX_BYTES]
            # Round down to a valid UTF-8 boundary so we
            # never cut a multi-byte char in half.
            while head_bytes and ((head_bytes[-1] & 0b11000000) == 0b11000000):
                head_bytes = head_bytes[:-1]
            text = head_bytes.decode("utf-8", errors="replace")
            truncated_marker = (
                f"\n\n…[truncated at {_MAX_BYTES} bytes; "
                f"original was {len(raw)} bytes. Use "
                f"``offset`` and ``limit`` to read further.]"
            )

        return ToolResult(content=text + truncated_marker)

    @staticmethod
    def _render_windowed(
        text: str,
        path_arg: str,
        offset: int | None,
        limit: int | None,
    ) -> ToolResult:
        """Build the line-numbered windowed output.

        ``offset`` is 1-indexed (the LLM's natural
        mental model). Defaults: ``offset=1``,
        ``limit=_MAX_LIMIT`` when one is given without
        the other.

        Output format mirrors the reference bash
        tool's read-with-line-numbers::

             42|def foo():
             43|    return bar

        so the LLM can quote ``"line 47"`` and have
        the line number actually be 47, not off-by-one.
        """
        # ``splitlines(keepends=False)`` strips the
        # trailing newline so the output is
        # line-number, content — no double-newline
        # noise. ``splitlines`` returns one entry per
        # line; a trailing newline doesn't add an
        # empty trailing entry.
        lines = text.splitlines()

        # Defaults
        start = (offset - 1) if offset is not None else 0
        # When only ``limit`` is given, default
        # ``start`` to 0 (the LLM is just asking "give
        # me the first N lines").
        if offset is None and limit is not None:
            start = 0
        if limit is None:
            limit = _MAX_LIMIT
        # Clamp.
        if start < 0:
            start = 0
        if start >= len(lines):
            return ToolResult.err(
                f"read_file: ``offset`` {offset} is past "
                f"the end of {path_arg!r} (file has "
                f"{len(lines)} lines)."
            )
        end = min(start + limit, len(lines))

        selected = lines[start:end]
        # ``w`` width matches the column the LLM
        # expects. ``%6d|`` right-aligns. Files
        # with > 999_999 lines would lose the
        # alignment, but the LLM isn't going to
        # read those.
        formatted = "\n".join(
            f"{line_no:>{_LINE_NUMBER_WIDTH}d}|{line}"
            for line_no, line in zip(range(start + 1, end + 1), selected, strict=False)
        )

        # Annotate the slice so the LLM can resume
        # cleanly. "Lines X-Y of N" is the same shape
        # the reference bash tool prints in its
        # pagination note.
        header = f"[lines {start + 1}-{end} of {len(lines)} in {path_arg!r}]"
        suffix = ""
        if end < len(lines):
            suffix = f"\n[... {len(lines) - end} more lines; use ``offset={end + 1}`` to continue]"
        return ToolResult(content=f"{header}\n{formatted}{suffix}")


__all__ = ["ReadFileTool"]
