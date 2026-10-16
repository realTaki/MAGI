import { execFile } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
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

/**
 * Check out the target revision in its own directory under MAGI_HOME, then
 * install, test, and build there. The running tree is left on its current commit.
 */
export async function reviseCheckout(root: string, target?: string): Promise<{ revision: string; previous: string; dir: string }> {
  const { git } = resolveToolchain();
  const name = process.env.MAGI_NODE_NAME?.trim() || "agent";
  const previous = (await run(git, ["rev-parse", "HEAD"], root)).trim();
  await run(git, ["fetch", "--prune", "origin"], root);
  const revision = (await run(git, ["rev-parse", "--verify", target?.trim() || "origin/HEAD"], root)).trim();
  if (!/^[0-9a-f]+$/i.test(revision)) throw new Error(`revision ${revision} is not a commit`);
  const dir = join(magiHome(), name, "revisions", revision);
  if (!existsSync(join(dir, ".git"))) {
    mkdirSync(join(magiHome(), name, "revisions"), { recursive: true });
    await run(git, ["clone", "--local", root, dir], root);
  }
  await run(git, ["checkout", "--detach", revision], dir);
  await run("npm", ["ci"], dir);
  await run("npm", ["test"], dir);
  await run("npm", ["run", "build"], dir);
  return { revision, previous, dir };
}
