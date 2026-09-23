# MAGI Repository Instructions

These instructions apply to the entire repository.

## Use MAGI-owned runtimes

MAGI must be developed, tested, built, and run with the runtimes owned by the
project or bundled with the MAGI desktop application. Do not silently fall back
to a system-installed Node.js or Python.

Preferred runtime order:

1. Use the component-local Python virtual environment when it exists:
   - `py-magi/.venv/bin/python`
2. For repository JavaScript tooling, use MAGI's bundled Node.js. In a prepared
   source build this is `desktop/runtime/bin/node`; in the installed macOS app
   it is `/Applications/MAGI.app/Contents/Resources/runtime/bin/node`.
3. Use the bundled `uv` and Python runtime to create or repair component virtual
   environments. In a prepared source build they are under `desktop/runtime/`;
   in the installed macOS app they are under
   `/Applications/MAGI.app/Contents/Resources/runtime/`.
4. On Windows or Linux, use the equivalent binaries inside the MAGI packaged
   runtime. Do not replace them with globally installed tools merely because
   the platform-specific path differs.

Invoke runtime binaries by explicit path whenever practical. A command-scoped
`PATH` is acceptable when a tool requires it, but it must contain only the
paths needed for that command and must not be persisted.

Examples:

```bash
py-magi/.venv/bin/python -m pytest py-magi/tests
desktop/runtime/bin/node --test magi-asp/test/*.test.ts
/Applications/MAGI.app/Contents/Resources/runtime/bin/node --test desktop/app/test/*.test.mjs
```

The `ts-magi` runtime uses Bun. Use the project-designated or packaged Bun
binary when one is available. Do not install Bun globally or substitute Node.js
for Bun-only APIs such as `bun:sqlite`.

## Keep dependencies inside the project

- Python dependencies belong in the appropriate component `.venv`; never run
  project installs into the bundled base Python or the system Python.
- JavaScript dependencies belong in the relevant project `node_modules`
  directory and must be installed from that project's lockfile.
- Do not use `pip install --user`, `sudo pip`, global `npm`/`pnpm`/`yarn`
  installs, `npm install -g`, or a global `uv tool install` for MAGI work.
- Do not create global shims or symlinks in `/usr/local/bin`, `/opt/homebrew/bin`,
  a user-level bin directory, or any other shared executable directory.
- Do not edit shell startup files such as `.zshrc`, `.zprofile`, `.bashrc`, or
  `.profile`; do not edit `/etc/paths`; and do not add MAGI runtime or virtual
  environment directories to a persistent user or system `PATH`.
- Do not set persistent system-wide environment variables for this repository.
  Pass environment variables to the individual command or child process.

If a required MAGI-owned runtime is missing or broken, report that condition or
repair the repository-local/bundled environment. Do not work around it by
modifying the user's system environment.

