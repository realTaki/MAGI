---
name: codebase_search
description: Locate modules, functions, and classes in the current workspace and explain what they do.
version: "1.0"
---

# Codebase search

Use `read_file` when the path is known and `list_files` to discover nearby files. Use `bash` with `rg` for symbol or text searches. Report exact workspace-relative paths and explain the relevant behavior. Do not guess when the source can be inspected.
