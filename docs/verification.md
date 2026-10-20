# Verification

[Testing MAGI](testing.md) is the single command reference for root tests, UI
build, locale lint, browser checks and launcher integration. It tracks the
current [manifest](../package.json), [runner](../scripts/test.mjs) and platform
workflows. Toolchain preparation is in [Contributing](../CONTRIBUTING.md).

Root `npm test` compiles the workspace, then selects generated EVA and peer/package tests from
current source names and runs Shell source tests. Deleted tests left in `dist`
are not rediscovered by that runner. Running the whole Shell suite a second time
in the same job adds no coverage. Use fresh command-scoped node/OS-home paths;
never use a live profile, credentials or paid provider as a fixture.

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
not publish releases. AppImage/unsigned Windows packaging steps run on manual
workflow dispatch, while tag-release workflows are separate. Ordinary source
pushes do not produce those validation packages. Do not treat a queued
workflow or an unsigned package as a passed desktop acceptance run.

For a fresh manual desktop run, open the intended source/installed app, confirm
the built-in Shell EVA is online, create or connect a peer on the trusted private
network, configure the intended node,
and verify a representative conversation. Check source activation separately:
a queued rebuild is not a loaded revision. Inspect the replacement result,
launcher revision and displayed UI. Tests of child-process replacement do not
load new Electron main/preload code.

[Historical acceptance rationale](history/shell-rebuild-acceptance.md) explains
earlier implementation decisions; its old counts and commands are not current
gates. The [architecture](architecture.md) defines the current mechanism and
[future work](future.md) records unproven guarantees.
