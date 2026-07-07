"""``list_files`` tool — list immediate children of a
directory inside the workspace.

Non-recursive by design: a recursive walk could dump tens
of thousands of ``node_modules`` files into the LLM's
context and burn the next turn's input budget. The model
gets the immediate directory listing and ``read_file``'s
into specific paths it cares about.

Result shape: a JSON-ish text blob. v0 doesn't bother
with structured blocks — the LLM parses plain text just
fine, and a future schema upgrade is a one-line change.
"""

from __future__ import annotations

import json
from typing import Any

from magi.agent.tools._safe_path import safe_resolve
from magi.agent.tools.base import Tool, ToolContext, ToolResult

_MAX_ENTRIES = 200


class ListFilesTool(Tool):
    """List immediate children of a directory."""

    name = "list_files"
    description = (
        "List the immediate children of a directory "
        "(non-recursive). ``path`` is relative to the "
        "workspace root; default ``\".\"`` lists the "
        "workspace itself. Each entry includes ``name``, "
        "``type`` (``file`` or ``dir``), and ``size`` in "
        "bytes (files only). Output is capped at 200 entries."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Directory path relative to the workspace "
                    "root. Defaults to ``\".\"`` (the workspace "
                    "itself). Must be a directory; passing a "
                    "file path is rejected."
                ),
                "default": ".",
            },
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        **kwargs: Any,
    ) -> ToolResult:
        path_arg = kwargs.get("path", ".")
        if not isinstance(path_arg, str):
            return ToolResult(
                content="list_files: ``path`` must be a string",
                is_error=True,
            )

        # ``must_be_file=False`` + manual existence check,
        # because we want to accept "directory that exists"
        # but reject "path that doesn't exist" and "path
        # that exists but is a file".
        try:
            target = safe_resolve(ctx.workspace, path_arg, must_be_file=False)
        except ValueError as e:
            return ToolResult(content=f"list_files: {e}", is_error=True)

        if not target.exists():
            return ToolResult(
                content=f"list_files: path does not exist: {path_arg!r}",
                is_error=True,
            )
        if not target.is_dir():
            return ToolResult(
                content=f"list_files: path is a file, not a directory: {path_arg!r}",
                is_error=True,
            )

        try:
            entries_raw = sorted(target.iterdir(), key=lambda p: p.name)
        except OSError as e:
            return ToolResult(
                content=f"list_files: failed to read directory: {e}",
                is_error=True,
            )

        truncated = len(entries_raw) > _MAX_ENTRIES
        if truncated:
            entries_raw = entries_raw[:_MAX_ENTRIES]

        entries: list[dict[str, Any]] = []
        for p in entries_raw:
            try:
                if p.is_dir():
                    entries.append({"name": p.name, "type": "dir"})
                else:
                    # ``stat().st_size`` — cheaper than the
                    # per-entry ``is_file`` we already did
                    # via ``is_dir``. Symlinks show their
                    # target's size; v0 doesn't bother
                    # resolving them.
                    entries.append({
                        "name": p.name,
                        "type": "file",
                        "size": p.stat().st_size,
                    })
            except OSError:
                # Broken symlink, race, permission denied
                # on a single entry — skip rather than fail
                # the whole listing.
                continue

        body = json.dumps(
            {
                "path": path_arg,
                "entries": entries,
                "truncated": truncated,
                "total_seen": len(entries_raw) + (len(entries_raw) - len(entries) if truncated else 0),
            },
            ensure_ascii=False,
        )
        suffix = (
            f"\n\n…[truncated: directory has more than {_MAX_ENTRIES} entries]"
            if truncated else ""
        )
        return ToolResult(content=body + suffix)