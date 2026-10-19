---
name: self_update
description: Read and edit this node's own source, compile and run the full test suite in its own checkout, then request to replace itself after everything passes.
version: "1.0"
---

# Self-update

Your state (memory, skills, prompts, logs) is the workspace. The source you may change is the Git checkout at `MAGI/` inside that workspace. The Shell clone normally uses `magi/shell`, ASP uses `magi/asp`, and an agent uses `magi/<name>`; inspect the actual branch before editing. If `MAGI/.git` is missing, this process is not on its own clone: stop and say so. Do not guess another checkout path, and do not request a replace.

Work like a coding agent in this turn: read, edit, compile, and run the full test suite with `bash`. Failures belong in this turn so you can keep fixing. Call `request_replace` only after the shared verify command exits 0.

## Find and read

Paths for `list_files`, `read_file`, `search_files`, `edit_file`, and `write_file` are relative to the workspace. Prefix source paths with `MAGI/`.

`search_files` finds a literal string and returns `path:line: text`. Narrow it with `path` and `glob` before reading. `read_file` returns numbered lines (`N|text`); the number is not part of the file. Pass `offset` and `limit` to read past the first window. Load `codebase_search` before hunting for a symbol. Read `MAGI/docs/architecture.md` before changing a boundary between the shell, the app, ASP, and a MAGI.

| Tree | What runs from it |
| --- | --- |
| `MAGI/` on `magi/<you>` | This process, after a successful replace |
| `magi/shell` (`~/.magi/shell/MAGI`) | Balthasar’s own checkout: Shell backend and UI. Other nodes do not edit it |
| `magi/asp` (`~/.magi/asp/MAGI`) | Casper’s own checkout: ASP. Other nodes do not edit it |

Each node edits only its own `MAGI/`. On Balthasar (`@shell.magi`, role `shell`), `MAGI/apps/shell/web/src` is its own UI source: a self replace builds that UI and the open window loads `MAGI_APP_CHECKOUT/apps/shell/dist/index.html` from the new revision. On an agent-role node, changing `apps/shell` does not update Balthasar. Ask the operator to use the Balthasar chat for a Shell UI change; do not edit another node’s checkout.

## Edit

Edit only under `MAGI/`. Do not write product source into `memories/`, `skills/`, `logs/`, or `prompts/`.

Prefer `edit_file` with one unique exact substring. Use `write_file` for a new file or a full rewrite of at most 256 KiB. Do not commit, push, or merge unless the operator asked. Never change git config. If a commit is required and git reports a missing `user.name` or `user.email`, stop and tell the operator.

A commit, when one was asked for, stays on this branch:

```bash
cd MAGI && git status --short && git add <paths you changed> && git commit -m "<why>"
```

Do not `git add` secrets, `memories/`, or `skills/`.

## Compile and test

A saved file is not a passing change. `apps/eva/dist` tests run the last build, not the TypeScript you just wrote. Do not treat a green dist test as proof of an unbuilt edit.

`bash` uses bash on Linux/macOS and Windows PowerShell 5.1 on Windows. Use the syntax for the current platform; PowerShell environment variables use `$env:MAGI_NODE`, and PowerShell 5.1 does not support `&&`.

From `MAGI/`, run the same verify entry the replace safety gate runs (`verifyCheckout`). Use `MAGI_NODE` and `MAGI_NPM` from the environment; never a system Node. Foreground `bash` keeps only a head and tail of output, so a long verify job should use `run_in_background` and `bash_output`, or redirect to a file and `read_file` the end. Raise `timeout` if you stay in the foreground.

```bash
cd MAGI
"$MAGI_NODE" "$MAGI_NPM" run verify
```

On Windows, `bash` is PowerShell 5.1:

```powershell
Set-Location MAGI
& $env:MAGI_NODE $env:MAGI_NPM run verify
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Read the log in this turn. If the entry fails, fix the source and run it again. Do not `request_replace` until it exits 0.

## Replace

`request_replace` asks this node's launcher to replace the running process. Call it only after compile and tests are already green. This process cannot safely stop and restart itself; the parent snapshots saved local source (staged/unstaged changes and non-ignored new files/deletions), runs that same verify entry as a last safety gate, then drains, switches revision, health-checks, and rolls back on failure. It does not fetch upstream for a local replace and does not commit or push. Ignored files such as dependencies, generated output and secrets are not added to the snapshot.

Call it only after the edit is saved, and say what changed first. Queuing a job is not proof the new process is running. Another node replaces itself the same way. This node does not restart Shell or ASP. `bash` does not replace a running process. Do not kill your own process.
