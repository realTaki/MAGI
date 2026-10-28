# Testing MAGI's DSH plugins

Use the MAGI-owned Node 24 and npm required by [AGENTS.md](../AGENTS.md), not a
system runtime. The existing repository-local toolchain path is retained only
for tooling; using it does not build or launch the old Electron application.

```sh
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js test
# Explicitly narrower portable gate (also used by Windows CI):
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run test:portable
```

`npm test` checks the two DSH package manifests, lock metadata, distributed
resources and all top-level plugin JavaScript syntax, then runs **every**
`apps/eva/test/*.test.js` and `apps/magi-shell/test/*.test.js` regression. No
TypeScript output, old EVA launcher, Electron UI build, installer or AppImage is
needed. Test discovery also includes its own wiring/bundle-boundary regression
and the authoritative root-lock/dependency-advisory policy. Literal relative
imports in active bundles must stay inside their bundle; official DSH services
are supplied by the Host. Neither active bundle depends on `apps/shell` or the
pre-DSH `packages/*` implementations.

## CI scope and meaningful coverage

- **Linux and macOS:** complete current plugin suite, including real synthetic
  child processes, concurrent start/owner locks, readiness rejection, stop and
  controller-crash descendant cleanup, filesystem symlink/migration refusal,
  credential/auth boundaries, durable identity and native Session navigation.
- **Windows:** all plugin syntax/resources plus the explicitly enumerated
  portable Client/auth/navigation/manifest tests in `scripts/test.mjs` and shared
  wiring/dependency policy. It does **not** run the full Host suite: the latter
  assumes POSIX private file modes, directory fsync and filesystem Unix sockets.
  Green portable checks do not establish Windows Host/runtime support.
- Both gates use synthetic temporary homes and controller/provider fixtures.
  They do not install plugins in a real DSH profile, make paid model calls, or
  certify the native DSH Desktop/GUI experience.

The retired launcher's atomic-state contract is now also tested against the
**public DSH main-Session initializer** in `main-session-atomic.test.js`, for both
EVA and MAGI Shell. Partial writes and failed rename must preserve the previous
complete mapping, remove only their private temporary file, and retry without
creating another identity. Existing persistence-barrier, competing Host,
credential/auth and descendant-cleanup assertions remain enabled in the complete
suite. A failed meaningful safety assertion is not deleted or silently skipped.

## Retained pre-DSH code, not current acceptance

```sh
# Compiles the pre-DSH packages and runs their remaining historical tests:
apps/shell/runtime/bin/node apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js run test:legacy
```

`test:legacy` preserves the old Shell/package assertions rather than deleting
them. It is not a passing replacement for current tests: fixtures that start
removed `apps/eva/eva.mjs` or import its deleted atomic helper are known obsolete.
The AppImage rollback tests test an intentionally retired distribution model.
No current DSH bundle invokes those entrypoints. `verify:legacy` retains the old
self-update build/UI gate only for historical source work; the default `verify`
now checks the DSH product. No CI workflow builds or publishes standalone desktop
packages, even via manual dispatch. Release/tag/organization-fork operations are
separate and are not part of source validation.

[DSH handoff](../DSH-HANDOFF.md) distinguishes fixture regression, a real native
DSH integration, installation, model/tool calls and user-visible GUI acceptance.
Report exact revision, test counts and environmental failures separately. For
example, a sandbox that refuses creation of a temporary Unix socket has not
executed the socket-refusal assertion; it must not be reported as a pass.
