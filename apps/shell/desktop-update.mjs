/** Linux desktop activation. This module is copied next to a durable bundled Node
 * before Electron exits; its only dependencies are Node built-ins. */
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync, closeSync, constants, copyFileSync, existsSync, fchmodSync, fstatSync, fsyncSync,
  linkSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, readdirSync,
  renameSync, statSync, unlinkSync, writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SELF = fileURLToPath(import.meta.url);
const FINAL = new Set(["completed", "rolled-back"]);
const STATES = new Set(["preparing", "prepared", "waiting-for-exit", "swapping", "installed", "launching", "awaiting-ready", "rolling-back", "completed", "rolled-back", "recovery-required"]);
const PREFIX = "MAGI_DESKTOP_UPDATE_";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const errorText = (error) => error instanceof Error ? error.message : String(error);
function assert(condition, message) { if (!condition) throw new Error(message); }
function remove(file) { try { unlinkSync(file); } catch (error) { if (error.code !== "ENOENT") throw error; } }
function syncDirectory(directory) { const fd = openSync(directory, "r"); try { fsyncSync(fd); } finally { closeSync(fd); } }
function atomicWrite(file, value, mode = 0o600) {
  const temporary = `${file}.${randomUUID()}.tmp`;
  const fd = openSync(temporary, "wx", mode);
  try { writeFileSync(fd, value); fchmodSync(fd, mode); fsyncSync(fd); } finally { closeSync(fd); }
  try { renameSync(temporary, file); syncDirectory(path.dirname(file)); }
  finally { remove(temporary); }
}
function json(file) {
  const info = lstatSync(file);
  assert(info.isFile() && info.size <= 2 * 1024 * 1024, "Invalid desktop update JSON file");
  return JSON.parse(readFileSync(file, "utf8"));
}
function regularFile(file, label) {
  assert(typeof file === "string" && path.isAbsolute(file), `${label} must be an absolute path`);
  const info = lstatSync(file);
  assert(info.isFile() && !info.isSymbolicLink() && info.size > 0, `${label} must be a nonempty regular file, not a symlink`);
  return realpathSync(file);
}
function hashFile(file) {
  const fd = openSync(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    assert(fstatSync(fd).isFile(), "Desktop artifact must be a regular file");
    const hash = createHash("sha256"), chunk = Buffer.allocUnsafe(1024 * 1024);
    let size; while ((size = readSync(fd, chunk, 0, chunk.length, null)) > 0) hash.update(chunk.subarray(0, size));
    return hash.digest("hex");
  } finally { closeSync(fd); }
}
function maybeHash(file) { return existsSync(file) ? hashFile(file) : null; }
function processIdentity(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  try {
    process.kill(pid, 0);
    const raw = readFileSync(`/proc/${pid}/stat`, "utf8");
    const stat = raw.slice(raw.lastIndexOf(")") + 2).split(" ");
    return stat[0] === "Z" ? null : { pid, start: stat[19] };
  } catch (error) {
    if (error.code === "ESRCH" || error.code === "ENOENT") return null;
    throw error;
  }
}
function alive(identity) { return Boolean(identity && processIdentity(identity.pid)?.start === identity.start); }
function locations(target, id) {
  const base = path.join(path.dirname(target), `.${path.basename(target)}.magi-updates`);
  const directory = path.join(base, "transactions", id);
  return {
    base, directory, active: path.join(base, "active.json"), journalPath: path.join(directory, "journal.json"),
    helperPath: path.join(directory, "desktop-update.mjs"), nodePath: path.join(directory, "node"),
    stage: path.join(path.dirname(target), `.${path.basename(target)}.${id}.next`),
    backup: path.join(path.dirname(target), `.${path.basename(target)}.${id}.previous`),
    ack: path.join(directory, "ready.json"), runner: path.join(directory, "runner.json"),
  };
}
function validateManifest(manifest, home) {
  assert(manifest?.schema === 1 && manifest.platform === "linux" && manifest.arch === process.arch, "Unsupported desktop manifest schema, platform, or architecture");
  assert(/^[a-f0-9]{40}$/.test(manifest.revision) && (manifest.previous === "" || /^[a-f0-9]{40}$/.test(manifest.previous)), "Invalid desktop manifest revision");
  assert(/^[a-f0-9]{64}$/.test(manifest.sha256), "Invalid desktop manifest SHA256");
  const sourceDir = manifest.sourceDir ?? manifest.dir;
  assert(typeof sourceDir === "string" && path.isAbsolute(sourceDir), "Desktop manifest needs its revision source directory");
  const revisions = path.join(home, "shell", "revisions");
  assert(realpathSync(revisions) === revisions, "Desktop revision root must not be a symlink");
  assert(realpathSync(sourceDir) === path.join(revisions, manifest.revision), "Desktop source directory is outside its trusted revision root");
  assert(statSync(sourceDir).isDirectory(), "Desktop revision source must be a directory");
  if (manifest.dir !== undefined) assert(realpathSync(manifest.dir) === realpathSync(sourceDir), "Desktop manifest source directories disagree");
  return realpathSync(sourceDir);
}
function readJournal(journalPath) {
  const journal = json(journalPath);
  assert(journal?.schema === 1 && /^[a-f0-9-]{36}$/.test(journal.transactionId) && STATES.has(journal.status), "Malformed desktop update journal");
  assert(typeof journal.target === "string" && path.isAbsolute(journal.target) && realpathSync(path.dirname(journal.target)) === path.dirname(journal.target), "Invalid desktop target path");
  const loc = locations(journal.target, journal.transactionId);
  assert(path.resolve(journalPath) === loc.journalPath && realpathSync(loc.directory) === loc.directory, "Desktop update journal path does not match its transaction");
  assert(typeof journal.home === "string" && realpathSync(journal.home) === journal.home, "Invalid desktop profile path");
  assert(journal.sourceDir === validateManifest(journal.manifest, journal.home), "Desktop transaction source directory changed");
  assert(journal.launcherState?.path === path.join(journal.home, "shell", "launcher.json"), "Invalid launcher state snapshot path");
  const snapshot = journal.launcherState;
  assert(typeof snapshot.exists === "boolean" && Number.isInteger(snapshot.mode) && snapshot.mode >= 0 && snapshot.mode <= 0o777, "Malformed launcher state snapshot");
  assert(snapshot.exists ? typeof snapshot.data === "string" && /^[a-f0-9]{64}$/.test(snapshot.sha256) && createHash("sha256").update(Buffer.from(snapshot.data, "base64")).digest("hex") === snapshot.sha256 : snapshot.data === null, "Launcher state snapshot integrity mismatch");
  assert(/^[a-f0-9]{64}$/.test(journal.originalSha256) && Number.isSafeInteger(journal.job) && journal.job >= 0, "Invalid desktop update identity");
  assert(Array.isArray(journal.progress), "Malformed desktop journal progress");
  assert(Number.isFinite(journal.timeoutMs) && journal.timeoutMs > 0 && Number.isFinite(journal.exitTimeoutMs) && journal.exitTimeoutMs > 0, "Invalid desktop update timeout");
  return { journal, loc };
}
function save(journal, loc, status, extra = {}) {
  Object.assign(journal, extra, { status, updatedAt: new Date().toISOString() });
  journal.progress.push({ status, at: journal.updatedAt });
  atomicWrite(loc.journalPath, JSON.stringify(journal, null, 2));
}
function ownsLock(loc, id) { try { return json(loc.active).transactionId === id; } catch (error) { if (error.code === "ENOENT") return false; throw error; } }
function releaseLock(loc, id) { if (ownsLock(loc, id)) { remove(loc.active); syncDirectory(loc.base); } }
function copyDurable(source, destination, mode) {
  copyFileSync(source, destination, constants.COPYFILE_EXCL | constants.COPYFILE_FICLONE);
  chmodSync(destination, mode);
  const fd = openSync(destination, "r"); try { fsyncSync(fd); } finally { closeSync(fd); }
  syncDirectory(path.dirname(destination));
}
function terminalCleanup(journal, loc) {
  remove(loc.stage);
  if (journal.status === "completed") remove(loc.backup);
  remove(loc.nodePath);
  releaseLock(loc, journal.transactionId);
}

