"""Workspace filesystem tools.

Four tools that act on the operator's workspace tree:

  - :class:`magi.tools.filesystem.read_file.ReadFileTool`
  - :class:`magi.tools.filesystem.write_file.WriteFileTool`
  - :class:`magi.tools.filesystem.edit_file.EditFileTool`
  - :class:`magi.tools.filesystem.list_files.ListFilesTool`

All four share :mod:`magi.tools._safe_path` for the
``safe_resolve`` boundary — every path is checked against
the workspace root before it touches disk.
"""
