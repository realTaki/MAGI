import * as fs from 'node:fs/promises';
import path from 'node:path';

export function profilePaths(home, paths = path) {
  const dataRoot = paths.join(home, '.magi');
  const shell = paths.join(dataRoot, 'shell');
  return { dataRoot, shell, cache: paths.join(shell, 'cache') };
}
async function exists(file) { try { await fs.lstat(file); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } }
async function json(file) { return JSON.parse(await fs.readFile(file, 'utf8')); }
async function save(file, value) {
  const temporary = `${file}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(value));
  await fs.rename(temporary, file);
}
async function cacheOnly(directory) {
  if (!await exists(directory)) return;
  const stat = await fs.lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (await fs.readdir(directory)).some((name) => name !== 'cache')) throw new Error(`Shell destination contains existing files: ${directory}`);
}

/** Prepare the profile before services run; all Git work uses Shell's owner. */
export async function prepareShellProfile({ home, repository, tools, command, assertOpen = () => {}, progress = () => {}, rename = fs.rename }) {
  const paths = profilePaths(home), { dataRoot, shell, cache } = paths;
  await fs.mkdir(dataRoot, { recursive: true });
  const lock = path.join(dataRoot, '.shell-layout.lock'), journal = path.join(dataRoot, '.shell-layout.json');
  let handle;
  try { handle = await fs.open(lock, 'wx'); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    // A live owner's lock is never stolen. Crash recovery only retires a lock
    // whose recorded process is absent, without signalling any process.
    const owner = await json(lock);
    if (!Number.isInteger(owner.pid) || owner.pid <= 0) throw new Error('Invalid Shell profile lock; preserve it for inspection');
    try { process.kill(owner.pid, 0); throw new Error('Shell profile preparation is already running'); }
    catch (failure) { if (failure.code !== 'ESRCH') throw failure; }
    await fs.unlink(lock);
    handle = await fs.open(lock, 'wx');
  }
  await handle.writeFile(JSON.stringify({ pid: process.pid }));
  const git = (args, cwd) => { assertOpen(); return command(tools.git, args, { cwd, env: tools.env, description: 'Could not prepare Shell source' }); };
  const staging = path.join(dataRoot, '.shell-bootstrap');
  try {
    assertOpen();
    if (await exists(shell)) {
      const stat = await fs.lstat(shell);
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Profile source must be a real directory: ${shell}`);
    }
    let state = await exists(journal) ? await json(journal) : null;
    if (state && (state.version !== 1 || state.kind !== 'clone')) throw new Error('Unknown Shell profile journal; no files were replaced');
    if (!state && !await exists(path.join(shell, '.git'))) {
      await cacheOnly(shell);
      if (await exists(staging)) throw new Error('Unjournaled Shell bootstrap directory exists; preserve it for inspection');
      await fs.mkdir(staging);
      progress('Cloning Shell source…', 0.06);
      try { await git(['clone', '--no-local', repository, path.join(staging, 'checkout')], staging); }
      catch (error) { await fs.rm(staging, { recursive: true, force: true }); throw error; }
      const entries = await fs.readdir(path.join(staging, 'checkout'));
      if (entries.includes('cache')) throw new Error('Repository contains a tracked root cache directory');
      state = { version: 1, kind: 'clone', entries: [...entries.filter((name) => name !== '.git'), '.git'] };
      await save(journal, state);
    }
    if (state?.kind === 'clone') {
      if (!Array.isArray(state.entries) || state.entries.some((name) => typeof name !== 'string' || name === '.' || name === '..' || path.basename(name) !== name || name === 'cache')) throw new Error('Invalid Shell bootstrap journal');
      await fs.mkdir(shell, { recursive: true });
      for (const name of state.entries) {
        assertOpen();
        const source = path.join(staging, 'checkout', name), destination = path.join(shell, name);
        if (await exists(source)) {
          if (await exists(destination)) throw new Error(`Shell bootstrap destination already exists: ${destination}`);
          await rename(source, destination);
        } else if (!await exists(destination)) throw new Error(`Shell bootstrap entry is missing: ${name}`);
      }
      await git(['rev-parse', '--show-toplevel'], shell);
      await fs.rm(staging, { recursive: true, force: true });
    }
    assertOpen();
    await fs.mkdir(cache, { recursive: true });
    if (state) await fs.unlink(journal);
    return shell;
  } catch (error) {
    throw new Error(`Shell profile preparation failed; source/data and recovery journal are retained for retry: ${error.message}`, { cause: error });
  } finally {
    await handle.close(); await fs.unlink(lock);
  }
}