/** Prepare only; caller starts the returned durable helper detached, then quits. */
export async function prepareDesktopUpdate({ installer, target, execPath, manifest, job, pid = process.pid, home, nodePath, timeoutMs = 90_000, exitTimeoutMs = 60_000, launcherStatePath }) {
  assert(process.platform === "linux", "Transactional desktop activation currently requires Linux");
  assert(Number.isSafeInteger(job) && job >= 0 && Number.isInteger(pid) && pid > 0, "Desktop update requires a job and old process id");
  assert(Number.isFinite(timeoutMs) && timeoutMs > 0 && Number.isFinite(exitTimeoutMs) && exitTimeoutMs > 0, "Desktop update timeouts must be positive");
  home = realpathSync(home);
  const sourceDir = validateManifest(manifest, home);
  installer ??= manifest.installer;
  installer = regularFile(installer, "Desktop installer");
  assert(installer === regularFile(manifest.installer, "Manifest installer") && installer.startsWith(`${sourceDir}${path.sep}`), "Desktop installer is outside its manifest source directory");
  target = regularFile(target, "Outer AppImage");
  assert(target !== regularFile(execPath, "Extracted Electron executable") && target !== installer, "Desktop update requires the real outer AppImage, not its extracted executable or installer");
  assert((statSync(target).mode & 0o111) !== 0, "Outer AppImage must be executable");
  nodePath = regularFile(nodePath, "Bundled Node runtime");
  assert((statSync(nodePath).mode & 0o111) !== 0, "Bundled Node runtime must be executable");
  assert(hashFile(installer) === manifest.sha256, "Desktop installer SHA256 does not match its manifest");
  launcherStatePath ??= path.join(home, "shell", "launcher.json");
  assert(path.resolve(launcherStatePath) === path.join(home, "shell", "launcher.json"), "Launcher snapshot must belong to the Shell profile");
  const stateExists = existsSync(launcherStatePath);
  if (stateExists) { regularFile(launcherStatePath, "Launcher state"); assert(statSync(launcherStatePath).size <= 1024 * 1024, "Launcher state is too large"); }
  const stateData = stateExists ? readFileSync(launcherStatePath) : null;
  const transactionId = randomUUID(), loc = locations(target, transactionId);
  mkdirSync(loc.directory, { recursive: true, mode: 0o700 });
  assert(realpathSync(loc.directory) === loc.directory, "Desktop update directory must not traverse symlinks");
  const journal = {
    schema: 1, transactionId, target, home, sourceDir, job,
    manifest: { ...manifest, sourceDir, installer }, originalSha256: hashFile(target),
    oldProcess: processIdentity(pid), oldPid: pid, timeoutMs, exitTimeoutMs,
    launcherState: { path: launcherStatePath, exists: stateExists, data: stateData?.toString("base64") ?? null, sha256: stateData ? createHash("sha256").update(stateData).digest("hex") : null, mode: stateExists ? statSync(launcherStatePath).mode & 0o777 : 0o600 },
    status: "preparing", progress: [], createdAt: new Date().toISOString(),
  };
  save(journal, loc, "preparing");
  const ownerFile = path.join(loc.directory, "owner.json");
  atomicWrite(ownerFile, JSON.stringify({ transactionId, journalPath: loc.journalPath }));
  try { linkSync(ownerFile, loc.active); syncDirectory(loc.base); }
  catch (error) {
    if (error.code === "EEXIST") throw new Error(`Another desktop update is active: ${json(loc.active).journalPath}`);
    throw error;
  }
  try {
    copyDurable(installer, loc.stage, 0o755);
    assert(hashFile(loc.stage) === manifest.sha256, "Staged desktop installer SHA256 changed during copy");
    copyDurable(nodePath, loc.nodePath, 0o700);
    atomicWrite(loc.helperPath, readFileSync(SELF), 0o600);
    save(journal, loc, "prepared");
    return { journalPath: loc.journalPath, helperPath: loc.helperPath, nodePath: loc.nodePath, transactionId, revision: manifest.revision, sha256: manifest.sha256 };
  } catch (error) {
    save(journal, loc, "rolled-back", { error: errorText(error) });
    terminalCleanup(journal, loc);
    throw error;
  }
}

