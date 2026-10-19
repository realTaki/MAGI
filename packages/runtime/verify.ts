/**
 * The compile and test commands the replace safety gate runs. The self_update
 * skill tells the agent to run these same npm invocations in its own checkout
 * with MAGI_NODE / MAGI_NPM before requesting a replace.
 */

import { execFile } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveToolchain } from "./toolchain.js";

export const VERIFY_TEST_ARGS = ["test"] as const;
export const VERIFY_BUILD_ARGS = ["run", "build"] as const;
export const VERIFY_UI_ARGS = ["run", "build:ui", "--prefix", "apps/shell"] as const;

export async function verifyCheckout(root: string, env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const validationHome = mkdtempSync(join(tmpdir(), "magi-revision-tests-"));
  const testEnv = { ...env };
  for (const key of Object.keys(testEnv)) {
    if (key.startsWith("MAGI_") && !["MAGI_NODE", "MAGI_NPM", "MAGI_GIT"].includes(key)) delete testEnv[key];
  }
  testEnv.MAGI_HOME = validationHome;
  testEnv.MAGI_OS_HOME = validationHome;
  delete testEnv.NODE_TEST_CONTEXT;
  try {
    await npmRun([...VERIFY_TEST_ARGS], root, testEnv);
  } finally {
    rmSync(validationHome, { recursive: true, force: true });
  }
  await npmRun([...VERIFY_BUILD_ARGS], root, env);
  if (env.MAGI_ROLE === "shell") {
    await npmRun([...VERIFY_UI_ARGS], root, env);
    if (!existsSync(join(root, "apps", "shell", "dist", "index.html"))) throw new Error("Shell build produced no UI entry");
  }
}

function npmRun(args: string[], cwd: string, env: NodeJS.ProcessEnv): Promise<void> {
  const { node, npm } = resolveToolchain(env);
  return new Promise((resolve, reject) => {
    execFile(node, [npm, ...args], { cwd, env }, (error, stdout, stderr) => {
      if (error) reject(new Error(`${stderr}${stdout}`.trim() || error.message));
      else resolve();
    });
  });
}
