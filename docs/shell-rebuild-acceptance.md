# Shell rebuild and Windows acceptance

Base: `realTaki/MAGI` main at `1a14cf3d3b62c9b98a33762e9cb80fb69a081bfd`.
This change does not update the organization repository or publish a release.

## Control boundary

Shell owns the OS processes, original entry/cwd/args/env, restart locks, exit
waiting and ready waiting for the operator process, ASP and EVA. `magi://runtime/rebuild` is
handled by Shell, including while the operator process is unavailable. The operator backend can
request spawn/stop over its existing host channel; it does not implement the
self-rebuild transaction. Its retirement closes its own BUS, workers and host
pipe without stopping its peers. A broken operator host pipe cannot crash Electron when a
peer exits; a replacement operator process restores the supervisor snapshot. Shell terminates all supervised apps on quit.
The operator worker still holds roster/clone policy and proxies to supervised
handles; moving those product policies is outside this change.

Targets must have a canonical `apps/<kind>/dist/<kind>.js` entry. Publishing an
EVA branch into Shell/ASP checks both `magi/<caller>` and `magi/shell`/`magi/asp`,
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
"$NODE_BIN" --test apps/shell/test/shell-bootstrap.test.ts apps/shell/test/runtime-lifecycle.test.ts apps/shell/test/deeplink.test.ts apps/shell/test/magi-runtime.test.ts
"$NODE_BIN" "$NPM_CLI" run build:ui --prefix apps/shell
"$NODE_BIN" --check apps/shell/main.mjs
"$NODE_BIN" --check apps/shell/process.mjs
"$NODE_BIN" --check apps/shell/rebuild.mjs
git diff --check
```

Windows uses `apps/shell/runtime/bin/node.exe` and the same npm CLI. The
`Windows validation (no release)` workflow bootstraps the owned runtime, runs
root and full Shell acceptance, builds Shell UI, and produces an unsigned NSIS
installer with `--publish never`. It triggers on `codex/**` branches, main PRs
and manual dispatch, not tags. Its token has only `contents: read`; no release
job or release upload exists. The EXE is retained only as a short-lived Actions
artifact. Existing tag-release workflows are unchanged.

Root acceptance includes Shell tests. They use temporary directories and
explicit state paths: target isolation, freshly generated output, unchanged
identity/cwd/args, build/restart failures, no-ready timeout, duplicate restarts,
stop timeout, wrong entry/branches, and restored operator-process management relationships.
Current compiled EVA CLI, ASP class and Shell CLI are exercised. The ignored
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
closing the workspace fixture's SQLite connection and waiting for test
children to exit before deleting their working directories; these cleanup fixes
preserve all content assertions. The broken-host test closes the
actual parent pipe; closing CRT fd 0 does not close Node's Windows pipe handle.

## Historical User baseline failures and their fixes

The following six failures reproduced at the original base and the Shell
rebuild delivery (`e7921cb7`). This follow-up fixes them and adds User state
regressions; they now belong to the full User acceptance gate, including the
real checkout startup integration. Its npm cache is explicitly inside the
disposable test root, including when invoked through npm (which injects a cache
path into child environments). The live fixture passes an explicit temporary
workspace instead of changing global HOME/USERPROFILE, reports install/build
phases and allows five minutes for cold Windows dependency installation. Its
commands observe the test cancellation signal; all content assertions remain. App entry detection compares real filesystem
paths so Windows short-path/directory aliases cannot silently skip main; a
compiled EVA directory-alias startup regression verifies this on all runners. The historical failure evidence is retained
below. No raw temporary logs are committed.

```sh
apps/shell/runtime/bin/node --test apps/shell/test/chat-store.test.ts apps/shell/test/import-asp-history.test.ts apps/shell/test/provider.test.ts
```

| Test | Historical failure | Fix applied |
| --- | --- | --- |
| desktop chat history and pending receipts survive a restart | `bus` is undefined in directory `saveChats` | Supplied/reopened a real BUS alongside chat storage in the fixture |
| ASP import preserves events without acknowledging | Missing BUS in `listChats` | Provided BUS and retained exact event/ack assertions |
| app saves provider key locally and broadcasts through ASP | Expected `provider.json` is absent | Asserted SettingsBook persistence and unchanged transient ASP broadcast |

Every provider fixture now disposes its API in `t.after`, including failed
assertions, so an assertion failure cannot leak its relay timer. The fixtures
provide/reopen BUS in storage, verify SettingsBook rather than a provider JSON
file, and close resources before deleting test directories. Import owns/closes
both BUS and cache. Webapp initialization is awaited before ready; disposal
aborts its ASP requests, cancels polling, awaits cache closure, and only then
allows User to close BUS.

Operator state lives at `~/.magi/shell`. There is no directory rename or
provider-file copy on startup. User credential databases/WAL/SHM keep POSIX
0600 privacy. `shell-state.test.ts` covers atomic SettingsBook write failure,
ready failure, and disposal during an active provider poll. Offline save still
retains the operator's requested configuration locally. Existing `~/.magi`
trees from earlier layouts are discarded and redeployed.

## Evidence and remaining end-to-end work

Local Linux acceptance after folding User into Shell: tsc -b green, ASP 19/19,
Shell 88/88 plus one skipped live MAGI-boot test. Root and Shell UI builds passed. Native Windows result must be read from the workflow for the actual
pushed commit; producing an EXE does not prove installation or runtime boot.

Still queued: run one real EVA turn that edits its
own clone, writes/runs a meaningful test, compiles, invokes the actual tool
and OS link, is restarted by Shell, then verifies the changed behavior and
preserved identity/state after ASP reconnection. The current tests prove these
components separately, not that entire OS-delivered self-modification loop.
After that, design failure recovery with explicit test gates, rollback and
state migration policy. No autonomous repeated self-modification is enabled.

## Native Linux x64 AppImage handoff

The Linux validation workflow builds `MAGI-Linux-x64.AppImage` on Ubuntu x64,
checks the packed Shell modules and ELF executables, and runs the packaged
Electron in Node mode plus packaged Node, npm, and dugite Git. Its
`linux-x64-appimage` artifact includes `SOURCE_COMMIT`, `SHA256SUMS`, and
`linux-verification.json`. This is build/runtime verification; window startup,
OS protocol registration, deep links, and a real self-update remain desktop
acceptance tasks.

A native package probe confirmed that Electron's `process.execPath` is the
executable inside the extracted AppDir. Linux updates now resolve the absolute
outer `APPIMAGE` instead, reject a missing/invalid outer path, and relaunch with
`APPIMAGE_EXTRACT_AND_RUN=1`. A disposable native shell-script regression checks
that the inner executable remains intact and only the outer file is replaced.
Explicit `publish: null` prevents electron-builder from auto-selecting update
metadata publishing from ambient GitHub credentials during unsigned builds.
The static desktop arguments no longer include `--no-sandbox`; upstream AppRun
still has its own user-namespace detection and fallback behavior. No kernel,
setuid, sandbox, FUSE, or desktop permission settings were changed.

On the separate test cloud desktop, verify `sha256sum -c SHA256SUMS`, make the
received file executable, then run:

```sh
./MAGI-Linux-x64.AppImage --appimage-extract-and-run
```

This official AppImage mode does not require FUSE. Use a fresh test desktop
profile and prepare `~/.magi/shell/MAGI` from `realTaki/MAGI` at the exact
`SOURCE_COMMIT` before first launch. The package ships Shell and tools; its
runtime User/ASP/EVA sources come from that checkout. Do not overwrite an
existing operator checkout or state. The existing default source URL points to
AgenticSocietyLab/MAGI, and setting only `MAGI_REPOSITORY_URL` would clone the
remote default branch rather than pin this validation commit. Source preparation
must therefore be explicit for this acceptance run. No organization repository
was written or synchronized.

References: [AppImage environment variables](https://docs.appimage.org/packaging-guide/environment-variables.html)
and [official extract-and-run troubleshooting](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).

## EVA self-modification process acceptance

Shell-owned EVA now sends `request_rebuild` through its private process IPC.
Shell derives the requesting handle from the live registered process spec;
caller-supplied handles, unknown targets, non-EVA entries and retiring/not-ready
processes are refused. The managed-checkout check and the existing clone,
entry-point, publishing-branch and transaction exclusion checks still apply.
A supervised caller with a disconnected IPC channel fails closed. Hand-run
processes retain the OS deep-link fallback. Acceptance is not readiness; Shell
retains/emits transaction status separately.

The activation order is dependency preparation, ordinary root tests (including
compilation), explicit build, old-process retirement, replacement readiness.
Failed tests or compilation leave the old loaded instance running. Windows EVA
uses the same IPC shutdown/drain path as Linux; after draining it disconnects
IPC so its listener does not hold the retiring process alive. User has acquired
no new lifecycle responsibility. Worker polling now starts only after all
configured workers initialize, so a pending recovered message sees its memory,
provider and tool context on its first turn.

`apps/shell/integration/self-rebuild.test.mjs` is an explicit CI step outside
the root test glob, so candidate `npm test` does not recursively activate a
second acceptance test. It creates an isolated `magi/self-loop` clone and
workspace, uses local OpenAI-compatible SSE and ASP HTTP/WebSocket fixtures,
and starts the real compiled EVA entry under the real Shell supervisor. The
model fixture drives the actual BUS tool channel to write candidate source and
a test, run tests, and request rebuild. Separate candidates intentionally fail
a test and TypeScript compilation. The valid candidate adds only a source
context marker; a replacement must demonstrate that marker in its actual
provider request. The test checks duplicate rejection, old exit before new
start, stable supervisor identity, ASP reconnection/replay position, nickname,
settings, long-term memory, and a message durably ingested while the old agent
is draining, then completed by the new process.

The model's decisions and ASP service are simulated. File tools, bash, provider
serialization, BUS databases, Node processes, candidate tests/build, private IPC
and supervisor replacement are real. This is a controlled self-modification
loop, not evidence of RSI or measured improvement. Electron GUI/OS protocol
acceptance, process-crash recovery during side effects, atomic artifact
activation, rollback to a known-good build, and persistent Shell transaction
journaling remain separate work. Candidate tests and source are not an
immutable security policy; this change does not grant new authority to change
policy or environmental settings.

During development the new IPC listener initially prevented clean exit; the
explicit post-drain disconnect fixed that introduced failure. The process
regression also exposed the pre-existing startup race that omitted memory from
a recovered turn's context; the worker-start barrier fixes it. The test fixture
removes only its inherited `NODE_TEST_CONTEXT` in its child environment copy;
leaking the outer harness context into nested test commands had produced
misleading exit codes during the first attempted gate verification.

The added `EVA that stalls before ready can still be retired` regression uses
an actual compiled EVA entry with only its startup await deliberately stalled.
It failed against this round's early signal handlers (one child remained after
stop). Signal handlers are now installed after startup completes: a not-ready
process retains normal signal termination, while a ready service drains over
IPC. This is bounded retirement, not startup rollback.

Native Windows acceptance exposed the previous unconditional `npm ci` over a
live EVA: it could not unlink the loaded SQLite `win32-x64.node` (EPERM).
Shell now records the lockfile and workspace dependency declarations at spawn,
reuses the live installation for source-only validation/build, and checks the
same declaration fingerprint before activation. A changed lock/dependency
manifest or missing installation is refused while retaining the old process.
Dependency upgrades need separately prepared activation; this is not an atomic
dependency updater or a guarantee against tampering with installed files.
The end-to-end test asserts exactly one install, before the first EVA starts.

The next Windows run rejected the valid candidate in the ordinary shell tool
suite. Review found that dugite preserves the OS variable spelling `Path`,
while the old environment builder read `.PATH` on a plain object and emitted
a second key. Node selects one case-insensitive variant for Windows children;
this could discard the system search path. Shell and the fixture now share a
path builder that preserves the inherited value and emits one canonical PATH
key. A deterministic Windows-key test and a real native PowerShell preflight
cover it. Only individual child environments change, never persistent settings.
See [Node child-process environment semantics](https://nodejs.org/api/child_process.html).

Candidate behavior verification also adds a pure square-calculation tool only
inside the temporary candidate clone. The restarted EVA must expose it in
its real model catalog, process a native model tool call through BUS, and return
`7 ** 2 === 49` from that new compiled tool before the simulated model can finish
the resumed reply. The tool is not added to the product's permanent catalog.

After restoring Windows shell lookup, candidate validation exposed a pre-existing
Telegram stop test that counted server arrivals and occasionally saw one late
in-flight request after stop. It now records client fetch initiation, requires
no new initiation after completed stop, and requires renewed `getUpdates` after
explicit restart. This preserves the lifecycle assertion without treating an
already-dispatched HTTP request's late arrival as a worker restart.

Native Windows subsequently completed all activation, arithmetic-tool,
reconnection and state assertions. The fixture now clones `magi/self-loop`
independently (`git clone --no-local`) and deletes that temporary tree with
Node's recursive removal. There is no shared object store and no `git worktree`
registration to prune.

## Shell shutdown during preparation

Shell closes process admission synchronously on `before-quit`, prevents the
Electron quit until cleanup finishes, and uses one owner for bootstrap Git/npm,
build/rebuild commands and supervised User/ASP/EVA roots. User's internal
command/dependency preparation requests go through its private host channel to
the same Shell owner. The host permits the bundled Node/Git and canonical owned
source directories; it adds no EVA-accessible command endpoint. `prepareAndStartShell`
checks admission both before preparation and after its final await. Dock activate
does not create another startup while shutdown is in progress.

POSIX roots have private process groups. Windows cleanup discovers process
ancestry/creation timestamps and uses scoped `taskkill /PID ... /T /F`; no
process-name kill is used. Roots that exit before descendants are reaped too.
Applications receive their existing graceful stop first. Cleanup then has finite
grace/force waits, with timeouts on native metadata/kill helpers. Errors are
reported rather than silently attributed to success. Close is idempotent.
The deliberately detached swap helper is spawned outside this owner and survives.

Managed dependency preparation records an installing/complete marker against
lock/dependency declarations. It checks npm's completed hidden lock plus required
installed package files, including nested workspace dependencies. An interrupted
marker or incomplete tree triggers npm ci on reopening; existence of node_modules
alone is insufficient. Only the source dependency installation is repaired,
not operator databases or workspace data. Unmanaged developer trees retain their
prior no-auto-install behavior. EN/JA/ZH runtimeHint now describes this shutdown
and repair behavior.

`shutdown.test.mjs` uses real Node roots, children and grandchildren to verify
command/application cleanup, exited-root orphan cleanup, unrelated same-binary
process survival, detached-helper survival, User→Shell command cancellation,
late prepare refusal and cold-install interruption/recovery with a preserved data
sentinel. Installer/application fixtures are deterministic local programs; full
User and EVA regressions cover the actual application entries.

Residual boundaries: GUI close interaction remains the separate cloud-desktop
acceptance. Forced cleanup may interrupt active tool side effects; rollback and
durable Shell transaction journaling remain separate work. A deliberately
detached descendant that escapes before observation or an OS-denied/privileged
process is outside the tested ordinary npm/app tree case; no elevation or global
kill fallback is introduced. This is graceful application exit cleanup, not an
OS job-object guarantee for a crashing Shell or arbitrary hostile descendants.

## Shell source/profile layout and Darwin process query

Shell's profile Git authority is `~/.magi/shell/MAGI`; generated caches are in
`~/.magi/shell/cache` (npm, Chromium HTTP cache, Electron downloads and builder).
User, ASP, and each EVA are independent clones under their own workspaces
(`~/.magi/shell/MAGI`, `~/.magi/asp/MAGI`, `~/.magi/<name>/MAGI`). They share
history only by push and fetch. Shell local installer build/replacement uses
`paths.checkout/apps/shell`, from the Shell checkout. Unpackaged root
resolution returns the repository root, two levels above `apps/shell`.

`profile-layout.mjs` owns serialized, journaled profile preparation. Bootstrap
clones into a staging directory, then renames the whole checkout into
`~/.magi/shell/MAGI` and checks out branch `magi/shell`. Interrupted operations
retain the journal and files for a forward retry after locks or permissions
are resolved. There is no checkout relocation, worktree repair, or cache merge.
Existing `~/.magi` trees from earlier layouts are discarded and redeployed.
`build/`, `dist/`, and `/cache/` are ignored in the tracked `.gitignore`.

Darwin's BSD ps has no Linux numeric `sid` field. It now queries `pid/ppid/pgid`,
never substitutes the opaque `sess` kernel pointer, and uses private detached
process groups plus observed ancestry for ownership. Linux keeps numeric SID;
Windows keeps its creation-time/PID checks and scoped taskkill trees. No process
name matching or global kill is added. Descendants that deliberately escape all
observed ancestry/groups before observation remain outside the guarantee; this
is not an OS Job Object or crash-survival guarantee.

Focused tests exercise a fresh clone, a resumed interrupted clone, a live
preparation lock, and recovery of a dead owner's lock. The full EVA integration
clones `magi/self-loop` independently, then performs real
self-edit/test/build/IPC replacement. Model/ASP endpoints are simulated, as in
the previous acceptance round.

Native macOS lifecycle/profile CI is added without packaging or publishing. Its
result, and final Linux/Windows full acceptance, must be read at the exact final
SHA. Linux adapter tests alone are not native macOS verification. Electron GUI,
OS deeplink and installed artifact swap remain separate parent-side acceptance.
The first packaged profile preparation runs before any BrowserWindow opens cache
files; this can delay the first startup window while cloning. No app is
started before it completes. Browser cookies/localStorage remain in the existing
User Electron profile; the Chromium HTTP cache uses an explicit disk-cache-dir.