/** Discover an interrupted transaction without trusting a caller-supplied path. */
export function pendingDesktopUpdate(target) {
  const actual = path.join(realpathSync(path.dirname(target)), path.basename(target));
  const active = locations(actual, "unused").active;
  if (!existsSync(active)) return null;
  const lock = json(active), { journal, loc } = readJournal(lock.journalPath);
  assert(journal.target === actual && journal.transactionId === lock.transactionId, "Desktop update lock identity mismatch");
  const helperRunning = existsSync(loc.runner) && alive(json(loc.runner));
  return { journalPath: lock.journalPath, status: journal.status, transactionId: journal.transactionId, helperRunning, nodePath: loc.nodePath, helperPath: loc.helperPath };
}

/** Cold-start repair may need to replace a partially copied recovery runtime.
 * Never call this from a candidate/rollback launch. Start the returned helper
 * with --run JOURNAL CURRENT_PID and quit before launching any backend. */
export async function prepareDesktopUpdateRecovery({ target, nodePath }) {
  const pending = pendingDesktopUpdate(target);
  if (!pending || pending.helperRunning) return pending;
  let { journal, loc } = readJournal(pending.journalPath);
  const releaseRunner = await acquireRunner(loc);
  try {
    ({ journal } = readJournal(pending.journalPath));
    if (FINAL.has(journal.status)) { terminalCleanup(journal, loc); return null; }
    assert(ownsLock(loc, journal.transactionId), "Desktop recovery no longer owns its active transaction");
    nodePath = regularFile(nodePath, "Bundled recovery Node runtime");
    const temporary = path.join(loc.directory, `node-${randomUUID()}`);
    copyDurable(nodePath, temporary, 0o700);
    renameSync(temporary, loc.nodePath); syncDirectory(loc.directory);
    atomicWrite(loc.helperPath, readFileSync(SELF), 0o600);
    return { ...pending, helperRunning: false };
  } finally { releaseRunner(); }
}

