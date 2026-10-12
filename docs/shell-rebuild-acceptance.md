# Shell rebuild and Windows acceptance

Base: `realTaki/MAGI` main at `1a14cf3d3b62c9b98a33762e9cb80fb69a081bfd`.
This change does not update the organization repository or publish a release.

## Control boundary

Shell owns the OS processes, original entry/cwd/args/env, restart locks, exit
waiting and ready waiting for User, ASP and EVA. `magi://runtime/rebuild` is
handled by Shell, including while User is unavailable. User's backend can
request spawn/stop over its existing host channel; it does not implement the
self-rebuild transaction. Its retirement closes its own BUS, workers and host
pipe without stopping its peers. A broken User host pipe cannot crash Shell when a
peer exits; a replacement User restores the supervisor snapshot. Shell terminates all supervised apps on quit.
User's worker still holds roster/worktree policy and proxies to supervised
handles; moving those product policies is outside this change.

Targets must have a canonical `apps/<kind>/dist/<kind>.js` entry. Publishing an
EVA branch into User/ASP checks both `magi/<caller>` and `magi/user`/`magi/asp`,
refuses dirty target trees, and does not restart unrelated apps. Simultaneous
restarts of one supervisor id share one replacement. A stop followed by start
waits for the stopping process to exit instead of adopting its dying handle. Queued equivalent links
are coalesced; overlapping rebuild transactions are rejected.

An OS-accepted link is a request. A ready result requires the new process's IPC
ready signal after app initialization, not merely spawn. ASP has bound its
server by that point. EVA channel health/ASP connectivity is a separate axis.
Failure phases remain available via `runtime.rebuildStatus` for the current
Shell session; automatic rollback and cross-Shell persistent status are not
implemented. The existing link protocol is not a new authentication scheme.

## Reproduce in a prepared checkout

Use the MAGI-owned runtime; do not substitute a global Node installation.
From the repository root on Linux/macOS:

```sh
export PATH="$PWD/apps/shell/runtime/bin:$PATH" # this shell only
NODE_BIN="$PWD/apps/shell/runtime/bin/node"
NPM_CLI="$PWD/apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js"
"$NODE_BIN" "$NPM_CLI" run build
"$NODE_BIN" "$NPM_CLI" test
"$NODE_BIN" --test apps/user/test/user-bootstrap.test.ts apps/user/test/runtime-lifecycle.test.ts apps/user/test/deeplink.test.ts apps/user/test/magi-runtime.test.ts
"$NODE_BIN" "$NPM_CLI" run build --prefix apps/user
"$NODE_BIN" --check apps/shell/main.mjs
"$NODE_BIN" --check apps/shell/process.mjs
"$NODE_BIN" --check apps/shell/rebuild.mjs
git diff --check
```

Windows uses `apps/shell/runtime/bin/node.exe` and the same npm CLI. The
`Windows validation (no release)` workflow bootstraps the owned runtime, runs
root and full User acceptance, builds User, and produces an unsigned NSIS
installer with `--publish never`. It triggers on `codex/**` branches, main PRs
and manual dispatch, not tags. Its token has only `contents: read`; no release
job or release upload exists. The EXE is retained only as a short-lived Actions
artifact. Existing tag-release workflows are unchanged.

Root acceptance includes Shell tests. They use temporary directories and
explicit state paths: target isolation, freshly generated output, unchanged
identity/cwd/args, build/restart failures, no-ready timeout, duplicate restarts,
stop timeout, wrong entry/branches, and restored User management relationships.
Current compiled EVA CLI, ASP class and User CLI are exercised. The ignored
SIGTERM fixture is POSIX-only: Windows forcibly terminates that signal target.
The Windows path test runs on all platforms; native Windows CI provides the
platform-specific process/build evidence. Windows bootstrap passes an explicit
npm CLI to the owned Node rather than executing an npm.cmd shim with execFileSync.

