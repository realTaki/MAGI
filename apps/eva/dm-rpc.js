// Transport only: the owner of the process/channel must authorize operations.
// Envelope fields are not credentials, and requests are never retried.
import { randomUUID } from 'node:crypto';

const TYPE = 'eva-dm-rpc';
const LIMIT = 48 * 1024;
const OPERATIONS = new Set(['deliver', 'contacts', 'hello']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const own = (object, key) => Object.hasOwn(object, key);

// Do not invoke accessors/toJSON or silently drop non-JSON data. A snapshot also
// prevents a caller mutating a queued in-memory message after validation.
function snapshot(value) {
  const seen = new Set();
  let budget = LIMIT;
  function visit(item, depth) {
    if (--budget < 0 || depth > 128) throw new Error('DM RPC envelope is too large or deeply nested.');
    if (item === null || typeof item === 'boolean') return;
    if (typeof item === 'string') {
      budget -= Buffer.byteLength(item);
      if (budget < 0) throw new Error('DM RPC envelope exceeds 48KiB.');
      return;
    }
    if (typeof item === 'number' && Number.isFinite(item)) return;
    if (typeof item !== 'object' || item === null || seen.has(item)) throw new Error('DM RPC requires plain JSON values.');
    const array = Array.isArray(item);
    if (![(array ? Array.prototype : Object.prototype), null].includes(Object.getPrototypeOf(item))) throw new Error('DM RPC requires plain JSON values.');
    seen.add(item);
    const keys = Reflect.ownKeys(item);
    if (array && keys.length !== item.length + 1) throw new Error('DM RPC requires dense JSON arrays.');
    for (const key of keys) {
      if (array && key === 'length') continue;
      const descriptor = Object.getOwnPropertyDescriptor(item, key);
      if (typeof key !== 'string' || !descriptor.enumerable || !own(descriptor, 'value') ||
          (array && (!/^(0|[1-9][0-9]*)$/.test(key) || Number(key) >= item.length))) {
        throw new Error('DM RPC requires plain JSON values.');
      }
      if (!array) budget -= Buffer.byteLength(key);
      visit(descriptor.value, depth + 1);
    }
    seen.delete(item);
  }
  visit(value, 0);
  const text = JSON.stringify(value);
  if (Buffer.byteLength(text) > LIMIT) throw new Error('DM RPC envelope exceeds 48KiB.');
  return JSON.parse(text);
}

function validEnvelope(message) {
  if (message === null || typeof message !== 'object' || Array.isArray(message) ||
      message.type !== TYPE || message.version !== 1 || typeof message.id !== 'string' || !UUID.test(message.id)) return false;
  let fields;
  if (message.kind === 'request') {
    if (!OPERATIONS.has(message.operation) || !own(message, 'payload')) return false;
    fields = ['type', 'version', 'kind', 'id', 'operation', 'payload'];
  } else if (message.kind === 'response') {
    if (own(message, 'value') === own(message, 'error')) return false;
    if (own(message, 'error') && (typeof message.error !== 'string' || !message.error.length || message.error.length > 512)) return false;
    fields = ['type', 'version', 'kind', 'id', own(message, 'error') ? 'error' : 'value'];
  } else return false;
  return Object.keys(message).length === fields.length && fields.every(key => own(message, key));
}

function errorText(error) {
  try {
    const text = typeof error === 'string' ? error : error?.message;
    return typeof text === 'string' && text.length ? text.slice(0, 512) : 'DM RPC operation failed.';
  } catch { return 'DM RPC operation failed.'; }
}

/** Generic JSON RPC over a process-like EventEmitter with send(message, cb).
 * maxPending bounds outbound calls; inbound handler concurrency is always 32.
 * Undefined payloads/results become null. close() is idempotent and does not
 * disconnect the underlying channel. Cancellation does not retry/undo a handler.
 */
export function createDmRpc(channel, handler, { timeoutMs = 10000, maxPending = 32 } = {}) {
  if (!channel || typeof channel.on !== 'function' || typeof channel.removeListener !== 'function' || typeof channel.send !== 'function') {
    throw new TypeError('DM RPC requires an IPC channel.');
  }
  if (typeof handler !== 'function') throw new TypeError('DM RPC requires a handler.');
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 2147483647 ||
      !Number.isSafeInteger(maxPending) || maxPending < 1) throw new TypeError('Invalid DM RPC limits.');
  const pending = new Map();
  const inflight = new Set();
  let closed = false;

  function finish(id, error, value) {
    const entry = pending.get(id);
    if (!entry) return;
    pending.delete(id);
    clearTimeout(entry.timer);
    if (error) entry.reject(error); else entry.resolve(value);
  }

  function close() {
    if (closed) return;
    closed = true;
    channel.removeListener('message', onMessage);
    channel.removeListener('disconnect', close);
    channel.removeListener('close', close);
    for (const id of pending.keys()) finish(id, new Error('DM RPC closed or disconnected.'));
    inflight.clear();
  }

  function send(message, onFailure = () => {}) {
    if (closed) return onFailure(new Error('DM RPC is closed.'));
    if (channel.connected === false) {
      close();
      return onFailure(new Error('DM RPC disconnected.'));
    }
    try {
      // false means IPC backpressure, not a failed send. The callback owns errors.
      channel.send(message, error => {
        if (error) onFailure(new Error(`DM RPC send failed: ${errorText(error)}`));
      });
    } catch (error) { onFailure(new Error(`DM RPC send failed: ${errorText(error)}`)); }
  }

  function respond(id, value, error) {
    if (closed) return;
    const base = { type: TYPE, version: 1, kind: 'response', id };
    let response;
    try {
      response = snapshot(error ? { ...base, error: errorText(error) } : { ...base, value: value === undefined ? null : value });
    } catch {
      response = { ...base, error: 'DM RPC response is not valid JSON or exceeds 48KiB.' };
    }
    send(response);
  }

  function onMessage(raw) {
    if (closed) return;
    let message;
    try {
      message = snapshot(raw);
      if (!validEnvelope(message)) return;
    } catch { return; } // Foreign/malformed messages must not crash or elicit loops.
    if (message.kind === 'response') {
      finish(message.id, own(message, 'error') ? new Error(message.error) : null, message.value);
      return;
    }
    if (inflight.has(message.id)) return;
    if (inflight.size >= 32) {
      respond(message.id, null, new Error('DM RPC inbound overload (32 concurrent requests).'));
      return;
    }
    inflight.add(message.id);
    Promise.resolve().then(() => {
      if (closed) return;
      return handler(message.operation, message.payload);
    }).then(value => {
      inflight.delete(message.id);
      if (!closed) respond(message.id, value);
    }, error => {
      inflight.delete(message.id);
      if (!closed) respond(message.id, null, new Error(errorText(error)));
    });
  }

  function call(operation, payload = null) {
    if (closed) return Promise.reject(new Error('DM RPC is closed.'));
    if (channel.connected === false) {
      close();
      return Promise.reject(new Error('DM RPC disconnected.'));
    }
    if (!OPERATIONS.has(operation)) return Promise.reject(new Error('Invalid DM RPC operation.'));
    if (pending.size >= maxPending) return Promise.reject(new Error('DM RPC outbound overload (maxPending reached).'));
    const id = randomUUID();
    let request;
    try { request = snapshot({ type: TYPE, version: 1, kind: 'request', id, operation, payload }); }
    catch (error) { return Promise.reject(new Error(errorText(error))); }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => finish(id, new Error('DM RPC request timed out.')), timeoutMs);
      timer.unref();
      pending.set(id, { resolve, reject, timer });
      send(request, error => finish(id, error));
    });
  }

  channel.on('message', onMessage);
  channel.on('disconnect', close);
  channel.on('close', close);
  if (channel.connected === false) close();
  return { call, close };
}
