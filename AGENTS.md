# MAGI Repository Instructions

These instructions apply to the entire repository.

## Use MAGI-owned runtimes

MAGI must be developed, tested, built, and run with the runtimes owned by the
project or bundled with the MAGI desktop application. Do not silently fall back
to a system-installed Node.js.

Preferred runtime order:

1. For repository JavaScript tooling, use MAGI's bundled Node.js. In a prepared
   source build this is `apps/shell/runtime/bin/node`; in the installed macOS app
   it is `/Applications/MAGI.app/Contents/Resources/runtime/bin/node`.
2. On Windows or Linux, use the equivalent binaries inside the MAGI packaged
   runtime. Do not replace them with globally installed tools merely because
   the platform-specific path differs.

Invoke runtime binaries by explicit path whenever practical. A command-scoped
`PATH` is acceptable when a tool requires it, but it must contain only the
paths needed for that command and must not be persisted.

Examples:

```bash
apps/shell/runtime/bin/node --test packages/channel-a2a/dist/test/*.test.js
/Applications/MAGI.app/Contents/Resources/runtime/bin/node --test apps/shell/test/*.test.ts
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js test
```

The current product is the directly executed DSH Host/Client bundles in
`apps/eva` and `apps/magi-shell`; neither needs a TypeScript or Electron build.
Root `npm test` checks their source syntax/manifests and runs every DSH plugin
regression plus shared dependency-policy checks. Linux/macOS run this complete
suite. `npm run test:portable` is the explicitly narrower Windows Client/auth/
bundle-contract gate; it does not certify Host lifecycle or POSIX persistence.

`apps/shell` and `packages/*` remain pre-DSH source, not dependencies of these
bundles. Their TypeScript build and old acceptance remain explicitly available
as `npm run test:legacy` / `npm run verify:legacy`, with known removed-launcher
fixtures. Do not restore the retired EVA launcher to make them pass. There is no
standalone Electron/AppImage/installer/release pipeline. See `DSH-HANDOFF.md`.

Do not install alternate JavaScript runtimes globally or substitute a
system-installed Node.js for MAGI's bundled Node 24.

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
