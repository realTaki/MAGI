# Testing MAGI's DSH plugins

Use the MAGI-owned Node 24 and npm required by [AGENTS.md](../AGENTS.md), not a
system runtime. The repository-local runtime is prepared by `scripts/prepare-runtime.mjs`;
using it does not build or launch an Electron application.

```sh
runtime/bin/node runtime/npm/node_modules/npm/bin/npm-cli.js test
# Explicitly narrower portable gate (also used by Windows CI):
runtime/bin/node runtime/npm/node_modules/npm/bin/npm-cli.js run test:portable
```

`npm test` checks all five workspace manifests, lock metadata, distributed
resources and all top-level plugin JavaScript syntax, then runs **every**
regression in EVA, MAGI Shell, DM, memory and bot-mode. See the explicit roots in
[the test collector](<../scripts/test.mjs>). No
TypeScript output, old EVA launcher, Electron UI build, installer or AppImage is
needed. Test discovery also includes its own wiring/bundle-boundary regression
and the authoritative root-lock/dependency-advisory policy. Literal relative
imports in active bundles must stay inside their bundle; official DSH services
are supplied by the Host. EVA bundles independent DM, memory and bot-mode;
Shell bundles bot-mode. Actual standalone bot-mode and both nested application
archives are extracted and their resources/exports/imports validated in
[packaging tests](<../scripts/test/pack-dsh.test.mjs>). The deleted Electron Shell
and providers, and remaining archived pre-DSH packages, are not dependencies.

## CI scope and meaningful coverage

- **Linux and macOS:** complete current plugin suite, including real synthetic
  child processes, concurrent start/owner locks, readiness rejection, stop and
  controller-crash descendant cleanup, filesystem symlink/migration refusal,
  credential/auth boundaries, durable identity and native Session navigation.
- **Windows:** all plugin syntax/resources plus the explicitly enumerated
  portable Client/auth/navigation/manifest tests in `scripts/test.mjs` and shared
  wiring/dependency policy. It does **not** run the full Host suite: the latter
  assumes POSIX private file modes, directory fsync and filesystem Unix sockets.
  Green portable checks do not establish Windows Host/runtime support.
- Both gates use synthetic temporary homes and controller/provider fixtures.
  They do not install plugins in a real DSH profile, make paid model calls, or
  certify the native DSH Desktop/GUI experience.

The retired launcher's atomic-state contract is now also tested against the
**public DSH main-Session initializer** in `main-session-atomic.test.js`, for both
EVA and MAGI Shell. Partial writes and failed rename must preserve the previous
complete mapping, remove only their private temporary file, and retry without
creating another identity. Existing persistence-barrier, competing Host,
credential/auth and descendant-cleanup assertions remain enabled in the complete
suite. A failed meaningful safety assertion is not deleted or silently skipped.

## Bot preset native probe

[Canonical Bot contracts](<../packages/bot-mode/test/presets.test.js>) cover unchanged
capability rows, native expression spelling, platform guards, group isolation,
installation-anchored skills, generated legacy body equality and content hashes.
These contracts run in both gates. Packaging also executes each extracted
package-owned loader against a synthetic unrelated native installation anchor;
this detects source-path/hoisting assumptions without touching a live profile.

The optional [native probe](<../apps/eva/test/preset-native.js>) uses MAGI Electron
in Node mode and the installed DSH YAML schema, Include, Loader and registry:

```sh
ELECTRON_RUN_AS_NODE=1 /Applications/MAGI.app/Contents/MacOS/MAGI --expose-internals \
  apps/eva/test/preset-native.js \
  '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/package.json'
```

This verifies actual package loader → native Include → declaration registration,
source-linked directory resolution, parent duplicate-root collapse and child
disable rows. Real registry selection/binding uses empty synthetic plugin bodies
and an in-memory session projection: unset default becomes `bot-mode`, saved
`eva`/other choices remain, recorded `eva` binds without resetting the default.
No Host starts and no files are written. Full capability eager mount and persisted
Session reopening are explicitly untested; neither native imports nor empty-body
binding certify them, paid calls, deployment or live GUI acceptance.

## Retired pre-DSH code

The old Electron Shell and provider module have been removed. Remaining old
packages are archived source, excluded from the root workspace allowlist and
lockfile. Their provider-bearing launcher composition and Shell-dependent
webapp APIs are intentionally nonfunctional historical code. Root `build`,
`db:generate`, `test:legacy` and `verify:legacy` entries and old acceptance
runners/TypeScript solution are retired; no current command promises to compile
that archive. Do not restore removed modules to make it pass.

The relocated shared dependency policy remains enabled, including all
advisory-range checks. Only the Drizzle-specific transform smoke test retired
with that excluded dependency. No CI workflow publishes standalone packages.

[DSH handoff](../DSH-HANDOFF.md) distinguishes fixture regression, a real native
DSH integration, installation, model/tool calls and user-visible GUI acceptance.
Report exact revision, test counts and environmental failures separately. For
example, a sandbox that refuses creation of a temporary Unix socket has not
executed the socket-refusal assertion; it must not be reported as a pass.
