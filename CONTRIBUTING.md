# Contributing to MAGI

Development source is [realTaki/MAGI](https://github.com/realTaki/MAGI).
`AgenticSocietyLab/MAGI` is the release fork; do not assume both repositories
contain the same revision. Discuss substantial changes in an Issue, then work
on a focused branch and open a reviewable PR.

## Prepare the toolchain

The checkout is one npm workspace (`packages/*`, `apps/*`) with a committed
root `package-lock.json`. Install against that lockfile with `npm ci`.
Use MAGI's own Node 24 and npm, as required by [AGENTS.md](AGENTS.md).
A prepared source build has:

```text
apps/shell/runtime/bin/node
apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js
```

Windows uses `bin/node.exe`. A packaged MAGI installation also supplies its
runtime. If neither is available, prepare the pinned runtime using the
[platform workflow bootstrap](.github/workflows/linux-validation.yml) and
[`prepare-runtime.mjs`](apps/shell/scripts/prepare-runtime.mjs); do not silently
fall back to an arbitrary system Node. CI has a separate initial Node 24
bootstrap stage before it switches to the MAGI-owned runtime. No global tool
installation, persistent PATH change or shell-startup edit is needed.

From a prepared checkout on Linux/macOS:

```sh
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js ci
PATH="$PWD/apps/shell/runtime/bin:$PATH" apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run build
```

Dependencies stay inside the workspace. Cache overrides, when needed, belong to
individual commands. See [Running MAGI](docs/running.md) for the EVA/Electron
startup order and [Testing MAGI](docs/testing.md) for commands and
[Verification](docs/verification.md) for acceptance boundaries.

## Code and review

Use TypeScript for runtime packages and follow neighboring code conventions.
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

Use Conventional Commits (`fix:`, `feat:`, `docs:`, `refactor:`). PRs should state
the problem, resulting behavior and relevant validation. Keep unrelated changes,
credentials, node workspaces and generated artifacts out of commits. Publishing
a release, changing a release fork and activating a desktop build are distinct
from saving a source change.

## Source map

| Path | Responsibility |
| --- | --- |
| `apps/shell/` | Electron window, process ownership, startup/profile helpers and React UI |
| `apps/eva/eva.mjs` | Launcher for Shell or an independent EVA |
| `packages/node/` | Role composition and launcher child entry |
| `packages/bus/` | Books, durable jobs and shared contracts |
| `packages/manager/` | Worker lifecycle, configuration and self-replacement |
| `packages/channel-peer/` | Trusted-network direct EVA messaging and group synchronization |
| `packages/webapp/` | Shell's desktop backend and source/runtime actions |
| `packages/agent/`, `packages/providers/`, other worker packages | Agent turns, model execution and tools/channels |
| `docs/` | Current guides and clearly marked historical rationale |

Package names come from the manifests: the launcher workspace is `eva`, the
desktop is `@magi/shell`, and libraries use `@magi/*`.
`@user.magi` is operator speech, not an npm workspace or separate process.

Security reports belong in [SECURITY.md](SECURITY.md), not public issue bodies.

## Dependency security maintenance

The root workspace lock is the only install lock. Do not regenerate separate
per-app locks: npm workspace installation and Electron's
production dependency collection resolve from the workspace root. Release
runtime and packaging caches must also use the root lock hash.

The scoped `@esbuild-kit/core-utils` override uses esbuild 0.25.12 to remove
[GHSA-67mh-4wv8-2f99](https://github.com/advisories/GHSA-67mh-4wv8-2f99)
from Drizzle's deprecated loader dependency. Its synchronous
and asynchronous TypeScript transforms are covered by the dependency policy
test. Remove the override when Drizzle removes or updates that dependency.

As of 2026-10-19, two advisories remain in electron-builder's development-only
chain through app-builder-lib 26.15.3 and @electron/get 3.1.0:

- http-cache-semantics 4.2.0, [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp): no patched version is listed
  by GitHub. The report is disputed upstream; 4.3.0 is not a verified fix.
- sprintf-js 1.1.3, [GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c): no patched release is available.

Keep these alerts visible. Do not override @electron/get to v5 just to remove
its old dependencies: its fetch-based downloader does not preserve the Got
agent and timeout options passed by the current electron-builder. A supported
upstream migration or a separately tested downloader change is required.