function boundContext({ env = process.env, artifact = env.APPIMAGE, journalPath = env[`${PREFIX}JOURNAL`] } = {}) {
  if (!journalPath && !env[`${PREFIX}ID`]) return null;
  assert(journalPath && journalPath === env[`${PREFIX}JOURNAL`], "Desktop update journal environment mismatch");
  const { journal, loc } = readJournal(journalPath);
  assert(env[`${PREFIX}ID`] === journal.transactionId && env[`${PREFIX}SHA256`] === journal.manifest.sha256 && env[`${PREFIX}REVISION`] === journal.manifest.revision, "Desktop update environment identity mismatch");
  const rollback = env[`${PREFIX}MODE`] === "rollback";
  assert(rollback || env[`${PREFIX}MODE`] === "candidate", "Invalid desktop update launch mode");
  assert(regularFile(artifact, "Running outer AppImage") === journal.target, "Running desktop artifact does not match transaction target");
  assert(hashFile(artifact) === (rollback ? journal.originalSha256 : journal.manifest.sha256), "Running desktop artifact SHA256 does not match transaction");
  assert(rollback ? ["rolling-back", "rolled-back"].includes(journal.status) : ["launching", "awaiting-ready", "completed"].includes(journal.status), "Desktop transaction is not in the expected launch phase");
  return { journal, loc, rollback };
}

