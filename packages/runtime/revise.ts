import { execFile } from "node:child_process";
import { mkdirSync, renameSync, rmSync } from "node:fs";
import { join } from "node:path";
import { magiHome } from "./home.js";
import { resolveToolchain } from "./toolchain.js";

function run(file: string, args: string[], cwd: string): Promise<string> {
  const { node } = resolveToolchain();
  const command = file === "npm" ? node : file;
  const argv = file === "npm" ? [resolveToolchain().npm, ...args] : args;
  return new Promise((resolve, reject) => {
    execFile(command, argv, { cwd, env: process.env }, (error, stdout, stderr) => {
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

/**
 * Check out `target` in a new directory under MAGI_HOME, then install, test,
 * and build there. A half-finished directory is never reused.
 */
export async function reviseCheckout(root: string, target: string): Promise<{ revision: string; previous: string; dir: string }> {
  const ref = target.trim();
  if (!ref) throw new Error("no revision was requested");
  const { git } = resolveToolchain();
  const name = process.env.MAGI_NODE_NAME?.trim() || "agent";
  const previous = (await run(git, ["rev-parse", "HEAD"], root)).trim();
  await run(git, ["fetch", "--prune", "origin"], root);
  let revision = "";
  try {
    revision = (await run(git, ["rev-parse", "--verify", ref], root)).trim();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`revision ${ref} is missing: ${detail}`);
  }
  if (!/^[0-9a-f]+$/i.test(revision)) throw new Error(`revision ${revision} is not a commit`);
  const dir = join(magiHome(), name, "revisions", revision);
  const staging = `${dir}.building`;
  rmSync(staging, { recursive: true, force: true });
  mkdirSync(join(magiHome(), name, "revisions"), { recursive: true });
  try {
    await run(git, ["clone", "--local", root, staging], root);
    await run(git, ["checkout", "--detach", revision], staging);
    await run("npm", ["ci"], staging);
    await run("npm", ["test"], staging);
    await run("npm", ["run", "build"], staging);
    if (process.env.MAGI_ROLE === "shell") await run("npm", ["run", "build:ui", "--prefix", "apps/shell"], staging);
  } catch (error) {
    rmSync(staging, { recursive: true, force: true });
    throw error;
  }
  rmSync(dir, { recursive: true, force: true });
  renameSync(staging, dir);
  return { revision, previous, dir };
}