The architecture test now inspects every worker package's source imports and
manifest dependencies, allowing only itself and BUS. Negative cases explicitly
reject peer imports and escapes to apps. It no longer scans removed monolithic
`apps/eva/dist/agent` directories.

CRLF regression tests use both `core.autocrlf=false` and `true` in disposable
Git repositories. They assert logical working-tree content, the exact LF Git
blob, an actual `two\r\n` checkout, merge/restart and conflict-abort behavior.
They never alter the user's Git configuration. Native Windows also requires
closing the legacy workspace fixture's SQLite connection and waiting for test
children to exit before deleting their working directories; these cleanup fixes
preserve all content and migration assertions. The broken-host test closes the
actual parent pipe; closing CRT fd 0 does not close Node's Windows pipe handle.

## Historical User baseline failures and their fixes

The following six failures reproduced at the original base and the Shell
rebuild delivery (`e7921cb7`). This follow-up fixes them and adds User state
regressions; they now belong to the full User acceptance gate, including the
real checkout startup integration. Its npm cache is explicitly inside the
disposable test root, including when invoked through npm (which injects a cache
path into child environments). The historical failure evidence is retained
below. No raw temporary logs are committed.

```sh
apps/shell/runtime/bin/node --test apps/user/test/chat-store.test.ts apps/user/test/import-asp-history.test.ts apps/user/test/provider.test.ts
```

| Test | Historical failure | Fix applied |
| --- | --- | --- |
| desktop chat history and pending receipts survive a restart | `bus` is undefined in directory `saveChats` | Supplied/reopened a real BUS alongside chat storage in the fixture |
| versioned baseline adopts existing desktop cache | Same missing BUS parameter | Retained migration assertions with the new directory owner |
| legacy ASP import preserves events without acknowledging | Missing BUS in `listChats` | Provided BUS and retained exact event/ack assertions |
| app saves provider key locally and broadcasts through ASP | Expected `provider.json` is absent | Asserted SettingsBook persistence and unchanged transient ASP broadcast |
| legacy app state moves to user directory | Expected preserved key is null | Moved directory preparation before BUS creates `user` |
| app copies legacy ASP key before deleting that copy | Old file-based migration assertion fails | Asserted stored SettingsBook key before deleting the ASP copy |

Every provider fixture now disposes its API in `t.after`, including failed
assertions, so an assertion failure cannot leak its relay timer. The fixes provide/reopen BUS in storage fixtures, verify SettingsBook rather
than the retired provider JSON file, and close resources before deleting test
directories. Import owns/closes both BUS and cache. Webapp initialization is
awaited before ready; disposal aborts its ASP requests, cancels polling, awaits
cache closure, and only then allows User to close BUS.

`apps/user/workspace.ts` migrates the old directory before BUS creation. Both
old/new directories present is a visible conflict: neither is merged, deleted
or overwritten. Provider files are removed only after an atomic SettingsBook
write; matching retry is idempotent, differing/invalid files stay recoverable.
User credential databases/WAL/SHM keep the old key file's POSIX 0600 privacy.
`user-state.test.ts` covers history/receipts/queued messages across rename and
restart, directory/provider conflicts, malformed migration data, atomic write
failure, ready failure and disposal during an active provider poll.

## Evidence and remaining end-to-end work

Local Linux acceptance after the User fixes: root 100/100, full User 49/49
with no skips (including real checkout startup), root and User builds passed. Native Windows result must be read from the workflow for the actual
pushed commit; producing an EXE does not prove installation or runtime boot.

Still queued: run one real EVA turn that edits its
own worktree, writes/runs a meaningful test, compiles, invokes the actual tool
and OS link, is restarted by Shell, then verifies the changed behavior and
preserved identity/state after ASP reconnection. The current tests prove these
components separately, not that entire OS-delivered self-modification loop.
After that, design failure recovery with explicit test gates, rollback and
state migration policy. No autonomous repeated self-modification is enabled.