/** Only verified startup context may override the packaged backend revision. */
export function desktopUpdateLaunchContext(options) {
  const bound = boundContext(options);
  if (!bound) return null;
  const { journal, loc, rollback } = bound;
  return {
    journalPath: loc.journalPath, transactionId: journal.transactionId, job: journal.job, rollback,
    ...(rollback ? { error: journal.error || "Desktop activation failed" } : { revision: journal.manifest.revision, sourceDir: journal.sourceDir }),
  };
}

/** Call only after the real backend AND UI are ready. Expected revision alone
 * is not an observation: the caller supplies the running backend's revision. */
export function acknowledgeDesktopUpdate({ revision, ...options } = {}) {
  const bound = boundContext(options);
  if (!bound || bound.rollback) return false;
  const { journal, loc } = bound;
  assert(revision === journal.manifest.revision, "Ready backend revision does not match the desktop transaction");
  const identity = processIdentity(process.pid);
  assert(identity, "Ready desktop process identity is unavailable");
  atomicWrite(loc.ack, JSON.stringify({ schema: 1, transactionId: journal.transactionId, sha256: journal.manifest.sha256, revision, ...identity, at: new Date().toISOString() }));
  return true;
}
function validAck(journal, loc) {
  if (!existsSync(loc.ack)) return false;
  const ack = json(loc.ack);
  assert(ack?.schema === 1 && ack.transactionId === journal.transactionId && ack.sha256 === journal.manifest.sha256 && ack.revision === journal.manifest.revision, "Desktop readiness ACK identity mismatch");
  assert(alive(journal.candidateProcess) && alive(ack), "Ready desktop exited before activation could commit");
  const rows = new Map(processTable().map((row) => [row.pid, row]));
  let ancestor = rows.get(ack.pid);
  const visited = new Set();
  while (ancestor && ancestor.pid !== journal.candidateProcess.pid && !visited.has(ancestor.pid)) {
    visited.add(ancestor.pid); ancestor = rows.get(ancestor.parent);
  }
  assert(ancestor?.pid === journal.candidateProcess.pid && ancestor.start === journal.candidateProcess.start, "Desktop readiness ACK is not from the launched candidate process tree");
  assert(hashFile(journal.target) === journal.manifest.sha256, "Installed desktop artifact changed before readiness ACK");
  return true;
}
function launchEnvironment(journal, loc, mode) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (key.startsWith(PREFIX)) delete env[key];
  for (const key of ["APPIMAGE", "APPDIR", "ARGV0", "OWD", "MAGI_REVISION", "MAGI_REVISION_DIR", "MAGI_APP_CHECKOUT", "MAGI_CHILD", "MAGI_LAUNCHER", "MAGI_PREVIOUS_REVISION", "MAGI_REPLACE_ERROR"]) delete env[key];
  Object.assign(env, {
    APPIMAGE_EXTRACT_AND_RUN: "1", APPIMAGE: journal.target, MAGI_HOME: journal.home,
    [`${PREFIX}JOURNAL`]: loc.journalPath, [`${PREFIX}ID`]: journal.transactionId,
    [`${PREFIX}SHA256`]: journal.manifest.sha256, [`${PREFIX}REVISION`]: journal.manifest.revision,
    [`${PREFIX}MODE`]: mode, MAGI_REPLACE_JOB: String(journal.job),
  });
  if (mode === "candidate") Object.assign(env, { MAGI_REVISION: journal.manifest.revision, MAGI_REVISION_DIR: journal.sourceDir });
  else env.MAGI_REPLACE_ERROR = journal.error || "Desktop activation failed";
  return env;
}
function processTable() {
  const rows = [];
  for (const entry of readdirSync("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const raw = readFileSync(`/proc/${entry}/stat`, "utf8");
      const fields = raw.slice(raw.lastIndexOf(")") + 2).split(" ");
      if (fields[0] !== "Z" && fields[0] !== "X") rows.push({ pid: Number(entry), parent: Number(fields[1]), group: Number(fields[2]), start: fields[19] });
    } catch (error) { if (error.code !== "ENOENT" && error.code !== "ESRCH") throw error; }
  }
  return rows;
}
async function stopGroup(identity) {
  if (!identity) return;
  // Candidate owners and Eva backends preserve this group, even if Electron
  // exits first. Also capture descendants that explicitly detach (the ordinary
  // non-candidate Eva default); start times protect against PID reuse.
  const current = processIdentity(identity.pid);
  if (current && current.start !== identity.start) throw new Error("Candidate process identity changed; refusing to signal a reused PID");
  const owned = new Map();
  const liveOwned = () => {
    const rows = processTable();
    let changed;
    do {
      changed = false;
      for (const row of rows) {
        if (owned.get(row.pid)?.start === row.start) continue;
        if (row.group === identity.pid || rows.some((parent) => parent.pid === row.parent && owned.get(parent.pid)?.start === parent.start)) {
          owned.set(row.pid, row); changed = true;
        }
      }
    } while (changed);
    return rows.filter((row) => owned.get(row.pid)?.start === row.start);
  };
  const signal = (row, value) => {
    if (!alive(row)) return;
    try { process.kill(row.pid, value); } catch (error) { if (error.code !== "ESRCH") throw error; }
  };
  const terminated = new Set(), deadline = Date.now() + 1500;
  while (true) {
    const live = liveOwned();
    if (!live.length) return;
    for (const row of live) if (!terminated.has(`${row.pid}:${row.start}`)) {
      signal(row, "SIGTERM"); terminated.add(`${row.pid}:${row.start}`);
    }
    if (Date.now() >= deadline) break;
    await sleep(30);
  }
  const forceDeadline = Date.now() + 5000;
  while (true) {
    const live = liveOwned();
    if (!live.length) return;
    assert(Date.now() < forceDeadline, "Candidate processes did not stop; refusing overlapping rollback launch");
    for (const row of live) signal(row, "SIGKILL");
    await sleep(30);
  }
}
async function launch(journal, loc, mode) {
  const key = mode === "candidate" ? "candidateProcess" : "rollbackProcess";
  const report = path.join(loc.directory, `${mode}-launch.json`);
  if (!alive(journal[key] ?? null)) {
    if (journal[key]) await stopGroup(journal[key]);
    if (mode === "candidate") remove(loc.ack);
    remove(report);
    const child = spawn(loc.nodePath, [loc.helperPath, "--launch", loc.journalPath, mode], { detached: true, stdio: "ignore", env: process.env });
    await new Promise((resolve, reject) => { child.once("spawn", resolve); child.once("error", reject); });
    const identity = processIdentity(child.pid);
    assert(identity, "Desktop launch helper exited before registration");
    // --launch waits for this durable registration before executing the image.
    save(journal, loc, mode === "candidate" ? "launching" : "rolling-back", { [key]: identity });
    child.unref();
  }
  const deadline = Date.now() + 10_000;
  while (!existsSync(report)) {
    assert(alive(journal[key]), "Desktop launch helper exited before starting the AppImage");
    assert(Date.now() < deadline, "Desktop AppImage launch timed out");
    await sleep(25);
  }
  const result = json(report);
  assert(result.transactionId === journal.transactionId && result.mode === mode, "Desktop launch report identity mismatch");
  if (result.error) throw new Error(result.error);
}
async function launchChild(journalPath, mode) {
  assert(mode === "candidate" || mode === "rollback", "Invalid desktop launch helper mode");
  const key = mode === "candidate" ? "candidateProcess" : "rollbackProcess";
  const deadline = Date.now() + 10_000;
  let journal, loc;
  do {
    ({ journal, loc } = readJournal(journalPath));
    if (journal[key]?.pid === process.pid && alive(journal[key])) break;
    assert(Date.now() < deadline, "Desktop launch helper was not registered");
    await sleep(25);
  } while (true);
  const report = path.join(loc.directory, `${mode}-launch.json`);
  assert(hashFile(journal.target) === (mode === "candidate" ? journal.manifest.sha256 : journal.originalSha256), "Desktop artifact changed before launch");
  const child = spawn(journal.target, [], { env: launchEnvironment(journal, loc, mode), stdio: "ignore" });
  child.once("spawn", () => atomicWrite(report, JSON.stringify({ transactionId: journal.transactionId, mode, pid: child.pid })));
  child.once("error", (error) => atomicWrite(report, JSON.stringify({ transactionId: journal.transactionId, mode, error: errorText(error) })));
  await new Promise((resolve) => { child.once("exit", resolve); child.once("error", resolve); });
}
async function acquireRunner(loc) {
  const identity = { ...processIdentity(process.pid), token: randomUUID() };
  const directory = path.join(loc.directory, "runner-claims");
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  assert(realpathSync(directory) === directory, "Desktop runner claims must not traverse a symlink");
  const claim = path.join(directory, `${identity.pid}-${identity.token}.json`);
  const contenders = () => readdirSync(directory).filter((name) => name.endsWith(".json")).flatMap((name) => {
    const file = path.join(directory, name);
    try {
      const record = json(file);
      assert(record && (record.ticket === null || (Number.isSafeInteger(record.ticket) && record.ticket > 0)), "Malformed desktop runner claim");
      return alive(record) ? [{ ...record, file }] : [];
    } catch (error) { if (error.code === "ENOENT") return []; throw error; }
  });
  // Lamport's choosing/ticket gate serializes the short stale-owner replacement.
  // Unique claim paths are never reclaimed or reused; dead owners can therefore
  // be ignored without a check-dead/unlink race against a new live owner.
  atomicWrite(claim, JSON.stringify({ ...identity, ticket: null }));
  try {
    const ticket = Math.max(0, ...contenders().map((item) => item.ticket ?? 0)) + 1;
    atomicWrite(claim, JSON.stringify({ ...identity, ticket }));
    while (contenders().some((item) => item.file !== claim && (item.ticket === null || item.ticket < ticket || (item.ticket === ticket && item.file < claim)))) await sleep(10);
    if (existsSync(loc.runner)) assert(!alive(json(loc.runner)), "Desktop update helper is already running");
    atomicWrite(loc.runner, JSON.stringify(identity));
  } finally { remove(claim); }
  return () => {
    if (existsSync(loc.runner) && json(loc.runner).token === identity.token) remove(loc.runner);
  };
}
function restoreLauncher(journal) {
  const state = journal.launcherState;
  if (state.exists) atomicWrite(state.path, Buffer.from(state.data, "base64"), state.mode);
  else { remove(state.path); syncDirectory(path.dirname(state.path)); }
}
async function rollback(journal, loc, cause) {
  save(journal, loc, "rolling-back", { error: journal.error || errorText(cause) });
  await stopGroup(journal.candidateProcess);
  const backupHash = maybeHash(loc.backup), targetHash = maybeHash(journal.target);
  if (alive(journal.oldProcess) && !journal.candidateProcess && backupHash === null && targetHash === journal.originalSha256) {
    // No activation happened. A still-running original may have legitimately
    // changed its launcher state since preparation; do not rewind it.
    save(journal, loc, "rolled-back"); terminalCleanup(journal, loc); return;
  }
  if (backupHash !== null) {
    assert(backupHash === journal.originalSha256, "Previous desktop backup failed integrity verification");
    assert(targetHash === null || targetHash === journal.manifest.sha256 || targetHash === journal.originalSha256, "Desktop target was externally changed; refusing to overwrite it");
    renameSync(loc.backup, journal.target); syncDirectory(path.dirname(journal.target));
  } else assert(targetHash === journal.originalSha256, "Original desktop backup is missing; recovery needs attention");
  restoreLauncher(journal);
  if (!alive(journal.oldProcess)) await launch(journal, loc, "rollback");
  save(journal, loc, "rolled-back");
  terminalCleanup(journal, loc);
}

