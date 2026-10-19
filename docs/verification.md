# Verification

Use a prepared MAGI-owned runtime; setup is in [Contributing](../CONTRIBUTING.md).
From the repository root on Linux/macOS:

```sh
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js test
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run build:ui --workspace @magi/shell
apps/shell/runtime/bin/node apps/shell/scripts/i18n-lint.ts
```

`npm test` is defined in the root [manifest](../package.json). It compiles the
workspace before running runtime and Shell tests. Use new, command-scoped
`MAGI_HOME`/`MAGI_OS_HOME` directories when the test environment does not already
provide them; never reuse live credentials or a desktop profile. Do not run
compiled tests from an older branch and assume they validate current source.
A build does not necessarily remove orphaned generated tests.

Choose additional checks for the changed boundary rather than re-running the
same suite through every workspace. For a scientific or behavior claim, record
the expected outcome, relevant fixture/control, exact commit and actual result.
Only synthetic credentials belong in automated provider tests.

## Evidence levels

- A focused fixture can establish a function, persistence or permission boundary.
- A real launcher/child integration can establish process readiness and loaded
  code against its fixture services. It does not prove Electron interaction.
- A browser fixture checks UI interaction against its declared mock boundaries.
- A native CI job establishes only the steps actually run on that platform.
- Building an installer/AppImage establishes an artifact, not installation,
  authentication with a real provider or complete demo acceptance.

The platform [workflows](../.github/workflows) specify their own test/build and
artifact steps. Read results at the exact pushed SHA. Validation workflows do
not publish releases; tag-release workflows are separate. Do not treat a queued
workflow or an unsigned package as a passed desktop acceptance run.

For a fresh manual desktop run, start ASP, open the intended source/installed
app, confirm the default node handles are online, configure the intended node,
and verify a representative conversation. Check source activation separately:
a queued rebuild is not a loaded revision. Inspect the replacement result,
launcher revision and displayed UI. Tests of child-process replacement do not
load new Electron main/preload code.

[Historical acceptance rationale](history/shell-rebuild-acceptance.md) explains
earlier implementation decisions; its old counts and commands are not current
gates. The [architecture](architecture.md) defines the current mechanism and
[future work](future.md) records unproven guarantees.
