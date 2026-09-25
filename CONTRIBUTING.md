# Contributing to MAGI

Thanks for your interest in MAGI! This guide helps you get started.

## Quick start

```bash
git clone https://github.com/realTaki/MAGI.git
cd MAGI
npm install
```

One install at the root covers every workspace (`packages/` and `apps/`). The
root lockfile is not committed yet, so use `npm install` rather than `npm ci`.

MAGI runs on Node.js 24, and the desktop app is where it comes from — not a
separate install on the machine. A packaged install ships the runtime MAGI's
child processes use; a source checkout builds that same runtime into
`apps/shell/runtime/`:

```bash
(cd apps/shell && npm install && node scripts/prepare-runtime.mjs)
```

That runtime (`bin/node` plus its bundled npm CLI) is what builds and runs the app;
the pinned versions live in `apps/shell/package.json` and CI uses Node 24.
`apps/shell/main.mjs` is what puts it on a child process's `PATH`.

## Where to start

- **Good first issues** — tagged `good first issue` in Issues
- **Documentation** — translations, README improvements, docstrings
- **Tests** — adding tests for untested paths
- **Bug fixes** — pick an existing bug report and fix it

## Before you code

1. Open an Issue first (feature request or bug report). Let's discuss.
2. Once aligned, fork and create a branch: `feat/description` or `fix/description`.
3. Keep PRs small — one concern per PR.

## Code conventions

Two bars, not a tradeoff: **little code**, and **clear code**. Long code is hard to read. Extra glue that converts the same idea between two shapes is also noise — unify the type instead of mapping back and forth.

Then:

- **TypeScript** for the runtime (`apps/eva/`, `packages/`), `apps/asp/`, and the operator app (`apps/user/`)
- Follow what's already in the codebase:
  - English for code and comments (Chinese allowed in user-facing strings)
- `npm test` at the repository root (workspace build + runtime tests), `npm test` in
  `apps/asp/`, and `npm test` in `apps/user/` should pass before pushing

## Commit style

Conventional Commits:
```
feat: Add multi-channel dispatcher
fix: Handle empty contact notes rendering
refactor: Rename employees table to contacts
docs: Update README with new architecture
```

## PR checklist

- [ ] Issue linked
- [ ] Code follows existing patterns
- [ ] Tests pass locally
- [ ] No unrelated changes mixed in

## Project structure

| Directory | Purpose |
|-----------|---------|
| `apps/shell/` | Electron shell: window, bundled Node 24 and npm |
| `apps/user/` | Operator interface and local backend |
| `apps/asp/` | ASP chat server (`main.ts` + `server/` + `db/`, Node 24) |
| `apps/eva/` + `packages/` | The runtime entry and its packages: bus, agent, providers, tools, mcp, channels, tests |
| `docs/` | Design docs + roadmap |

**Package names follow one rule: a role in the society stays bare, everything else carries the `@magi/` scope.** `user` (the operator's app) and `eva` (the agent runtime behind each `eva-000`) are roles; `@magi/asp`, `@magi/shell` and every library under `packages/` are the machinery around them. The scope is not part of the module name, so `apps/asp` ↔ `@magi/asp` is aligned — what has to match is the *module*: `apps/user/`, `apps/eva/`, `apps/eva/eva.ts`.

## Questions?

Open a Discussion or ask in the Issue you're working on.
