import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, delimiter } from "node:path";
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

const nodeVersions = new Map<string, string>();

/** MAGI_NODE / MAGI_NPM / MAGI_GIT, then the bundled runtime and dugite, then PATH. Node must be 24 or newer. */
export function resolveToolchain(env: NodeJS.ProcessEnv = process.env): { node: string; npm: string; git: string } {
  const node = env.MAGI_NODE?.trim() || bundledNode() || "node";
  requireNode24(node);
  return {
    node,
    npm: env.MAGI_NPM?.trim() || bundledNpm() || "npm",
    git: env.MAGI_GIT?.trim() || bundledGit() || "git",
  };
}

/** Env for agent bash and the replace safety gate: same Node/npm/git, Node's directory on PATH. */
export function toolchainEnv(env: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const { node, npm, git } = resolveToolchain(env);
  return {
    ...env,
    MAGI_NODE: node,
    MAGI_NPM: npm,
    MAGI_GIT: git,
    PATH: `${dirname(node)}${delimiter}${env.PATH ?? ""}`,
  };
}

function requireNode24(binary: string): void {
  const version = nodeVersion(binary);
  const major = Number(version.split(".")[0]);
  if (!Number.isInteger(major) || major < 24) {
    throw new Error(`MAGI requires Node >= 24 for node:sqlite (${binary} is ${version})`);
  }
}

function nodeVersion(binary: string): string {
  if (binary === process.execPath) return process.versions.node;
  const cached = nodeVersions.get(binary);
  if (cached !== undefined) return cached;
  let version: string;
  try {
    version = execFileSync(binary, ["-p", "process.versions.node"], { encoding: "utf8", timeout: 10_000 }).trim();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`MAGI requires Node >= 24, and ${binary} did not report a version (${message})`);
  }
  nodeVersions.set(binary, version);
  return version;
}