/** Idempotent recovery entry point. Re-run the same journal after a helper crash. */
export async function runDesktopUpdate(journalPath, { waitPid } = {}) {
  let { journal, loc } = readJournal(journalPath);
  const releaseRunner = await acquireRunner(loc);
  try {
    ({ journal } = readJournal(journalPath));
    if (waitPid !== undefined) {
      assert(Number.isInteger(waitPid) && waitPid > 0 && waitPid !== process.pid, "Invalid recovery handoff PID");
      const recoveryOwner = processIdentity(waitPid), deadline = Date.now() + journal.exitTimeoutMs;
      while (alive(recoveryOwner)) {
        assert(Date.now() < deadline, "Recovery desktop did not exit before activation timeout");
        await sleep(30);
      }
    }
    if (FINAL.has(journal.status)) { terminalCleanup(journal, loc); return journal; }
    assert(ownsLock(loc, journal.transactionId), "Desktop update no longer owns its active journal lock");
    try {
      if (journal.status === "preparing") throw new Error("Desktop preparation was interrupted");
      if (["rolling-back", "recovery-required"].includes(journal.status)) { await rollback(journal, loc, journal.error); return journal; }
      if (["prepared", "waiting-for-exit"].includes(journal.status)) {
        const exitDeadline = journal.exitDeadline ?? Date.now() + journal.exitTimeoutMs;
        save(journal, loc, "waiting-for-exit", { exitDeadline });
        while (alive(journal.oldProcess)) {
          assert(Date.now() < exitDeadline, "Running desktop did not exit before the activation timeout");
          await sleep(30);
        }
        assert(hashFile(journal.target) === journal.originalSha256, "Outer AppImage changed while waiting for shutdown");
        save(journal, loc, "swapping");
      }
      if (journal.status === "swapping") {
        const backupHash = maybeHash(loc.backup), targetHash = maybeHash(journal.target);
        if (backupHash === null) {
          assert(targetHash === journal.originalSha256 && hashFile(loc.stage) === journal.manifest.sha256, "Desktop artifacts changed before activation");
          // A hard-linked backup followed by one atomic replacement keeps the
          // outer launch path present even if power fails between operations.
          linkSync(journal.target, loc.backup); syncDirectory(path.dirname(journal.target));
        } else assert(backupHash === journal.originalSha256, "Previous desktop backup failed integrity verification");
        if (targetHash === journal.originalSha256 || targetHash === null) {
          assert(hashFile(loc.stage) === journal.manifest.sha256, "Staged AppImage failed integrity verification");
          renameSync(loc.stage, journal.target); syncDirectory(path.dirname(journal.target));
        }
        assert(hashFile(journal.target) === journal.manifest.sha256, "Installed AppImage failed integrity verification");
        save(journal, loc, "installed");
      }
      const readyDeadline = journal.readyDeadline ?? Date.now() + journal.timeoutMs;
      save(journal, loc, journal.status, { readyDeadline });
      await launch(journal, loc, "candidate");
      save(journal, loc, "awaiting-ready");
      while (!validAck(journal, loc)) {
        assert(alive(journal.candidateProcess), "Updated desktop exited before becoming ready");
        assert(Date.now() < readyDeadline, "Updated desktop readiness timed out");
        await sleep(30);
      }
      save(journal, loc, "completed");
      terminalCleanup(journal, loc);
    } catch (error) {
      try { await rollback(journal, loc, error); }
      catch (recoveryError) {
        save(journal, loc, "recovery-required", { error: `${errorText(error)}; rollback failed: ${errorText(recoveryError)}` });
        throw recoveryError;
      }
    }
    return journal;
  } finally { releaseRunner(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === SELF) {
  const [operation, journalPath, mode] = process.argv.slice(2);
  const task = operation === "--run" ? runDesktopUpdate(journalPath, mode === undefined ? {} : { waitPid: Number(mode) })
    : operation === "--launch" ? launchChild(journalPath, mode) : Promise.reject(new Error("Use --run JOURNAL or --launch JOURNAL MODE"));
  task.catch((error) => { console.error(errorText(error)); process.exitCode = 1; });
}
