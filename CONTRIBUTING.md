# Contributing to MAGI

Thanks for your interest in MAGI! This guide helps you get started.

## Quick start

```bash
git clone https://github.com/realTaki/MAGI.git
cd MAGI
uv sync --group dev
```

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

- **Python 3.12+** with `ruff` for linting
- **TypeScript** + **React** for the operator app
- Follow what's already in the codebase:
  - English for code and comments (Chinese allowed in user-facing strings)
  - SQLAlchemy 2.0 style (`mapped_column`, `Mapped[]`)
  - FastAPI dependency injection pattern
- `cd py-magi && ruff check . && pytest tests/` should pass before pushing

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
| `py-magi/agent/` | Agent loop, memory, tools |
| `py-magi/channels/` | Telegram + HTTP channel adapters |
| `webapp/` | Browser operator UI |
| `desktop/` | Electron desktop process and local App storage |
| `py-magi/tests/` | Unit and integration tests |
| `docs/` | Design docs + roadmap |

## Questions?

Open a Discussion or ask in the Issue you're working on.
