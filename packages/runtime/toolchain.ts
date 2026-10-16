import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

function existing(candidates: string[]): string {
  for (const candidate of candidates) {
    if (candidate !== "" && existsSync(candidate)) return candidate;
  }
  return "";
}

function resourcesPath(): string {
  const value = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath;
  return typeof value === "string" ? value : "";
}

function bundledNode(): string {
  const name = process.platform === "win32" ? "node.exe" : "node";
  const resources = resourcesPath()
    ? join(resourcesPath(), "runtime", "bin", name)
    : "";
  return existing([
    resources,
    join(here, "..", "..", "..", "apps", "shell", "runtime", "bin", name),
  ]);
}

function bundledNpm(): string {
  const resources = resourcesPath()
    ? join(resourcesPath(), "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js")
    : "";
  return existing([
    resources,
    join(here, "..", "..", "..", "apps", "shell", "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js"),
  ]);
}

function bundledGit(): string {
  const name = process.platform === "win32" ? "git.exe" : "git";
  try {
    const require = createRequire(import.meta.url);
    const root = dirname(require.resolve("dugite/package.json"));
    return existing([join(root, "git", "bin", name)]);
  } catch {
    return "";
  }
}

/** MAGI_NODE / MAGI_NPM / MAGI_GIT, then the bundled runtime and dugite, then PATH. */
export function resolveToolchain(env: NodeJS.ProcessEnv = process.env): { node: string; npm: string; git: string } {
  return {
    node: env.MAGI_NODE?.trim() || bundledNode() || "node",
    npm: env.MAGI_NPM?.trim() || bundledNpm() || "npm",
    git: env.MAGI_GIT?.trim() || bundledGit() || "git",
  };
}
