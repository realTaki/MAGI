import { join } from 'node:path';
import { DmStore } from './dm-store.js';
import { DmHost, validateEnvelope } from './dm-host.js';
import { createDmRpc } from './dm-rpc.js';
import { dmToolDefinition } from './dm-tool.js';

export const dmInject = ['tools', 'profileContext', 'appReady', 'sessionController', 'sessionPersistence'];
function ownHost(ctx, hostPromise, defineTool, ready = async () => {}) {
  hostPromise.catch(() => {}); // Tool calls retain the actual initialization error.
  ctx.effect(() => ctx.tools.register(defineTool(dmToolDefinition(hostPromise))));
  let closed = false;
  ctx.effect(() => {
    const cancel = ctx.appReady.onReady(() => {
      void hostPromise.then(async host => {
        if (closed) return;
        await ready(host);
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
export function installParentDm(ctx, manager, defineTool) {
  const peers = new Map();
  let closed = false;
  const hostPromise = DmStore.open(ctx.profileContext.dir, ctx.profileContext.name).then(store => new DmHost(ctx, store, {
    workspace: join(ctx.profileContext.home, 'workspace'),
    transmit: envelope => route(envelope, store.identity().handle),
    contacts: async () => {
      const self = store.identity();
      const inventory = await manager.status();
      if (inventory.nodes.some(node => node.handle === self.handle)) throw new Error('DM parent handle collides with an EVA node.');
      return [{ ...self, status: 'online' }, ...inventory.nodes.map(node => ({ handle: node.handle,
        name: peers.get(node.handle)?.identity?.name ?? node.handle,
        status: peers.get(node.handle)?.identity && node.status === 'running' ? 'online' : 'offline' }))];
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
    peer.rpc = createDmRpc(channel, async (operation, payload) => {
      const host = await hostPromise;
      if (closed || peers.get(handle) !== peer) throw new Error('Stale DM process channel.');
      if (operation === 'hello') {
        if (!payload || payload.handle !== handle || typeof payload.name !== 'string' ||
            !payload.name.trim() || [...payload.name].length > 80 || /\p{Cc}/u.test(payload.name) ||
            handle === host.identity().handle) throw new Error('DM process identity mismatch.');
        peer.identity = { handle, name: payload.name };
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
    return close;
  };
  ctx.effect(() => () => { closed = true; for (const peer of [...peers.values()]) peer.close(); peers.clear(); });
  ownHost(ctx, hostPromise, defineTool);
  return { attach, host: hostPromise };
}

export function installChildDm(ctx, config, defineTool, channel = process) {
  if (!channel.connected || typeof channel.send !== 'function' || config.profile !== ctx.profileContext.name) {
    throw new Error('DM child requires its managed parent IPC channel and exact profile.');
  }
  let parent;
  let hostPromise;
  const rpc = createDmRpc(channel, async (operation, payload) => {
    if (operation !== 'deliver' || !parent) throw new Error('DM child is not ready for delivery.');
    const host = await hostPromise;
    // This channel is exclusively owned by the spawning parent. The broker has
    // already bound sibling sender identities to their own process channels.
    return host.receive(payload, payload?.from);
  });
  hostPromise = DmStore.open(ctx.profileContext.dir, ctx.profileContext.name).then(store => {
    // A profile may be renamed without changing its identity. Managed EVA node
    // handles, however, are permanent: never silently route an old identity under
    // a new node handle after copying/moving its profile state.
    if (store.identity().handle !== config.profile) throw new Error('DM identity does not match the fixed EVA handle.');
    return new DmHost(ctx, store, { workspace: ctx.profileContext.home,
      transmit: envelope => rpc.call('deliver', envelope), contacts: () => rpc.call('contacts', null),
      identityChanged: async identity => { if (parent) await rpc.call('hello', identity); } });
  });
  ctx.effect(() => () => rpc.close());
  ownHost(ctx, hostPromise, defineTool, async host => {
    const result = await rpc.call('hello', host.identity());
    if (!result?.parent || typeof result.parent.handle !== 'string' || result.parent.handle === host.identity().handle) {
      throw new Error('Invalid DM parent admission.');
    }
    parent = result.parent;
  });
  return { host: hostPromise, rpc };
}
