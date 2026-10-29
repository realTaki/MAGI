# MAGI Repository Instructions

These instructions apply to the entire repository.

## Use MAGI-owned runtimes

MAGI must be developed, tested, built, and run with the runtimes owned by the
project or bundled with the MAGI desktop application. Do not silently fall back
to a system-installed Node.js.

Preferred runtime order:

1. For repository JavaScript tooling, use MAGI's bundled Node.js. In a prepared
   source build this is `runtime/bin/node`; in the installed macOS app
   it is `/Applications/MAGI.app/Contents/Resources/runtime/bin/node`.
2. On Windows or Linux, use the equivalent binaries inside the MAGI packaged
   runtime. Do not replace them with globally installed tools merely because
   the platform-specific path differs.

Invoke runtime binaries by explicit path whenever practical. A command-scoped
`PATH` is acceptable when a tool requires it, but it must contain only the
paths needed for that command and must not be persisted.

Examples:

```bash
runtime/bin/node --test packages/dm/test/*.test.js
/Applications/MAGI.app/Contents/Resources/runtime/bin/node --test scripts/test/*.test.mjs
runtime/bin/node runtime/npm/node_modules/npm/bin/npm-cli.js test
```

The current product is the directly executed DSH Host/Client bundles in
`apps/eva` and `apps/magi-shell`, plus the independent `@magi/dm` plugin in
[packages/dm](<packages/dm/README.md>) and standalone `@magi/memory` bundle in
[packages/memory](<packages/memory/README.md>) consumed by EVA, and shared
[@magi/bot-mode](<packages/bot-mode/README.md>) consumed by Shell/EVA children; none
needs a TypeScript or Electron build. EVA mounts DM, memory and child bot-mode as
separate Loader roots, not copied source. Shell bundles the parent bot-mode root.
EVA retains process management and startup prerequisites; bot-mode owns fixed-main
identity. Agent presets remain unchanged in this first extraction increment.
Use `npm run pack:dsh -- --app eva --out dist` with the MAGI-owned runtime for a
portable archive: plain npm packing can omit a hoisted bundled workspace symlink.
A source-linked EVA requires its workspace dependencies installed from the root
lockfile before native installation; do not run dependency installation in a profile.
Root `npm test` checks their source syntax/manifests and runs every DSH plugin
regression plus shared dependency-policy checks. Linux/macOS run this complete
suite. `npm run test:portable` is the explicitly narrower Windows Client/auth/
bundle-contract gate; it does not certify Host lifecycle or POSIX persistence.

The old Electron Shell, provider module, BUS launcher, centralized BUS skills,
BUS MCP, channel-a2a and channel-tasks have been deleted. MCP belongs to native
DSH; do not delete or implicitly migrate existing MCP server data or credentials.
Skills belong to their native plugins, not a restored BUS worker; deleting old
source must not delete users'
workspace skills or native DSH skill assets. Timed instructions use native DSH
Schedule; do not restore the old task
worker or implicitly migrate old task databases. Local
Bot-to-Bot DM and Bot-to-user notifications belong to the independent DM plugin;
remote DM is out of scope. Other pre-DSH
packages remain archived source, excluded from the explicit five-workspace
install graph. They are not supported build/runtime dependencies; their dangling
legacy launcher/provider contracts are historical, not migration TODOs. Root
TypeScript build, database-generation and legacy acceptance scripts are retired.
Do not restore the old EVA launcher or provider implementation to make archived
code compile. Shared runtime provisioning lives in `scripts/prepare-runtime.mjs`;
Node is pinned at the root and the prepared runtime lives in `runtime/`.
There is no standalone Electron/AppImage/installer/release pipeline. See
`DSH-HANDOFF.md`.

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
