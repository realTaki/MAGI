import { execFileSync } from "node:child_process";
import {
  cpSync,
  mkdirSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_DIR = path.join(DESKTOP_ROOT, "runtime");

rmSync(RUNTIME_DIR, { recursive: true, force: true });
mkdirSync(path.join(RUNTIME_DIR, "bin"), { recursive: true });
try {
  cpSync(
    path.join(
      DESKTOP_ROOT,
      "node_modules",
      "node",
      "bin",
      process.platform === "win32" ? "node.exe" : "node",
    ),
    path.join(RUNTIME_DIR, "bin", process.platform === "win32" ? "node.exe" : "node"),
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
