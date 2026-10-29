// Pure Node storage. The caller owns native Session creation and its durability
// barrier. Use under the profile's existing single-process owner lock.
import { constants } from 'node:fs';
import { lstat, realpath, mkdir, open, link, rename, unlink, readdir } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

const handlePattern = /^[a-z][a-z0-9-]{0,31}$/;
const idPattern = /^[a-zA-Z0-9-]{1,100}$/;
const sessionPattern = /^session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const limit = 128 * 1024;
const clone = value => JSON.parse(JSON.stringify(value));
function validHandle(value) {
  if (typeof value !== 'string' || !handlePattern.test(value)) throw new Error('Invalid DM handle.');
  return value;
}
function nameValue(value) {
  if (typeof value !== 'string' || /[\p{Cc}]/u.test(value)) throw new Error('Invalid DM name: use 1..80 characters without control characters.');
  const name = value.trim();
  if (!name.length || [...name].length > 80) throw new Error('Invalid DM name: use 1..80 characters without control characters.');
  return name;
}
export function dmIdFor(a, b) {
  validHandle(a); validHandle(b);
  if (a === b) throw new Error('Cannot pair DM identity with itself.');
  return createHash('sha256').update(JSON.stringify([a, b].sort())).digest('hex');
}
function plain(value) {
  return value !== null && typeof value === 'object' &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
function jsonValue(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean' ||
      (typeof value === 'number' && Number.isFinite(value))) return;
  if ((!plain(value) && !Array.isArray(value)) || seen.has(value)) throw new Error('DM records must contain plain JSON values.');
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) {
    if (Array.isArray(value) && key === 'length') continue;
    if (Array.isArray(value) && (typeof key !== 'string' || !/^(0|[1-9][0-9]*)$/.test(key) || Number(key) >= value.length)) {
      throw new Error('DM records must contain plain JSON arrays without extra properties.');
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (typeof key !== 'string' || !descriptor.enumerable || !('value' in descriptor)) throw new Error('DM records must contain plain JSON values.');
    jsonValue(descriptor.value, seen);
  }
  if (Array.isArray(value) && Object.keys(value).length !== value.length) throw new Error('DM records must not contain sparse arrays.');
  seen.delete(value);
}
function encode(value) {
  if (!plain(value)) throw new Error('DM record must be a plain JSON object.');
  jsonValue(value);
  const text = `${JSON.stringify(value)}\n`;
  if (Buffer.byteLength(text) >= limit) throw new Error('DM record must be smaller than 128KiB.');
  return text;
}
function privateStat(stat) {
  return (stat.mode & 0o077) === 0 && (!process.getuid || stat.uid === process.getuid());
}
async function directory(path, privateDir = true) {
  if (typeof path !== 'string' || !isAbsolute(path) || resolve(path) !== path) throw new Error('DM directory must be absolute and canonical.');
  const stat = await lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(path) !== path || (privateDir && !privateStat(stat))) {
    throw new Error('Unsafe DM directory: symlink, permissions, or ownership.');
  }
}
async function syncDirectory(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try { await file.sync(); } finally { await file.close(); }
}
async function ensureDirectory(parent, path) {
  await directory(parent, false);
  try { await mkdir(path, { mode: 0o700 }); await syncDirectory(parent); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  await directory(path);
}
async function readObject(path) {
  let stat;
  try { stat = await lstat(path); }
  catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || !privateStat(stat)) throw new Error('Unsafe DM state file (including symlinks).');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const opened = await file.stat();
    if (opened.ino !== stat.ino || opened.dev !== stat.dev || opened.size >= limit) throw new Error('DM state changed or is oversized.');
    const bytes = await file.readFile();
    if (bytes.length >= limit) throw new Error('DM state is oversized.');
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    encode(value);
    return value;
  } catch (cause) { throw new Error('Refusing corrupt DM state.', { cause }); }
  finally { await file.close(); }
}
async function publish(dir, filename, value, initial) {
  await directory(dir);
  const target = join(dir, filename);
  // Check even on replacement: never silently overwrite an unsafe/corrupt file.
  const previous = await readObject(target);
  if (initial && previous !== undefined) throw new Error('DM state already exists; refusing replacement.');
  const text = encode(value);
  const temporary = join(dir, `.dm-${randomUUID()}.tmp`);
  const file = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    try { await file.writeFile(text); await file.sync(); } finally { await file.close(); }
    if (initial) await link(temporary, target);
    else await rename(temporary, target);
    await syncDirectory(dir);
  } finally {
    if (resolve(temporary) !== temporary || !temporary.startsWith(`${dir}/.dm-`)) throw new Error('Unsafe DM temporary path.');
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
    await syncDirectory(dir);
  }
}
function validateIdentity(value) {
  if (!plain(value) || Object.keys(value).sort().join(',') !== 'handle,name') throw new Error('Corrupt DM identity.');
  validHandle(value.handle);
  if (value.handle === 'user') throw new Error('DM handle user is reserved for the local human; manual identity recovery is required.');
  if (nameValue(value.name) !== value.name) throw new Error('Corrupt DM identity name.');
  return value;
}

