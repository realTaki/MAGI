/**
 * The replace safety gate and the self_update skill both call this npm entry.
 * Isolation, npm ci, test, and build live in scripts/verify.mjs.
 */

import { execFile } from "node:child_process";
import { resolveToolchain } from "./toolchain.js";

export const VERIFY_NPM_ARGS = ["run", "verify"] as const;

export async function verifyCheckout(root: string, env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const { node, npm } = resolveToolchain(env);
  await new Promise<void>((resolve, reject) => {
    execFile(node, [npm, ...VERIFY_NPM_ARGS], { cwd: root, env }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${stderr}${stdout}`.trim() || error.message));
      else resolve();
    });
  });
}
