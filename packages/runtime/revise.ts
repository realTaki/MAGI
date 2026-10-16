import { execFile } from "node:child_process";
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
 * This node upgrades its own checkout: fetch, install, test, build.
 * The caller then asks the launcher to switch to the resulting revision.
 */
export async function reviseCheckout(root: string): Promise<{ revision: string; previous: string }> {
  const { git } = resolveToolchain();
  const previous = (await run(git, ["rev-parse", "HEAD"], root)).trim();
  await run(git, ["fetch", "--prune", "origin"], root);
  await run("npm", ["ci", "--ignore-scripts"], root);
  await run("npm", ["test"], root);
  await run("npm", ["run", "build"], root);
  const revision = (await run(git, ["rev-parse", "HEAD"], root)).trim();
  return { revision, previous };
}
