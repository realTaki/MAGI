# MAGI Repository Instructions

These instructions apply to the entire repository.

## Use MAGI-owned runtimes

MAGI must be developed, tested, built, and run with the runtimes owned by the
project or bundled with the MAGI desktop application. Do not silently fall back
to a system-installed Node.js or Bun.

Preferred runtime order:

1. For repository JavaScript tooling, use MAGI's bundled Node.js. In a prepared
   source build this is `shell/runtime/bin/node`; in the installed macOS app
   it is `/Applications/MAGI.app/Contents/Resources/runtime/bin/node`.
2. For `magi`, use the project-designated or packaged Bun binary. In a
   prepared source build this is `shell/runtime/bin/bun`; in the installed
   macOS app it is `/Applications/MAGI.app/Contents/Resources/runtime/bin/bun`.
3. On Windows or Linux, use the equivalent binaries inside the MAGI packaged
   runtime. Do not replace them with globally installed tools merely because
   the platform-specific path differs.

Invoke runtime binaries by explicit path whenever practical. A command-scoped
`PATH` is acceptable when a tool requires it, but it must contain only the
paths needed for that command and must not be persisted.

Examples:

```bash
shell/runtime/bin/node --test asp/test/*.test.ts
/Applications/MAGI.app/Contents/Resources/runtime/bin/node --test app/test/*.test.mjs
cd magi
../shell/runtime/bin/bun run test
```

Do not install Bun globally or substitute Node.js for Bun-only APIs such as
`bun:sqlite`.

## Keep dependencies inside the project

- JavaScript dependencies belong in the relevant project `node_modules`
  directory and must be installed from that project's lockfile.
- Do not use global `npm`/`pnpm`/`yarn` installs or `npm install -g` for MAGI work.
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
