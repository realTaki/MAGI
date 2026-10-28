// Host-local DM domain. The transport owns peer authentication; native Sessions
// own all model execution. Only explicit send() puts content on the wire.
import { createHash, randomUUID } from 'node:crypto';
import { dmIdFor } from './dm-store.js';
import { USER_HANDLE, USER_SOURCE, normalizeDmHandle, userNotification, userSession } from './dm-user.js';

const handlePattern = /^[a-z][a-z0-9-]{0,31}$/;
const idPattern = /^[A-Za-z0-9-]{1,100}$/;
function validateMessage(value, localUser = false) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype ||
      value.version !== 1 || typeof value.id !== 'string' || !idPattern.test(value.id) ||
      !handlePattern.test(value.from ?? '') || !handlePattern.test(value.to ?? '') ||
      value.from === USER_HANDLE || (!localUser && value.to === USER_HANDLE) ||
      value.from === value.to || value.dmId !== dmIdFor(value.from, value.to) ||
      typeof value.text !== 'string' || !value.text.trim() || Buffer.byteLength(value.text) > 16384 ||
      !Number.isSafeInteger(value.createdAt) || value.createdAt < 0 ||
      (value.replyTo !== undefined && (typeof value.replyTo !== 'string' || !idPattern.test(value.replyTo))) ||
      Object.keys(value).some(key => !['version', 'id', 'from', 'to', 'dmId', 'text', 'createdAt', 'replyTo'].includes(key)) ||
      Buffer.byteLength(JSON.stringify(value)) > 40 * 1024) {
    throw new Error('Invalid DM envelope (text maximum 16 KiB).');
  }
  return value;
}
export function validateEnvelope(value) { return validateMessage(value); }
// Message IDs belong to the sender, not to the whole network. Separate peers
// may choose the same explicit retry ID without colliding in a local inbox.
export function inboxRecordId(peer, id) {
  if (typeof peer !== 'string' || !handlePattern.test(peer) || typeof id !== 'string' || !idPattern.test(id)) throw new Error('Invalid DM inbox identity.');
  return createHash('sha256').update(JSON.stringify([peer, id])).digest('hex');
}
function sameMessage(a, b) {
  return ['version', 'id', 'from', 'to', 'dmId', 'text', 'createdAt', 'replyTo'].every(key => a[key] === b[key]);
}
// Read the committed raw log, not the model surface (which can be compacted).
// Insertion is acceptance, claim is not model delivery, cancellation is final.
export function nativeAdmission(events, envelope, kind = 'eva-dm') {
  const matches = message => message?.source?.kind === kind &&
    message.source.peer === envelope.from && message.source.messageId === envelope.id;
  const lists = { 'next-turn': [], 'next-step': [] };
  let state = 'absent', committed = false;
  for (const event of events) {
    if (event.type === 'user/message' && matches(event.data)) committed = true;
    if (event.type !== 'agent/inbox/spliced') continue;
    const data = event.data, list = lists[data.target];
    if (!list || !Number.isInteger(data.start) || !Array.isArray(data.inserted)) throw new Error('Invalid native inbox log.');
    const removed = list.splice(data.start, data.removedCount ?? 0, ...data.inserted);
    if (removed.some(matches)) state = data.outcome === 'canceled' ? 'canceled' : 'claimed';
    if (data.inserted.some(matches)) state = 'pending';
  }
  return committed ? 'committed' : state;
}
function nativeMessage(envelope) {
  const source = Object.freeze({ kind: 'eva-dm', peer: envelope.from, messageId: envelope.id, dmId: envelope.dmId });
  const block = Object.freeze({ type: 'text', text: `[MAGI-DM] from @${envelope.from}:\n\n${envelope.text}` });
  return Object.freeze({ id: `dm-${envelope.id}`, role: 'user', source, content: Object.freeze([block]) });
}

