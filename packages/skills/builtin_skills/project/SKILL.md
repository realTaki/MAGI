---
name: project
description: Read and edit this MAGI's source checkout, then replace this node with request_rebuild target self.
version: "1.0"
---

# Project

Your state (memory, skills, prompts, logs) is the workspace. The source you may change is the Git checkout at `MAGI/` inside that workspace. For `@eva-000.magi` the branch is `magi/eva-000`. If `MAGI/.git` is missing, this process is not on its own clone: stop and say so. Do not guess another checkout path, and do not request a rebuild.

## Find and read

Paths for `list_files`, `read_file`, `search_files`, `edit_file`, and `write_file` are relative to the workspace. Prefix source paths with `MAGI/`.

`search_files` finds a literal string and returns `path:line: text`. Narrow it with `path` and `glob` before reading. `read_file` returns numbered lines (`N|text`); the number is not part of the file. Pass `offset` and `limit` to read past the first window. Load `codebase_search` before hunting for a symbol. Read `MAGI/docs/architecture.md` before changing a boundary between the shell, the app, ASP, and a MAGI.

| Tree | What runs from it |
| --- | --- |
| `MAGI/` on `magi/<you>` | This process, after a `self` rebuild |
| `magi/shell` (`~/.magi/shell/MAGI`) | Operator UI and Electron. Not this checkout |
| `magi/asp` (`~/.magi/asp/MAGI`) | ASP. Not this checkout |

Editing `MAGI/apps/shell` or `MAGI/apps/asp` does not change the running UI or ASP.

## Edit

Edit only under `MAGI/`. Do not write product source into `memories/`, `skills/`, `logs/`, or `prompts/`.

Prefer `edit_file` with one unique exact substring. Use `write_file` for a new file or a full rewrite of at most 256 KiB. Do not commit, push, or merge unless the operator asked. Never change git config. If a commit is required and git reports a missing `user.name` or `user.email`, stop and tell the operator.

A commit, when one was asked for, stays on this branch:

```bash
cd MAGI && git status --short && git add <paths you changed> && git commit -m "<why>"
```

Do not `git add` secrets, `memories/`, or `skills/`.

## Check

A saved file is not a passing change. `apps/eva/dist` tests run the last build, not the TypeScript you just wrote. Do not treat a green dist test as proof of an unbuilt edit.

When a check can run in this process, run that one file with the checkout's Node at `apps/shell/runtime/bin/node`. If that binary is missing, say so. Do not use a system Node, and do not run the whole suite. If the check fails, do not `request_rebuild`.

## Rebuild

`request_rebuild` with target `self` queues `ReplaceSelfJob` on this node. The child fetches, tests, and builds, then asks its launcher to switch revision. The launcher is a separate process. A failed switch leaves this process running and tells the operator. `bash` does not replace a running process. Do not kill your own process.

Call it only after the edit is saved, and say what changed first. Another node replaces itself the same way. This node does not restart Shell or ASP.
