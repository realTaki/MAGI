import { execFileSync } from "node:child_process";
import {
  cpSync,
  mkdirSync,
  rmSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_DIR = path.join(DESKTOP_ROOT, "runtime");
const nodePackageDir = path.dirname(createRequire(import.meta.url).resolve("node/package.json"));
const nodeBin = process.platform === "win32" ? "node.exe" : "node";

rmSync(RUNTIME_DIR, { recursive: true, force: true });
mkdirSync(path.join(RUNTIME_DIR, "bin"), { recursive: true });
try {
  // The `node` package is a shell devDependency. npm workspaces hoist it to
  // the repository root, so look it up by package name rather than a path
  // under apps/shell/node_modules.
  cpSync(
    path.join(nodePackageDir, "bin", nodeBin),
    path.join(RUNTIME_DIR, "bin", nodeBin),
  );
  const npmArguments = [
    "install",
    "--prefix",
    path.join(RUNTIME_DIR, "npm"),
    "--omit=dev",
    "--ignore-scripts",
    "--no-package-lock",
    "npm@12.0.2",
  ];
  if (process.env.npm_execpath) {
    execFileSync(process.execPath, [process.env.npm_execpath, ...npmArguments], {
      stdio: "inherit",
    });
  } else {
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", npmArguments, {
      stdio: "inherit",
    });
  }
} catch (error) {
  rmSync(RUNTIME_DIR, { recursive: true, force: true });
  throw error;
}
