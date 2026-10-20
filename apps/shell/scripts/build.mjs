import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const shellDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

/** Every stage is synchronous: a failed prerequisite must prevent packaging. */
export function buildDesktop({
  directory = shellDirectory,
  args = process.argv.slice(2),
  env = process.env,
  platform = process.platform,
  run = execFileSync,
  exists = existsSync,
  resolve = (name) => require.resolve(name),
} = {}) {
  const runtimeBin = path.join(directory, "runtime", "bin");
  const nodeName = platform === "win32" ? "node.exe" : "node";
  const node = path.join(runtimeBin, nodeName);
  // Bootstrap only with an existing MAGI-owned runtime, never process.execPath
  // (which could be a system Node supplied by a caller).
  const bootstrapNode = exists(node) ? node : path.join(path.dirname(resolve("node/package.json")), "bin", nodeName);
  if (!exists(bootstrapNode)) throw new Error("MAGI-owned Node is missing; install the repository dependencies before packaging.");
  run(bootstrapNode, [path.join(directory, "scripts", "prepare-runtime.mjs")], { cwd: directory, stdio: "inherit" });
  const options = { env: { ...env, PATH: [runtimeBin, env.PATH ?? ""].join(path.delimiter) }, stdio: "inherit" };
  const npm = path.join(directory, "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js");
  run(node, [npm, "run", "build", "--", "--force"], { ...options, cwd: path.resolve(directory, "../..") });
  run(node, [npm, "run", "build:ui"], { ...options, cwd: directory });
  run(node, [resolve("electron-builder/out/cli/cli.js"), ...args], { ...options, cwd: directory });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) buildDesktop();
