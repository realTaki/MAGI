# @magi/dm

Independent, directly executed Cordis Host/Client plugin. It owns the `dm` tool,
private profile storage, durable acknowledgements, deduplication/retry, Bot-pair
Sessions and the plain Agent-side `@user` notification renderer. It imports no
EVA or legacy MAGI package. It has no network listener and starts no processes.

## Composition and distribution

This is a reusable **plugin**, not a self-starting bundle: the integrating bundle
mounts its package root and supplies `magiDmTransport`. EVA declares it as an
actual dependency and mounts `@magi/dm` as a separate Loader root. EVA's portable
archive bundles this package; do not install DM as a separate profile bundle or
copy its implementation into EVA/Desktop. Other Host products can use the same
root plugin with their own adapter and main-Session provider.

Use `node scripts/pack-dsh.mjs --app eva --out dist` with the MAGI-owned Node from
the repository root. This stages real package files instead of relying on npm to
bundle a hoisted workspace symlink. Ordinary `npm pack apps/eva` can silently omit
DM. Linked development installs require workspace dependencies installed from the
root lockfile first (`npm ci --workspace @local/eva --ignore-scripts` using the
MAGI-owned npm). Native directory linking does not install those dependencies.

## Host adapter contract

The caller provides the Host-local Cordis service `magiDmTransport` before this
plugin can activate. Values are trusted integration data, never model arguments:

- Broker: `{ kind: 'broker', workspace, peers, bindBroker? }`. `workspace` is an
  absolute canonical Session working directory. Async `peers()` returns
  `[{ handle, status: 'online' | 'offline' }]`; authenticated channel admission,
  not inventory alone, determines online contacts. Optional `bindBroker(attach)`
  registers the process-attachment callback and returns an unsubscribe function.
  It must reattach existing owned processes after plugin reload without starting
  them. `attach(processChannel, fixedHandle)` returns a detach function.
- Child: `{ kind: 'child', workspace, handle, channel }`. The managed profile name
  and stored identity must match the fixed handle. `channel` is the actual
  connected IPC channel owned by the spawning parent, not a fabricated address.

The plugin provides `magiDm` with `{ host: Promise<DmHost>, attach }` on the broker
or `{ host, rpc }` on a child. Integrations must never pass arbitrary model-supplied
sender identities into `attach`. Cordis unload removes tools, timers, services and
RPC listeners. Broker reload can re-admit attached, still-running children.

For `@user`, the caller separately provides optional `magiMainSession.current()`
returning `{ sessionId, workspace }` only while the durable fixed main Session is
available. It must return `undefined` after unload. There is no Session creation,
replacement or cross-Bot fallback; a send from main itself is rejected.

## Compatibility and acceptance

Existing profile `magi-dm` records and wire/source IDs (`eva-dm-rpc`, `eva-dm`,
`eva-user-dm`, `magi-user-dm`) deliberately remain unchanged. Moving package
ownership must not rewrite identities, histories, outbox state or native events.
`@user` is local-only, has no DM header, does not wake an Agent, and preserves its
clearly marked non-authoritative model context. Accepted means durable native
acceptance, not execution/completion/read receipt.

The package test directory contains storage/security, delivery/recovery, IPC,
plugin lifecycle, native Session restore and Client projection/render regressions.
The root acceptance runner discovers them directly. Live Client inspection may
require a connected page; offline native metadata tests are not visual proof.
