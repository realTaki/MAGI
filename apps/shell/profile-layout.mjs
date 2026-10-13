import * as fs from 'node:fs/promises';
import path from 'node:path';

export function profilePaths(home, paths = path) {
  const dataRoot = paths.join(home, '.magi');
  const shell = paths.join(dataRoot, 'shell');
  return { dataRoot, shell, cache: paths.join(shell, 'cache'), legacyShell: paths.join(dataRoot, 'MAGI'), legacyCache: paths.join(dataRoot, 'cache') };
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

// Move cache entries without replacing either copy of a colliding file. A
// failure leaves the remainder at its original path; the same walk can resume.
async function mergeCache(source, destination, conflicts) {
  if (!await exists(source)) return;
  if (!await exists(destination)) { await fs.mkdir(path.dirname(destination), { recursive: true }); await fs.rename(source, destination); return; }
  const from = await fs.lstat(source), to = await fs.lstat(destination);
  if (from.isDirectory() && !from.isSymbolicLink() && to.isDirectory() && !to.isSymbolicLink()) {
    for (const name of await fs.readdir(source)) await mergeCache(path.join(source, name), path.join(destination, name), path.join(conflicts, name));
    await fs.rmdir(source); return;
  }
  let retained = conflicts, suffix = 0;
  while (await exists(retained)) retained = `${conflicts}.${++suffix}`;
  await fs.mkdir(path.dirname(retained), { recursive: true });
  await fs.rename(source, retained);
}

/** Prepare the profile before services run; all Git work uses Shell's owner. */
export async function prepareShellProfile({ home, repository, tools, command, assertOpen = () => {}, progress = () => {}, rename = fs.rename }) {
  const paths = profilePaths(home), { dataRoot, shell, legacyShell, legacyCache, cache } = paths;
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
    try { process.kill(owner.pid, 0); throw new Error('Shell profile migration is already running'); }
    catch (failure) { if (failure.code !== 'ESRCH') throw failure; }
    await fs.unlink(lock);
    handle = await fs.open(lock, 'wx');
  }
  await handle.writeFile(JSON.stringify({ pid: process.pid }));
  const git = (args, cwd) => { assertOpen(); return command(tools.git, args, { cwd, env: tools.env, description: 'Could not prepare Shell profile Git links' }); };
  const cacheHold = path.join(dataRoot, '.shell-cache-hold');
  const staging = path.join(dataRoot, '.shell-bootstrap');
  try {
    assertOpen();
    for (const root of [shell, legacyShell]) {
      if (await exists(root)) {
        const stat = await fs.lstat(root);
        if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Profile source must be a real directory: ${root}`);
      }
    }
    let state = await exists(journal) ? await json(journal) : null;
    if (state && (state.version !== 1 || !['move', 'clone'].includes(state.kind))) throw new Error('Unknown Shell profile migration journal; no files were replaced');
    if (!state && await exists(legacyShell)) {
      if (await exists(path.join(shell, '.git'))) throw new Error('Both legacy MAGI and shell checkouts exist; keep both and resolve the conflict before startup');
      await cacheOnly(shell);
      const marker = path.join(legacyShell, '.git');
      if (!await exists(marker)) throw new Error('Legacy MAGI source is not a Git checkout');
      const linked = (await fs.lstat(marker)).isFile();
      await git(['rev-parse', '--show-toplevel'], legacyShell);
      // Never move the main repository during a known Git write transaction.
      const common = (await git(['rev-parse', '--path-format=absolute', '--git-common-dir'], legacyShell)).trim();
      for (const name of ['index.lock', 'config.lock', 'HEAD.lock', 'packed-refs.lock']) if (await exists(path.join(common, name))) throw new Error(`Git is locked (${name}); retry migration after the Git operation finishes`);
      if (await exists(cacheHold)) throw new Error('Unjournaled Shell cache holding directory exists; preserve it for inspection');
      state = { version: 1, kind: 'move', linked, legacyCommon: common };
      await save(journal, state); // durable intent precedes directory relocation
    }
    if (state?.kind === 'move') {
      if (await exists(legacyShell)) {
        await cacheOnly(shell);
        if (await exists(shell)) {
          if (await exists(path.join(shell, 'cache'))) {
            if (await exists(cacheHold)) await mergeCache(path.join(shell, 'cache'), cacheHold, path.join(cacheHold, 'legacy-profile'));
            else await rename(path.join(shell, 'cache'), cacheHold);
          }
          await fs.rmdir(shell);
        }
        progress('Migrating Shell source and Git links…', 0.04);
        if (state.linked) {
          const common = (await git(['rev-parse', '--path-format=absolute', '--git-common-dir'], legacyShell)).trim();
          // Windows cannot rename the current directory held by Git itself.
          // Run from the profile parent with an explicit common Git directory.
          await git(['--git-dir', common, 'worktree', 'move', legacyShell, shell], dataRoot);
        }
        else await rename(legacyShell, shell);
      }
      if (!await exists(path.join(shell, '.git'))) throw new Error('Shell relocation is incomplete; original files and recovery journal were retained');
      // Git's documented repair reconnects every linked tree after moving the
      // main repository. It retains indexes, branches, reflogs and dirty files.
      await git(['worktree', 'repair'], shell);
      await git(['rev-parse', '--show-toplevel'], shell);
    }
    if (!state && !await exists(path.join(shell, '.git'))) {
      await cacheOnly(shell);
      if (await exists(staging)) throw new Error('Unjournaled Shell bootstrap directory exists; preserve it for inspection');
      await fs.mkdir(staging);
      progress('Cloning Shell source…', 0.06);
      try { await git(['clone', repository, path.join(staging, 'checkout')], staging); }
      catch (error) { await fs.rm(staging, { recursive: true, force: true }); throw error; }
      const entries = await fs.readdir(path.join(staging, 'checkout'));
      // cache is reserved for profile data; do not silently overwrite source.
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
    // Older Shell versions renamed app/ to user/ before opening the runtime.
    // Repair that moved User tree only when its pointer belongs to this actual
    // common repository; never attach an unrelated checkout by basename.
    const user = path.join(dataRoot, 'user', 'MAGI'), userMarker = path.join(user, '.git');
    if (await exists(userMarker) && (await fs.lstat(userMarker)).isFile()) {
      const pointer = /^gitdir: (.+)$/m.exec((await fs.readFile(userMarker, 'utf8')).trim());
      if (pointer) {
        const common = (await git(['rev-parse', '--path-format=absolute', '--git-common-dir'], shell)).trim();
        const admin = path.resolve(user, pointer[1]);
        const equal = (left, right) => process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right;
        const allowed = [path.join(common, 'worktrees'), path.join(legacyShell, '.git', 'worktrees')];
        if (typeof state?.legacyCommon === 'string' && path.isAbsolute(state.legacyCommon)) allowed.push(path.join(state.legacyCommon, 'worktrees'));
        if (allowed.some((root) => equal(path.dirname(admin), root)) && await exists(path.join(common, 'worktrees', path.basename(admin)))) await git(['worktree', 'repair', user], shell);
      }
    }
    assertOpen();
    // Older source revisions do not yet have /cache/ in their .gitignore.
    // Keep generated profile caches out of status without editing user source.
    const exclude = (await git(["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], shell)).trim();
    const exclusions = await exists(exclude) ? await fs.readFile(exclude, "utf8") : "";
    if (!exclusions.split(/\r?\n/).includes("/cache/")) {
      await fs.mkdir(path.dirname(exclude), { recursive: true });
      await fs.appendFile(exclude, `${exclusions.endsWith("\n") || !exclusions ? "" : "\n"}/cache/\n`);
    }
    await mergeCache(cacheHold, cache, path.join(cache, 'legacy-profile'));
    await mergeCache(legacyCache, cache, path.join(cache, 'legacy-profile'));
    await fs.mkdir(cache, { recursive: true });
    if (state) await fs.unlink(journal);
    return shell;
  } catch (error) {
    throw new Error(`Shell profile preparation failed; source/data and recovery journal are retained for retry: ${error.message}`, { cause: error });
  } finally {
    await handle.close(); await fs.unlink(lock);
  }
}
