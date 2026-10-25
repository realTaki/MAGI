# Testing MAGI

Run tests with the project-owned Node and bundled npm CLI. Commands below use
Linux/macOS shell syntax. Windows uses `runtime/bin/node.exe`; see the
[Windows workflow](../.github/workflows/windows-validation.yml) for its
process-scoped PATH and PowerShell invocation.


```sh
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js test
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run build:ui --workspace @magi/shell
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run lint:i18n --workspace @magi/shell
apps/shell/runtime/bin/node --test apps/shell/integration/self-rebuild.test.mjs
apps/shell/runtime/bin/node apps/shell/integration/chat-history.browser.mjs
apps/shell/runtime/bin/node apps/shell/integration/provider-form.browser.mjs
```

Root `npm test` builds the remaining TypeScript projects and runs the DSH EVA
plugin's source `.test.js` files, Shell, bus, contacts, action-items, providers
and peer-channel tests. The plugin needs no compilation. Shell includes both
`.test.ts` and `.test.mjs` files. The runner selects compiled package tests from
their current source names; obsolete files left in `dist` are not discovered.
Legacy Shell tests and startup still reference the removed EVA launcher; this
entry-point adjustment does not complete that migration or certify the desktop.
See [the DSH handoff](../DSH-HANDOFF.md). Running the entire Shell suite again in the same CI job
adds no coverage. Tests use disposable local workspaces and fake providers;
they must close workers/databases before removing their workspaces. Never use a
real profile, credentials or paid model service as a test fixture.

Keep regressions for startup, source ownership and local-change preservation,
message routing/deduplication/history, provider failures, revision validation and
activation, and owned-process shutdown. Test observable behavior and boundaries.
Avoid checking fixture helpers against themselves, CSS placement, exact private
callback ordering, or every Cartesian combination of equivalent options. A
missing translation belongs in the locale lint, not a separate regex snapshot of
a component. Native platform behavior still needs native tests; CRLF simulation
on Linux does not establish Windows acceptance.

On push and pull requests, Linux runs the complete suite, UI build, locale lint
and launcher integration; Windows retains the complete suite, UI build and
launcher integration; macOS retains native lifecycle, process and Git-bootstrap
coverage. The identical extra Shell runs and macOS's duplicate UI build are
omitted. Native Windows tests are not skipped to accommodate fixture failures. The root
runner caps Windows test-file concurrency at two: each isolated process owns a
WMI process-table poller, so default file parallelism can overload the shared WMI
service. All in-file process-ownership and concurrent-start checks still run.

AppImage and unsigned Windows installer build/verification/artifact steps run
when their validation workflows are started manually with `workflow_dispatch`.
The tag-triggered macOS/Windows release workflows still build their installers
and test the built app with its owned runtime before uploading. This keeps
packaging validation available without building distribution artifacts for every
source push. Run the manual packaging workflows before a release candidate;
ordinary green source checks alone do not certify an installer.

A failing meaningful regression is a defect or a fixture problem to investigate,
not a reason to delete or skip it. In particular, keep the real cold-start and
shutdown checks: an unconfigured instance that cannot stop must remain visible.

[Verification](verification.md) separates these checks from native desktop,
installer and real-provider acceptance. Passing or documenting the commands does
not certify those outcomes; inspect the actual result at the target revision.

### Bootstrap helper types

The shell's `check:bootstrap` script runs a strict, no-emit TypeScript check over
`interface.mjs`, `runtime-environment.mjs`, and their TypeScript tests. Both the
root and shell test commands include this check. The bootstrap helpers deliberately
remain directly executable JavaScript: this type gate does not generate files or
add a compiler dependency to startup, UI reload, or runtime path preparation.
Their tests run directly with the bundled Node, including before a workspace build:

```bash
apps/shell/runtime/bin/node --test apps/shell/test/interface.test.ts apps/shell/test/runtime-environment.test.ts
```
