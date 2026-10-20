---
name: codebase_search
description: Locate modules, functions, and classes in the current workspace and explain what they do.
version: "1.0"
---

# Codebase search

Use `search_files` to find a literal string. Narrow it with `path` and `glob` (for example `*.ts`). Each hit is `path:line: text`. Use `read_file` with `offset` and `limit` when the path is known; the metadata header (full-file SHA-256, counts, truncation) and `N|` prefix are not part of the file. A truncated result is only a window; read further offsets for the remaining lines. A full rewrite requires `read_file full:true` (max 256 KiB), which returns an `overwrite_token` in addition to the full-file SHA-256; paged reads cannot authorize overwrites. Use `list_files` to discover nearby files. Report exact workspace-relative paths and explain the relevant behavior. Do not guess when the source can be inspected.
