import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_DIR = path.join(DESKTOP_ROOT, "runtime");

function bundledBunSource() {
  const platform = process.platform === "win32" ? "windows" : process.platform;
  const architecture = process.arch === "arm64" ? "aarch64" : process.arch;
  const executable = process.platform === "win32" ? "bun.exe" : "bun";
  const candidates = [
    path.join(DESKTOP_ROOT, "node_modules", "bun", "bin", "bun.exe"),
    path.join(DESKTOP_ROOT, "node_modules", "@oven", `bun-${platform}-${architecture}`, "bin", executable),
  ];
  const found = candidates.find(existsSync);
  if (!found) throw new Error(`bun does not support this build platform: ${platform}-${architecture}`);
  return found;
}

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
  cpSync(bundledBunSource(), path.join(RUNTIME_DIR, "bin", process.platform === "win32" ? "bun.exe" : "bun"));

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
