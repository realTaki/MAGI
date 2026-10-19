---
name: project
description: Read and edit this node’s own MAGI/ source and request a local self rebuild. A Shell-role self rebuild also builds its UI and reloads the open window.
version: "1.0"
---

# Project

Your state (memory, skills, prompts, logs) is the workspace. The source you may change is the Git checkout at `MAGI/` inside that workspace. The Shell clone normally uses `magi/shell`, ASP uses `magi/asp`, and an agent uses `magi/<name>`; inspect the actual branch before editing. If `MAGI/.git` is missing, this process is not on its own clone: stop and say so. Do not guess another checkout path, and do not request a rebuild.

## Find and read

Paths for `list_files`, `read_file`, `search_files`, `edit_file`, and `write_file` are relative to the workspace. Prefix source paths with `MAGI/`.

`search_files` finds a literal string and returns `path:line: text`. Narrow it with `path` and `glob` before reading. `read_file` returns numbered lines (`N|text`); the number is not part of the file. Pass `offset` and `limit` to read past the first window. Load `codebase_search` before hunting for a symbol. Read `MAGI/docs/architecture.md` before changing a boundary between the shell, the app, ASP, and a MAGI.

| Tree | What runs from it |
| --- | --- |
| `MAGI/` on `magi/<you>` | This process, after a `self` rebuild |
| `magi/shell` (`~/.magi/shell/MAGI`) | Balthasar’s own checkout: Shell backend and UI. Other nodes do not edit it |
| `magi/asp` (`~/.magi/asp/MAGI`) | Casper’s own checkout: ASP. Other nodes do not edit it |

Each node edits only its own `MAGI/`. On Balthasar (`@shell.magi`, role `shell`), `MAGI/apps/shell/web/src` is its own UI source: a self rebuild builds that UI and the open window loads `MAGI_APP_CHECKOUT/apps/shell/dist/index.html` from the new revision. On an agent-role node, changing `apps/shell` does not update Balthasar. Ask the operator to use the Balthasar chat for a Shell UI change; do not edit another node’s checkout.

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

When a check can run in this process, run that one file with `MAGI_NODE`, the absolute bundled Node path supplied to this process; use its `MAGI_NPM` CLI for workspace scripts. If those are not supplied, use the checkout's `apps/shell/runtime/bin/node` and bundled npm. A revision/managed clone need not contain its own runtime copy. If no MAGI-owned runtime is available, say so. Do not use a system Node, and do not run the whole suite. If the check fails, do not `request_rebuild`.

## Rebuild

`request_rebuild` with target `self` (or `replace_self`) queues `ReplaceSelfJob` for this workspace’s `MAGI/`. The child captures saved local source, including staged/unstaged changes and non-ignored new files/deletions, tests and builds it, then asks its launcher to switch revision. It does not fetch upstream for a local rebuild and does not commit on your branch or push. Ignored files such as dependencies, generated output and secrets are not added to the snapshot. An explicitly requested revision/ref is a separate remote upgrade, not a way to activate unsaved/local edits. The launcher is a separate process. A failed switch leaves this process running and tells the operator. `bash` does not replace a running process. Do not kill your own process.

Call it only after the edit is saved, and say what changed first. In a Shell UI demo, wait for `[replace] replaced` and for the open window to load the new revision UI. Queuing a job is not proof that the new code is displayed. Another node replaces itself the same way. This node does not restart Shell or ASP.
