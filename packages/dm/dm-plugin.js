import { isAbsolute, resolve } from 'node:path';
import { DmStore } from './dm-store.js';
import { DmHost, validateEnvelope } from './dm-host.js';
import { createDmRpc } from './dm-rpc.js';
import { dmToolDefinition } from './dm-tool.js';

export const dmInject = ['tools', 'profileContext', 'appReady', 'sessionController', 'sessionPersistence'];
function validIdentity(identity) {
  return identity && typeof identity.handle === 'string' && /^[a-z][a-z0-9-]{0,31}$/.test(identity.handle)
    && typeof identity.name === 'string' && identity.name.trim().length > 0
    && [...identity.name].length <= 80 && !/\p{Cc}/u.test(identity.name);
}
function ownHost(ctx, hostPromise, defineTool, ready = async () => {}) {
  hostPromise.catch(() => {}); // Tool calls retain the actual initialization error.
  ctx.effect(() => ctx.tools.register(defineTool(dmToolDefinition(hostPromise))));
  let closed = false;
  ctx.effect(() => {
    const cancel = ctx.appReady.onReady(() => {
      void hostPromise.then(async host => {
        if (closed) return;
        try { await ready(host); }
        catch {
          // Admission can race a broker-only reload. The durable local pump must
          // still start; outbound RPC remains subject to broker admission and
          // failures stay queued. Startup never creates a peer or wakes a Bot.
          if (!closed) console.warn('MAGI DM initial admission is unavailable; delivery remains queued until the broker admits this process.');
        }
        if (!closed) host.start();
      }).catch(() => { if (!closed) console.warn('MAGI DM is unavailable; inspect the local DM state before retrying.'); });
    });
    return async () => {
      closed = true; cancel();
      const host = await hostPromise.catch(() => undefined);
      await host?.close();
    };
  });
}

// The parent is the only broker. A child process object, never a model argument,
// determines the authenticated sender. This module opens no network endpoints.
export function installParentDm(ctx, transport, defineTool) {
  if (typeof transport.peers !== 'function' || typeof transport.workspace !== 'string' || !isAbsolute(transport.workspace) || resolve(transport.workspace) !== transport.workspace) {
    throw new Error('DM broker requires a canonical workspace and Host-owned peer inventory.');
  }
  const peers = new Map();
  let closed = false;
  const hostPromise = DmStore.open(ctx.profileContext.dir, ctx.profileContext.name).then(store => new DmHost(ctx, store, {
    workspace: transport.workspace,
    transmit: envelope => route(envelope, store.identity().handle),
    contacts: async () => {
      const self = store.identity();
      const inventory = await transport.peers();
      if (inventory.some(peer => peer.handle === self.handle)) throw new Error('DM broker handle collides with a managed peer.');
      return [{ ...self, status: 'online' }, ...inventory.map(peer => ({ handle: peer.handle,
        name: peers.get(peer.handle)?.identity?.name ?? peer.handle,
        status: peers.get(peer.handle)?.identity && peer.status === 'online' ? 'online' : 'offline' }))];
    },
  }));
  async function route(envelope, sender) {
    if (closed) throw new Error('DM broker is unloading.');
    validateEnvelope(envelope);
    if (envelope.from !== sender) throw new Error('DM sender forgery rejected.');
    const host = await hostPromise;
    if (envelope.to === host.identity().handle) return host.receive(envelope, sender);
    const peer = peers.get(envelope.to);
    if (!peer?.identity) throw new Error('DM peer is offline or has not loaded DM support.');
    return peer.rpc.call('deliver', envelope);
  }
  const attach = (channel, handle) => {
    if (closed || peers.has(handle)) throw new Error('Duplicate or closed DM process channel.');
    const peer = {};
    const admit = (identity, host) => {
      if (closed || peers.get(handle) !== peer) throw new Error('Stale DM process channel.');
      if (!validIdentity(identity) || identity.handle !== handle || handle === host.identity().handle) {
        throw new Error('DM process identity mismatch.');
      }
      peer.identity = { handle, name: identity.name };
    };
    peer.rpc = createDmRpc(channel, async (operation, payload) => {
      const host = await hostPromise;
      if (closed || peers.get(handle) !== peer) throw new Error('Stale DM process channel.');
      if (operation === 'hello') {
        admit(payload, host);
        return { parent: host.identity() };
      }
      if (!peer.identity) throw new Error('DM peer has not completed identity admission.');
      if (operation === 'contacts') return host.contacts();
      if (operation === 'deliver') return route(payload, handle);
      throw new Error('Unsupported DM broker operation.');
    });
    peers.set(handle, peer);
    const close = () => {
      if (peers.get(handle) === peer) peers.delete(handle);
      peer.rpc.close(); channel.off('exit', close); channel.off('disconnect', close);
    };
    peer.close = close;
    channel.once('exit', close); channel.once('disconnect', close);
    // Re-admit an already-loaded child after a broker-only reload. The channel
    // remains the authority; an old/unsupported child simply stays offline.
    void hostPromise.then(async host => {
      if (closed || peers.get(handle) !== peer) return;
      const identity = await peer.rpc.call('hello', { parent: host.identity() });
      // A concurrent child-initiated hello may carry a newer display name.
      if (!peer.identity) admit(identity, host);
    }).catch(() => {});
    return close;
  };
  ctx.effect(() => () => { closed = true; for (const peer of [...peers.values()]) peer.close(); peers.clear(); });
  ownHost(ctx, hostPromise, defineTool);
  return { attach, host: hostPromise };
}

export function installChildDm(ctx, config, defineTool, channel = process) {
  if (!channel?.connected || typeof channel.send !== 'function' || config.profile !== ctx.profileContext.name ||
      typeof config.workspace !== 'string' || !isAbsolute(config.workspace) || resolve(config.workspace) !== config.workspace) {
    throw new Error('DM child requires its managed parent IPC channel and exact profile.');
  }
  let parent;
  let hostPromise;
  const admitParent = (identity, host) => {
    if (!validIdentity(identity) || identity.handle === host.identity().handle ||
        (parent && parent.handle !== identity.handle)) throw new Error('Invalid DM parent admission.');
    parent = { handle: identity.handle, name: identity.name };
  };
  const rpc = createDmRpc(channel, async (operation, payload) => {
    if (operation === 'hello') {
      const host = await hostPromise;
      admitParent(payload?.parent, host);
      return host.identity();
    }
    if (operation !== 'deliver' || !parent) throw new Error('DM child is not ready for delivery.');
    const host = await hostPromise;
    // This channel is exclusively owned by the spawning parent. The broker has
    // already bound sibling sender identities to their own process channels.
    return host.receive(payload, payload?.from);
  });
  hostPromise = DmStore.open(ctx.profileContext.dir, ctx.profileContext.name).then(store => {
    // Managed handles are permanent: never silently route an old identity under
    // a new handle after copying/moving its profile state.
    if (store.identity().handle !== config.profile) throw new Error('DM identity does not match the fixed Bot handle.');
    return new DmHost(ctx, store, { workspace: config.workspace,
      transmit: envelope => rpc.call('deliver', envelope), contacts: () => rpc.call('contacts', null),
      identityChanged: async identity => { if (parent) await rpc.call('hello', identity); } });
  });
  ctx.effect(() => () => rpc.close());
  ownHost(ctx, hostPromise, defineTool, async host => {
    const result = await rpc.call('hello', host.identity());
    admitParent(result?.parent, host);
  });
  return { host: hostPromise, rpc };
}