export class DmHost {
  constructor(ctx, store, { workspace, transmit, contacts, identityChanged, retryMs = 5000 } = {}) {
    this.ctx = ctx; this.store = store; this.workspace = workspace;
    if (store.identity().handle === USER_HANDLE) throw new Error('DM handle user is reserved for the local human; manual identity recovery is required.');
    this.transmit = transmit; this.peerContacts = contacts; this.identityChanged = identityChanged; this.retryMs = retryMs;
    this.preparing = new Map(); this.sending = new Map(); this.receiving = new Map(); this.closed = false;
    this.recovery = new Set(); this.loadRecovery = true;
  }
  identity() { return this.store.identity(); }
  async contacts() {
    const peers = await this.peerContacts();
    if (peers.some(peer => peer.handle === USER_HANDLE && peer.kind !== 'user')) throw new Error('A Bot handle collides with reserved @user.');
    const main = this.ctx.get?.('magiMainSession')?.current();
    return [...peers.filter(peer => peer.handle !== USER_HANDLE),
      { handle: USER_HANDLE, kind: 'user', name: 'User', status: main ? 'online' : 'offline' }];
  }
  async setName(name) {
    this.check();
    const identity = await this.store.setName(name);
    await this.identityChanged?.(identity);
    return identity;
  }
  check() { if (this.closed) throw new Error('DM Host is unloading.'); }
  async pair(peer) {
    this.check();
    const pair = await this.store.pair(peer, async sessionId => {
      const created = await this.ctx.sessionController.create({ sessionId, cwd: this.workspace });
      if (created?.sessionId !== sessionId) throw new Error('DM native Session identity mismatch.');
      await this.ctx.sessionPersistence.flush();
    });
    // A missing/deleted/corrupt Session never licenses creation of a replacement.
    const snapshot = await this.ctx.sessionController.inspect(pair.sessionId);
    const meta = snapshot.meta;
    if (meta?.id !== pair.sessionId || meta.cwd !== this.workspace || meta.parentSession !== undefined ||
        meta.origin !== undefined || meta.isSeeded !== false) throw new Error('DM native Session is missing or mismatched.');
    return { ...pair, events: snapshot.events };
  }
  send(args) {
    this.check();
    args = { ...args, to: normalizeDmHandle(args.to) };
    const id = args.messageId ?? randomUUID();
    if (typeof id !== 'string' || !idPattern.test(id)) throw new Error('Invalid DM message ID.');
    const active = this.preparing.get(id);
    if (active) {
      if (['to', 'text', 'replyTo', ...(args.to === USER_HANDLE ? ['senderSessionId'] : [])]
        .some(key => active.args[key] !== args[key])) throw new Error('Conflicting concurrent DM ID.');
      return active.promise;
    }
    const promise = this.prepare({ ...args, messageId: id }).finally(() => this.preparing.delete(id));
    this.preparing.set(id, { args: { ...args }, promise });
    return promise;
  }
  async prepare({ to, text, messageId, replyTo, senderSessionId }) {
    this.check();
    const from = this.identity().handle;
    const id = messageId ?? randomUUID();
    const prior = await this.store.getRecord('outbox', id);
    const localUser = to === USER_HANDLE;
    if (localUser && replyTo !== undefined) throw new Error('@user notifications do not use reply_to.');
    const envelope = validateMessage({ version: 1, id, from, to, dmId: dmIdFor(from, to),
      text, createdAt: prior?.createdAt ?? Date.now(), ...(replyTo === undefined ? {} : { replyTo }) }, localUser);
    if (prior && (!sameMessage(prior, envelope) || (localUser && prior.senderSessionId !== senderSessionId))) {
      throw new Error('DM message ID already belongs to different content or origin Session.');
    }
    if (!prior) {
      if (!localUser) {
        const peers = await this.contacts();
        if (!peers.some(peer => peer.handle === to && peer.kind !== 'user')) throw new Error('Unknown DM handle; use contacts first.');
      }
      if ((await this.store.records('outbox')).filter(item => item.status !== 'accepted').length + this.preparing.size > 128) {
        throw new Error('DM outbox is full (128 pending messages).');
      }
      if (replyTo !== undefined) {
        const original = await this.store.getRecord('inbox', inboxRecordId(to, replyTo));
        if (!original || original.from !== to) throw new Error('reply_to must identify an incoming DM from this peer.');
      }
      if (localUser && (typeof senderSessionId !== 'string' || !senderSessionId || senderSessionId.length > 200)) {
        throw new Error('@user requires the calling Agent Session identity.');
      }
      const pair = localUser ? await userSession(this.ctx, this.workspace) : await this.pair(to);
      if (localUser && senderSessionId === pair.sessionId) throw new Error('You are already in the main conversation; reply to the user directly instead of sending @user.');
      this.check();
      await this.store.putRecord('outbox', id, { ...envelope, sessionId: pair.sessionId,
        ...(localUser ? { senderSessionId } : {}), status: 'pending', attempts: 0 });
    }
    return this.deliver(id);
  }
  deliver(id) {
    this.check();
    if (this.sending.has(id)) return this.sending.get(id);
    const pending = this.deliverOnce(id).finally(() => this.sending.delete(id));
    this.sending.set(id, pending); return pending;
  }
  async deliverOnce(id) {
    const record = await this.store.getRecord('outbox', id);
    if (!record) throw new Error('Unknown outgoing DM.');
    if (record.status === 'accepted') return record;
    const envelope = Object.fromEntries(Object.entries(record).filter(([key]) =>
      ['version', 'id', 'from', 'to', 'dmId', 'text', 'createdAt', 'replyTo'].includes(key)));
    validateMessage(envelope, record.to === USER_HANDLE);
    let next;
    try {
      this.check();
      let ack;
      if (record.to === USER_HANDLE) {
        await this.notifyUser(record);
        ack = { id, dmId: record.dmId, status: 'accepted' };
      } else {
        await this.recordOutgoing(record);
        this.check();
        ack = await this.transmit(envelope);
      }
      if (ack?.id !== id || ack.dmId !== record.dmId || ack.status !== 'accepted') throw new Error('Invalid DM acceptance.');
      next = { ...record, status: 'accepted', attempts: record.attempts + 1, acceptedAt: Date.now() };
      delete next.lastError;
    } catch {
      // No transport internals, credential-bearing URLs, peer exception strings,
      // or model/tool output are copied into the durable outgoing message.
      next = { ...record, status: 'pending', attempts: record.attempts + 1, lastError: 'Peer unavailable or delivery not yet confirmed.' };
    }
    await this.store.putRecord('outbox', id, next);
    return next;
  }
  async notifyUser(record) {
    const main = await userSession(this.ctx, this.workspace);
    if (main.sessionId !== record.sessionId || typeof record.senderSessionId !== 'string' || !record.senderSessionId ||
        record.senderSessionId === main.sessionId) throw new Error('Invalid @user origin or pinned main Session.');
    if (nativeAdmission(main.events, record, USER_SOURCE) === 'absent') {
      const resolved = await this.ctx.sessionController.resolveAgent(main.sessionId);
      if (resolved.error || resolved.agent?.id !== main.sessionId) throw new Error('Main Agent is unavailable.');
      this.check();
      const current = this.ctx.get?.('magiMainSession')?.current();
      if (current?.sessionId !== main.sessionId || current.workspace !== this.workspace) throw new Error('Main Session is no longer ready.');
      // A native inbox splice is the durable notification record. The custom
      // Chat row displays it immediately. inject never wakes an idle main Agent.
      // Later model context is explicitly marked non-human and already shown.
      resolved.agent.inject(userNotification(record));
    }
    await this.ctx.sessionPersistence.flush();
  }
  async recordOutgoing(record) {
    const pair = await this.pair(record.to);
    const matches = message => message?.source?.kind === 'eva-dm-outgoing' &&
      message.source.peer === record.to && message.source.messageId === record.id;
    if (pair.events.some(event => event.type === 'user/message' ? matches(event.data) :
      event.type === 'agent/inbox/spliced' && event.data.inserted.some(matches))) {
      // inspect may include an earlier insertion whose flush failed in-process.
      await this.ctx.sessionPersistence.flush();
      return;
    }
    const resolved = await this.ctx.sessionController.resolveAgent(pair.sessionId);
    if (resolved.error || resolved.agent?.id !== pair.sessionId) throw new Error('DM Agent is unavailable.');
    this.check();
    resolved.agent.inject(Object.freeze({ id: `dm-out-${record.id}`, role: 'user',
      source: Object.freeze({ kind: 'eva-dm-outgoing', peer: record.to, messageId: record.id, dmId: record.dmId }),
      content: Object.freeze([Object.freeze({ type: 'text',
        text: `Explicit outgoing DM to @${record.to} (message_id: ${record.id}${record.replyTo ? `; reply_to: ${record.replyTo}` : ''})\n\n${record.text}` })]) }));
    await this.ctx.sessionPersistence.flush();
  }
  receive(envelope, authenticatedSender) {
    this.check(); validateEnvelope(envelope);
    if (authenticatedSender !== envelope.from || envelope.to !== this.identity().handle) {
      throw new Error('DM transport sender/recipient mismatch.');
    }
    const key = inboxRecordId(envelope.from, envelope.id);
    const active = this.receiving.get(key);
    if (active) {
      if (!sameMessage(active.envelope, envelope)) throw new Error('Conflicting concurrent DM ID.');
      return active.promise;
    }
    const promise = this.receiveOnce(envelope).finally(() => this.receiving.delete(key));
    this.receiving.set(key, { envelope, promise }); return promise;
  }
  async receiveOnce(envelope) {
    const key = inboxRecordId(envelope.from, envelope.id);
    let record = await this.store.getRecord('inbox', key);
    if (record && !sameMessage(record, envelope)) throw new Error('DM ID was replayed with different content.');
    const ack = { id: envelope.id, dmId: envelope.dmId, status: 'accepted' };
    if (record?.status === 'accepted') return ack; // No Agent activation for receipt/replay.
    if (!record) {
      if ((await this.store.records('inbox')).filter(item => item.status !== 'accepted').length + this.receiving.size > 128) throw new Error('DM inbox is full.');
      record = { ...envelope, status: 'received' };
      await this.store.putRecord('inbox', key, record);
    }
    const pair = await this.pair(envelope.from);
    this.check();
    const state = nativeAdmission(pair.events, envelope);
    if (state === 'absent') {
      const resolved = await this.ctx.sessionController.resolveAgent(pair.sessionId);
      if (resolved.error || resolved.agent?.id !== pair.sessionId) throw new Error('DM Agent is unavailable.');
      this.check();
      resolved.agent.followup(nativeMessage(envelope));
    } else if (state === 'pending') {
      record.attention = 'native-inbox-pending-after-restart';
    } else if (state === 'claimed') {
      record.attention = 'native-input-consumed-before-model-commit';
    }
    // Durable native admission precedes ack. On a crash between these two writes,
    // recovery finds the original inbox splice, even after consumption/compaction.
    await this.ctx.sessionPersistence.flush();
    const accepted = { ...record, sessionId: pair.sessionId, status: 'accepted' };
    delete accepted.recovery;
    await this.store.putRecord('inbox', key, accepted);
    return ack;
  }
  async recover(record) {
    const pair = await this.pair(record.from);
    const state = nativeAdmission(pair.events, record);
    // Native resume has no public wake-only operation. Do not manufacture a
    // model turn, cancel/reinsert queued input, or undo a user's stop. A later
    // substantive native input naturally wakes a still-pending native inbox.
    const next = { ...record };
    delete next.attention;
    if (state === 'pending') next.attention = 'native-inbox-pending-after-restart';
    if (state === 'claimed') next.attention = 'native-input-consumed-before-model-commit';
    if (state === 'absent') next.attention = 'native-admission-evidence-missing';
    await this.store.putRecord('inbox', inboxRecordId(record.from, record.id), next);
    return true;
  }
  async history(peer, limit = 20) {
    peer = normalizeDmHandle(peer);
    dmIdFor(this.identity().handle, peer);
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error('DM history limit must be 1–50.');
    const [inbox, outbox] = await Promise.all([this.store.records('inbox'), this.store.records('outbox')]);
    return [...inbox.filter(item => item.from === peer).map(item => ({ ...item, direction: 'incoming' })),
      ...outbox.filter(item => item.to === peer).map(item => ({ ...item, direction: 'outgoing' }))]
      .sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id)).slice(-limit);
  }
  pump() {
    if (this.pumpTask) return this.pumpTask;
    const task = this.pumpOnce().finally(() => { this.pumpTask = undefined; });
    this.pumpTask = task;
    return task;
  }
  async pumpOnce() {
    if (this.closed || this.pumping) return;
    this.pumping = true;
    try {
      const inbox = await this.store.records('inbox');
      if (this.loadRecovery) {
        for (const record of inbox) if (record.status === 'accepted') this.recovery.add(inboxRecordId(record.from, record.id));
        this.loadRecovery = false;
      }
      for (const id of [...this.recovery]) {
        if (this.closed) break;
        const record = await this.store.getRecord('inbox', id);
        try {
          if (await this.recover(record)) this.recovery.delete(id);
        } catch { /* Fail closed; retain the exact native Session and recovery intent. */ }
      }
      for (const record of inbox) {
        if (this.closed) break;
        if (record.status === 'received') {
          const envelope = Object.fromEntries(Object.entries(record).filter(([key]) =>
            ['version', 'id', 'from', 'to', 'dmId', 'text', 'createdAt', 'replyTo'].includes(key)));
          try { await this.receive(envelope, envelope.from); } catch { /* Retain durable recovery intent. */ }
        }
      }
      for (const record of await this.store.records('outbox')) {
        if (this.closed) break;
        if (record.status === 'pending') await this.deliver(record.id);
      }
    } finally { this.pumping = false; }
  }
  start() {
    this.check(); if (this.timer) return;
    void this.pump().catch(() => {});
    this.timer = setInterval(() => { void this.pump().catch(() => {}); }, this.retryMs);
    this.timer.unref();
  }
  async close() {
    this.closed = true; clearInterval(this.timer);
    await Promise.allSettled([this.pumpTask, ...[...this.preparing.values()].map(item => item.promise),
      ...this.sending.values(), ...[...this.receiving.values()].map(item => item.promise)]);
  }
}
