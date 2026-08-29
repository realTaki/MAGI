"""Workspace filesystem tools.

Four tools that act on the operator's workspace tree:

  - :class:`tools.filesystem.read_file.ReadFileTool`
  - :class:`tools.filesystem.write_file.WriteFileTool`
  - :class:`tools.filesystem.edit_file.EditFileTool`
  - :class:`tools.filesystem.list_files.ListFilesTool`

All four share :mod:`tools._safe_path` for the
``safe_resolve`` boundary — every path is checked against
the workspace root before it touches disk.
"""
