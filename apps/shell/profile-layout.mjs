import * as fs from 'node:fs/promises';
import path from 'node:path';
import { ensureManagedClone } from './clone.mjs';

export function profilePaths(home, paths = path) {
  const dataRoot = paths.join(home, '.magi');
  const shellHome = paths.join(dataRoot, 'shell');
  return { dataRoot, shellHome, shell: paths.join(shellHome, 'MAGI'), cache: paths.join(shellHome, 'cache') };
}
async function exists(file) { try { await fs.lstat(file); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } }
async function json(file) { return JSON.parse(await fs.readFile(file, 'utf8')); }
async function save(file, value) {
  const temporary = `${file}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(value));
  await fs.rename(temporary, file);
}
async function shellStartupFilesOnly(directory) {
  if (!await exists(directory)) return;
  const stat = await fs.lstat(directory);
  const reject = () => { throw new Error(`Shell destination contains existing files: ${directory}`); };
  if (!stat.isDirectory() || stat.isSymbolicLink()) reject();
  // main.mjs configures Electron storage before preparing the managed clone.
  // Preserve those owned siblings; never accept unrelated files or redirects.
  const directories = new Set(['cache', 'electron', 'logs', 'crash-dumps']);
  const files = new Set(['window-state.json', 'window-state.json.tmp']);
  for (const name of await fs.readdir(directory)) {
    const entry = await fs.lstat(path.join(directory, name));
    if (entry.isSymbolicLink() || !(directories.has(name) && entry.isDirectory() || files.has(name) && entry.isFile())) reject();
  }
}

/** Prepare the profile before services run; all Git work uses Shell's owner. */
export async function prepareShellProfile({ home, repository, tools, command, assertOpen = () => {}, progress = () => {}, rename = fs.rename }) {
  const { dataRoot, shellHome, shell, cache } = profilePaths(home);
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
  const git = (args, options) => { assertOpen(); return command(tools.git, args, { env: tools.env, description: 'Could not prepare Shell source', ...options }); };
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
      await shellStartupFilesOnly(shellHome);
      if (await exists(staging)) throw new Error('Unjournaled Shell bootstrap directory exists; preserve it for inspection');
      await fs.mkdir(staging);
      progress('Cloning Shell source…', 0.06);
      try { await git(['clone', '--no-local', repository, path.join(staging, 'checkout')], { cwd: staging }); }
      catch (error) { await fs.rm(staging, { recursive: true, force: true }); throw error; }
      state = { version: 1, kind: 'clone' };
      await save(journal, state);
    }
    if (state?.kind === 'clone') {
      const source = path.join(staging, 'checkout');
      if (await exists(source)) {
        if (await exists(shell)) throw new Error(`Shell bootstrap destination already exists: ${shell}`);
        await fs.mkdir(shellHome, { recursive: true });
        await rename(source, shell);
      } else if (!await exists(path.join(shell, '.git'))) throw new Error('Shell bootstrap checkout is missing');
      await git(['rev-parse', '--show-toplevel'], { cwd: shell });
      await fs.rm(staging, { recursive: true, force: true });
    }
    assertOpen();
    await ensureManagedClone({
      run: git, env: tools.env, url: repository, destination: shell, branch: 'magi/shell',
    });
    await fs.mkdir(cache, { recursive: true });
    if (state) await fs.unlink(journal);
    return shell;
  } catch (error) {
    throw new Error(`Shell profile preparation failed; source/data and recovery journal are retained for retry: ${error.message}`, { cause: error });
  } finally {
    await handle.close(); await fs.unlink(lock);
  }
}
