import { execFile } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, realpathSync, statSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { magiHome } from "./home.js";
import { resolveToolchain } from "./toolchain.js";

function run(file: string, args: string[], cwd: string, env: NodeJS.ProcessEnv = process.env): Promise<string> {
  const { node } = resolveToolchain();
  const command = file === "npm" ? node : file;
  const argv = file === "npm" ? [resolveToolchain().npm, ...args] : args;
  return new Promise((resolve, reject) => {
    execFile(command, argv, { cwd, env }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${stderr}${stdout}`.trim() || error.message));
      else resolve(String(stdout));
    });
  });
}

/** The branch this checkout is configured to follow. Missing upstream is an error. */
export async function trackingRef(root: string): Promise<string> {
  const { git } = resolveToolchain();
  let upstream = "";
  try {
    upstream = (await run(git, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], root)).trim();
  } catch {
    upstream = "";
  }
  if (!upstream || upstream === "@{upstream}") throw new Error("this checkout has no tracking branch");
  return upstream;
}

/** Capture only Git-visible source using a private index; do not move HEAD or refs. */
async function localRevision(root: string, previous: string): Promise<string> {
  const { git } = resolveToolchain();
  const top = (await run(git, ["rev-parse", "--show-toplevel"], root)).trim();
  const canonical = (value: string) => {
    const resolved = realpathSync.native(value);
    return process.platform === "win32" ? resolved.toLowerCase() : resolved;
  };
  if (canonical(top) !== canonical(root)) {
    // Windows Git can expand an 8.3 TEMP path while Node retains the alias.
    // Verify the directory itself; never accept a child directory as the root.
    const a = statSync(top, { bigint: true }), b = statSync(root, { bigint: true });
    if (!a.isDirectory() || !b.isDirectory() || a.ino === 0n || a.ino !== b.ino || a.dev !== b.dev) throw new Error("local rebuild requires the source checkout root");
  }
  const temporary = mkdtempSync(join(tmpdir(), "magi-source-index-"));
  const env = { ...process.env, GIT_INDEX_FILE: join(temporary, "index") };
  try {
    await run(git, ["read-tree", previous], root, env);
    await run(git, ["add", "--all", "--", "."], root, env);
    const tree = (await run(git, ["write-tree"], root, env)).trim();
    // This private commit is an artifact identity, never a commit on the user's
    // branch. No Git config, index, ref, upstream, or remote is changed.
    return (await run(git, ["-c", "user.name=MAGI", "-c", "user.email=local-revision@magi.invalid", "commit-tree", tree, "-p", previous, "-m", "Local MAGI source revision"], root, env)).trim();
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
}

/**
 * With no target, build a snapshot of this editable source tree (including
 * non-ignored new files and deletions). An explicit ref is a remote upgrade:
 * fetch and build that committed ref, independently of local edits.
 * Install, test and build before exposing the revision to the launcher.
 */
export async function reviseCheckout(root: string, target?: string): Promise<{ revision: string; previous: string; dir: string }> {
  const ref = target?.trim();
  if (target !== undefined && !ref) throw new Error("no revision was requested");
  const { git } = resolveToolchain();
  const name = process.env.MAGI_NODE_NAME?.trim() || "agent";
  const previous = (await run(git, ["rev-parse", "HEAD"], root)).trim();
  let revision: string;
  if (ref) {
    await run(git, ["fetch", "--prune", "origin"], root);
    try { revision = (await run(git, ["rev-parse", "--verify", `${ref}^{commit}`], root)).trim(); }
    catch (error) { throw new Error(`revision ${ref} is missing: ${error instanceof Error ? error.message : String(error)}`); }
  } else {
    revision = await localRevision(root, previous);
  }
  if (!/^[0-9a-f]+$/i.test(revision)) throw new Error(`revision ${revision} is not a commit`);
  const dir = join(magiHome(), name, "revisions", revision);
  const live = revision === process.env.MAGI_REVISION || revision === process.env.MAGI_PREVIOUS_REVISION;
  if (live && existsSync(dir)) return { revision, previous, dir };
  const staging = `${dir}.building`;
  rmSync(staging, { recursive: true, force: true });
  mkdirSync(join(magiHome(), name, "revisions"), { recursive: true });
  let materialized = false;
  try {
    await run(git, ["clone", "--local", "--no-hardlinks", root, staging], root);
    await run(git, ["checkout", "--detach", revision], staging);
    // Install at the permanent path: Windows workspace junctions (and some
    // build output) retain absolute paths and would break after a final rename.
    if (!live) rmSync(dir, { recursive: true, force: true });
    renameSync(staging, dir);
    materialized = true;
    await run("npm", ["ci"], dir);
    const validationHome = mkdtempSync(join(tmpdir(), "magi-revision-tests-"));
    const testEnv = { ...process.env };
    for (const key of Object.keys(testEnv)) {
      if (key.startsWith("MAGI_") && !["MAGI_NODE", "MAGI_NPM", "MAGI_GIT"].includes(key)) delete testEnv[key];
    }
    testEnv.MAGI_HOME = validationHome;
    testEnv.MAGI_OS_HOME = validationHome;
    delete testEnv.NODE_TEST_CONTEXT;
    try { await run("npm", ["test"], dir, testEnv); }
    finally { rmSync(validationHome, { recursive: true, force: true }); }
    await run("npm", ["run", "build"], dir);
    if (process.env.MAGI_ROLE === "shell") {
      await run("npm", ["run", "build:ui", "--prefix", "apps/shell"], dir);
      if (!existsSync(join(dir, "apps", "shell", "dist", "index.html"))) throw new Error("Shell build produced no UI entry");
    }
  } catch (error) {
    rmSync(staging, { recursive: true, force: true });
    if (materialized) rmSync(dir, { recursive: true, force: true });
    throw error;
  }
  return { revision, previous, dir };
}
