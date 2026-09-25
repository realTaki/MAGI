# Contributing to MAGI

Thanks for your interest in MAGI! This guide helps you get started.

## Quick start

```bash
git clone https://github.com/realTaki/MAGI.git
cd MAGI
cd shell
npm ci
npm ci --prefix ../app
cd ../asp
npm ci
cd ../magi
npm ci
```

MAGI runs on Node.js 24, and the desktop app is where it comes from — not a
separate install on the machine. A packaged install ships the runtime MAGI's
child processes use; a source checkout builds that same runtime into
`shell/runtime/`:

```bash
(cd shell && npm install && node scripts/prepare-runtime.mjs)
```

That runtime (`bin/node` plus its bundled npm CLI) is what builds and runs the app;
the pinned versions live in `shell/package.json` and CI uses Node 24.
`shell/main.mjs` is what puts it on a child process's `PATH`.

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

- **TypeScript** for `magi/`, `asp/`, and the operator app (`app/`)
- Follow what's already in the codebase:
  - English for code and comments (Chinese allowed in user-facing strings)
- `npm test` in `magi/`, `npm test` in `asp/`, and
  `npm test` in `app/` should pass before pushing

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
| `shell/` | Electron shell: window, bundled Node 24 and npm |
| `app/` | Operator interface and local backend |
| `asp/` | ASP chat server (`main.ts` + `server/` + `db/`, Node 24) |
| `magi/` | BUS, agent, providers, tools, channels, and tests |
| `docs/` | Design docs + roadmap |

## Questions?

Open a Discussion or ask in the Issue you're working on.
