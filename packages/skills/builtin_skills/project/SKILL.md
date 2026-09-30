---
name: project
description: Read and edit this MAGI's source checkout, then ask the desktop app to rebuild this MAGI, the operator UI, or ASP.
version: "1.0"
---

# Project

Your state (memory, skills, prompts, logs) is the workspace. The source you may change is the Git checkout at `MAGI/` inside that workspace. For `@eva-000.magi` the branch is `magi/eva-000`. If `MAGI/.git` is missing, this process is not on its own worktree: stop and say so. Do not guess another checkout path, and do not request a rebuild.

## Find and read

Paths for `list_files`, `read_file`, `search_files`, `edit_file`, and `write_file` are relative to the workspace. Prefix source paths with `MAGI/`.

`search_files` finds a literal string and returns `path:line: text`. Narrow it with `path` and `glob` before reading. `read_file` returns numbered lines (`N|text`); the number is not part of the file. Pass `offset` and `limit` to read past the first window. Load `codebase_search` before hunting for a symbol. Read `MAGI/ARCHITECTURE.md` before changing a boundary between the shell, the app, ASP, and a MAGI.

| Tree | What runs from it |
| --- | --- |
| `MAGI/` on `magi/<you>` | This process, after a `self` rebuild |
| `magi/user` (`~/.magi/user/MAGI`) | Operator UI. Not this checkout |
| `magi/asp` (`~/.magi/asp/MAGI`) | ASP. Not this checkout |

Editing `MAGI/apps/user` or `MAGI/apps/asp` does not change the running UI or ASP.

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

The desktop backend is not listening on a port. `request_rebuild` opens a `magi://` link; the installed MAGI app receives it and is the only process that may stop or start ASP, the UI, or you. `bash npm run build` does not replace a running process. Do not kill your own process.

Call `request_rebuild` only after the edit is saved, and say what changed first. The build log does not come back to this process.

| `target` | What the desktop does |
| --- | --- |
| `self` | `npm ci` and `npm run build` in this checkout, then restart only you. Uncommitted files in `MAGI/` are included. No merge. |
| `app` | Merge this branch into `magi/user`, rebuild the operator UI, then ask before reloading the window. ASP and other MAGI keep running. Uncommitted files are not included: commit first. |
| `asp` | Merge this branch into `magi/asp`, reinstall ASP, and start the society again. Every MAGI is restarted. Uncommitted files are not included: commit first. |

`app` and `asp` adopt the whole branch, not one file. Do not use them to publish an unrelated history. One rebuild at a time. If the tool says the link was opened, do not send another until the operator says the previous one finished. A conflict aborts the merge and does not build.
