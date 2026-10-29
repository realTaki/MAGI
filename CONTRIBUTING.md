# Contributing to MAGI

Development source is [realTaki/MAGI](https://github.com/realTaki/MAGI).
`AgenticSocietyLab/MAGI` is the release fork; do not assume both repositories
contain the same revision. For a substantial upstream change, discuss the scope
in an Issue, then work on a focused branch and open a reviewable PR.

## Current DSH development

The active product is `apps/eva` + `apps/magi-shell`, loaded by DSH as Host/Client
plugins. Use the owned runtime below with `npm test` (complete plugin acceptance)
or `npm run test:portable` (portable contracts only). No Electron UI compilation,
AppImage, installer or standalone release is required. See [Testing](docs/testing.md)
and [DSH handoff](DSH-HANDOFF.md) for exact coverage and real-DSH/GUI limitations.
The old Electron Shell and provider module are deleted. Other pre-DSH packages
remain archive-only source, excluded from installation and current acceptance.
Root TypeScript/build/database-generation/legacy gates are retired; do not
restore deleted launchers or providers to make that archived source compile.

## Your fork is not a temporary copy

MAGI is designed for users and their agents to change its source. Keep personal
adaptations in your own fork; you do not need upstream approval for every useful
change. Contribute back when a change serves the shared starting point.

There is no backward-compatibility promise. Before bringing an upstream change
into your version, inspect its effects on your code, stored state and running
agents. A fast-forward source update is not an automatic migration or an
intelligent merge of a diverged fork. Keep recoverable source history and back up
state you cannot recreate before making a risky change.

Agents are first-class readers and contributors. Documentation should explain
intent, constraints and how to verify behavior, not wrap the repository in
product ceremony. Useful comments and operational instructions still matter:
agent-readable does not mean context-free.

## Prepare the toolchain

The checkout explicitly installs only `apps/eva`, `apps/magi-shell` and
`packages/dm`, with a committed
root `package-lock.json`. Install against that lockfile with `npm ci`.
Use MAGI's own Node 24 and npm, as required by [AGENTS.md](AGENTS.md).
A prepared source build has:

```text
runtime/bin/node
runtime/npm/node_modules/npm/bin/npm-cli.js
```

Windows uses `bin/node.exe`. A packaged MAGI installation also supplies its
runtime. If neither is available, prepare the pinned runtime using the
[platform workflow bootstrap](.github/workflows/linux-validation.yml) and
[`prepare-runtime.mjs`](scripts/prepare-runtime.mjs); do not silently
fall back to an arbitrary system Node. CI has a separate initial Node 24
bootstrap stage before it switches to the MAGI-owned runtime. No global tool
installation, persistent PATH change or shell-startup edit is needed.
The checkout installer accepts an explicit absolute `MAGI_NODE` +
`MAGI_NPM_CLI` pair, an already prepared runtime, or the installed MAGI macOS
runtime; it never falls back to PATH Node/npm or nvm. If the runtime needing
replacement supplies the current bootstrap executable or npm CLI, provisioning
fails before deleting anything: invoke an external MAGI-owned runtime instead.

From a prepared checkout on Linux/macOS:

```sh
PATH="$PWD/runtime/bin:$PATH" runtime/bin/node runtime/npm/node_modules/npm/bin/npm-cli.js ci
PATH="$PWD/runtime/bin:$PATH" runtime/bin/node runtime/npm/node_modules/npm/bin/npm-cli.js test
```

Dependencies stay inside the workspace. Cache overrides, when needed, belong to
individual commands. See [Running MAGI](docs/running.md) for native DSH bundle preparation and [Testing MAGI](docs/testing.md) for commands and
[Verification](docs/verification.md) for acceptance boundaries.

## Code and review

Active plugins are directly executed JavaScript; follow neighboring conventions.
Prefer small, clear changes over extra abstractions. Workers coordinate through
BUS jobs rather than direct calls into peer workers. Before changing a process,
source or credential boundary, read [Architecture](docs/architecture.md).

A pre-release change need not preserve obsolete layouts or test data. Use fresh
isolated fixtures and current behavior; do not add compatibility machinery
without a product requirement. Preserve useful design rationale in versioned
history instead of keeping contradictory current instructions.

Verify the affected flow and required build/checks. Test output from generated
`dist` files is evidence for the last build, so compile changed TypeScript first.
Do not duplicate whole suites merely to accumulate green counts. Report what
was run, its revision, failures and platform limitations.

Use clear commit messages in whichever language communicates the change best.
Conventional prefixes (`fix:`, `feat:`, `docs:`, `refactor:`) are useful, not the
purpose of the contribution. PRs should state the problem, resulting behavior
and relevant validation. Keep unrelated changes, credentials, node workspaces
and generated artifacts out of commits. Publishing
a release, changing a release fork and activating a desktop build are distinct
from saving a source change.

## Source map

| Path | Responsibility |
| --- | --- |
| `apps/eva/` | DSH node lifecycle and independent child Hosts |
| `apps/magi-shell/` | Native Desktop session navigation |
| `packages/dm/` | Independent reliable local DM plugin, bundled by EVA |
| `scripts/` | Source checks, tests, portable packaging and owned runtime provisioning |
| Other `packages/` | Archived pre-DSH source; not installable workspaces |
| `docs/` | Current guides and clearly marked historical rationale |

Archived launcher composition still imports the removed provider module, and
archived webapp/runtime code still names the removed Electron Shell. These
contracts are intentionally not advertised as working builds or active APIs.

Security reports belong in [SECURITY.md](SECURITY.md), not public issue bodies.

## Dependency security maintenance

The root workspace lock is the only install lock. Do not regenerate per-app
locks. CI caches use the root lock hash. Node 24.21.0 and semver are explicit
root development dependencies; the runtime preparer pins npm 12.0.2.

The shared [dependency policy](<scripts/test/dependency-policy.test.mjs>) remains
in both complete and portable acceptance. It checks authoritative lock/cache
policy and resolved advisory ranges, plus the active-only install graph. The
old Drizzle loader transform smoke test and its scoped esbuild override retired
with the excluded Drizzle dependency; no advisory-range check was suppressed.
Electron-builder and the old provider SDK are no longer in the root install
graph. Historical advisory reports describe those archived revisions, not the
current lock.