export class DmStore {
  #root; #identity; #tail = Promise.resolve();
  constructor(root, identity) { this.#root = root; this.#identity = identity; }
  /** profileDir must already exist, be absolute/canonical, and contain no symlinks. */
  static async open(profileDir, initialHandle) {
    validHandle(initialHandle);
    if (initialHandle === 'user') throw new Error('DM handle user is reserved for the local human.');
    await directory(profileDir, false);
    const root = join(profileDir, 'magi-dm');
    await ensureDirectory(profileDir, root);
    for (const child of ['pairs', 'inbox', 'outbox']) await ensureDirectory(root, join(root, child));
    let identity = await readObject(join(root, 'identity.json'));
    if (identity === undefined) {
      // Missing identity alongside prior state (even interrupted identity writes)
      // must not silently generate a new owner after a profile rename.
      if ((await readdir(root)).some(name => !['pairs', 'inbox', 'outbox'].includes(name))) {
        throw new Error('Missing DM identity with existing state; manual recovery required.');
      }
      for (const child of ['pairs', 'inbox', 'outbox']) {
        if ((await readdir(join(root, child))).length) throw new Error('Missing DM identity with existing state; manual recovery required.');
      }
      identity = { handle: initialHandle, name: initialHandle };
      await publish(root, 'identity.json', identity, true);
    }
    return new DmStore(root, validateIdentity(identity));
  }
  identity() { return clone(this.#identity); }
  #mutate(callback) {
    const task = this.#tail.then(callback);
    this.#tail = task.catch(() => {});
    return task;
  }
  async #check() {
    await directory(this.#root);
    const identity = validateIdentity(await readObject(join(this.#root, 'identity.json')));
    if (JSON.stringify(identity) !== JSON.stringify(this.#identity)) throw new Error('DM identity changed externally; reopen the store.');
  }
  async setName(name) {
    const normalized = nameValue(name);
    return this.#mutate(async () => {
      await this.#check();
      const identity = { ...this.#identity, name: normalized };
      await publish(this.#root, 'identity.json', identity, false);
      this.#identity = identity;
      return this.identity();
    });
  }
  /** createSession(id) must durably create exactly id before resolving. */
  async pair(peer, createSession) {
    if (peer === 'user') throw new Error('@user uses the existing main Session, not a Bot pair Session.');
    const dmId = dmIdFor(this.#identity.handle, peer);
    if (typeof createSession !== 'function') throw new Error('DM pairing requires createSession(sessionId).');
    return this.#mutate(async () => {
      await this.#check();
      const dir = join(this.#root, 'pairs');
      await directory(dir);
      const filename = `${dmId}.json`;
      let mapping = await readObject(join(dir, filename));
      if (mapping !== undefined) {
        if (Object.keys(mapping).sort().join(',') !== 'dmId,peer,sessionId,state' || mapping.dmId !== dmId || mapping.peer !== peer ||
            !sessionPattern.test(mapping.sessionId) || !['reserved', 'ready'].includes(mapping.state)) throw new Error('Corrupt DM pair mapping.');
        if (mapping.state !== 'ready') throw new Error(`Incomplete DM reservation ${mapping.sessionId}; manual recovery required; refusing Session recreation.`);
      } else {
        mapping = { dmId, sessionId: `session-${randomUUID()}`, peer, state: 'reserved' };
        await publish(dir, filename, mapping, true);
        // Never retry this callback automatically: the native controller may not
        // safely reuse even a blank persisted Session. Reservation survives failure.
        await createSession(mapping.sessionId);
        await this.#check();
        const current = await readObject(join(dir, filename));
        if (JSON.stringify(current) !== JSON.stringify(mapping)) throw new Error('DM reservation changed during creation.');
        await publish(dir, filename, { ...mapping, state: 'ready' }, false);
      }
      return { dmId, sessionId: mapping.sessionId, peer };
    });
  }
  #recordDir(direction, id) {
    if (!['inbox', 'outbox'].includes(direction)) throw new Error('Invalid DM record direction.');
    if (id !== undefined && (typeof id !== 'string' || !idPattern.test(id))) throw new Error('Invalid DM record id.');
    return join(this.#root, direction);
  }
  async getRecord(direction, id) {
    const dir = this.#recordDir(direction, id);
    if (id === undefined) throw new Error('Invalid DM record id.');
    await this.#tail;
    await this.#check(); await directory(dir);
    return readObject(join(dir, `${id}.json`));
  }
  async putRecord(direction, id, record) {
    const dir = this.#recordDir(direction, id);
    if (id === undefined) throw new Error('Invalid DM record id.');
    const snapshot = JSON.parse(encode(record));
    return this.#mutate(async () => {
      await this.#check(); await directory(dir);
      await publish(dir, `${id}.json`, snapshot, false);
    });
  }
  async records(direction) {
    const dir = this.#recordDir(direction);
    await this.#tail;
    await this.#check(); await directory(dir);
    const entries = [];
    for (const filename of await readdir(dir)) {
      // A crash may leave a private exclusive temporary file, never an entry.
      if (/^\.dm-[0-9a-f-]{36}\.tmp$/.test(filename)) {
        await readObject(join(dir, filename));
        continue;
      }
      if (!filename.endsWith('.json') || !idPattern.test(filename.slice(0, -5))) throw new Error('Unsafe DM record filename.');
      const value = await readObject(join(dir, filename));
      if (value === undefined) throw new Error('DM record disappeared during listing.');
      entries.push({ id: filename.slice(0, -5), value });
    }
    const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
    entries.sort((a, b) => compare(a.value.createdAt ?? '', b.value.createdAt ?? '') || compare(a.id, b.id));
    return entries.map(entry => entry.value);
  }
}
