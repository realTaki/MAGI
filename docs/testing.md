# Testing MAGI

Run tests with the project-owned Node and bundled npm CLI:

```sh
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js test
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run build:ui --workspace @magi/shell
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run lint:i18n --workspace @magi/shell
apps/shell/runtime/bin/node --test apps/shell/integration/self-rebuild.test.mjs
apps/shell/runtime/bin/node apps/shell/integration/chat-history.browser.mjs
apps/shell/runtime/bin/node apps/shell/integration/provider-form.browser.mjs
```

Root `npm test` builds the workspace and runs EVA, ASP and all Shell `.test.ts`
and `.test.mjs` files. The runner selects compiled EVA/ASP tests from their
current source names; obsolete files left in `dist` are not discovered. Running the entire Shell suite again in the same CI job
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
omitted. Native Windows tests are not skipped to accommodate fixture failures.

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
