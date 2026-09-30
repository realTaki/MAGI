import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_DIR = path.join(DESKTOP_ROOT, "runtime");
const NPM_PINNED_VERSION = "12.0.2";
const nodePackageDir = path.dirname(createRequire(import.meta.url).resolve("node/package.json"));
const nodeBin = process.platform === "win32" ? "node.exe" : "node";
const expectedNodeVersion = JSON.parse(
  readFileSync(path.join(nodePackageDir, "package.json"), "utf8"),
).version;
const packagedNode = path.join(nodePackageDir, "bin", nodeBin);
// MAGI.app keeps npm under `runtime/npm`, unlike the conventional Node layout
// expected by the `node` npm package's install script. During a local rebuild,
// use the explicitly invoked bundled Node if that package has no downloaded bin.
const sourceNode = existsSync(packagedNode) ? packagedNode : process.execPath;
const sourceNodeVersion = execFileSync(sourceNode, ["--version"], { encoding: "utf8" }).trim().replace(/^v/, "");
if (sourceNodeVersion !== expectedNodeVersion) {
  throw new Error(`Node ${expectedNodeVersion} is required to prepare the runtime; found ${sourceNodeVersion}`);
}
const npmPackageJson = path.join(
  RUNTIME_DIR,
  "npm",
  "node_modules",
  "npm",
  "package.json",
);
const runtimeNpmCommand = path.join(RUNTIME_DIR, "bin", process.platform === "win32" ? "npm.cmd" : "npm");
const bundledNpmCli = path.join(
  path.dirname(path.dirname(process.execPath)),
  "npm",
  "node_modules",
  "npm",
  "bin",
  "npm-cli.js",
);
const inheritedNpmCli = process.env.npm_execpath ?? "";
const npmCli = [bundledNpmCli, inheritedNpmCli]
  .find((candidate) => candidate.endsWith("npm-cli.js") && existsSync(candidate));
// If the pinned npm is already prepared, reuse it. The runtime ships with the
// app and is content-addressed by the pinned npm version, so reinstalling it on
// every build only buys repeated downloads. Read the manifest directly rather
// than shelling out to `node -p` — `node -p` emits a bare version string, not
// JSON, so JSON.parse on its output would always throw and force a rebuild.
let pinnedNpmVersion;
let preparedNodeVersion;
try {
  pinnedNpmVersion = existsSync(npmPackageJson)
    ? JSON.parse(readFileSync(npmPackageJson, "utf8")).version
    : null;
} catch {
  pinnedNpmVersion = null;
}
try {
  preparedNodeVersion = existsSync(path.join(RUNTIME_DIR, "bin", nodeBin))
    ? execFileSync(path.join(RUNTIME_DIR, "bin", nodeBin), ["--version"], { encoding: "utf8" }).trim().replace(/^v/, "")
    : null;
} catch {
  preparedNodeVersion = null;
}
if (pinnedNpmVersion === NPM_PINNED_VERSION && preparedNodeVersion === expectedNodeVersion && existsSync(runtimeNpmCommand)) {
  console.log(`runtime already contains Node ${expectedNodeVersion} and npm@${NPM_PINNED_VERSION}; skipping prepare-runtime.`);
  process.exit(0);
}

rmSync(RUNTIME_DIR, { recursive: true, force: true });
mkdirSync(path.join(RUNTIME_DIR, "bin"), { recursive: true });
try {
  // The `node` package is a shell devDependency. npm workspaces hoist it to
  // the repository root, so look it up by package name rather than a path
  // under apps/shell/node_modules.
  cpSync(
    sourceNode,
    path.join(RUNTIME_DIR, "bin", nodeBin),
  );
  const npmArguments = [
    "install",
    "--prefix",
    path.join(RUNTIME_DIR, "npm"),
    "--omit=dev",
    "--ignore-scripts",
    "--no-package-lock",
    `npm@${NPM_PINNED_VERSION}`,
  ];
  if (npmCli) {
    execFileSync(process.execPath, [npmCli, ...npmArguments], {
      stdio: "inherit",
    });
  } else {
    execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", npmArguments, {
      stdio: "inherit",
    });
  }
  if (process.platform === "win32") {
    writeFileSync(
      runtimeNpmCommand,
      '@"%~dp0node.exe" "%~dp0..\\npm\\node_modules\\npm\\bin\\npm-cli.js" %*\r\n',
    );
  } else {
    writeFileSync(
      runtimeNpmCommand,
      '#!/usr/bin/env sh\nexec "$(dirname "$0")/node" "$(dirname "$0")/../npm/node_modules/npm/bin/npm-cli.js" "$@"\n',
      { mode: 0o755 },
    );
  }
} catch (error) {
  rmSync(RUNTIME_DIR, { recursive: true, force: true });
  throw error;
}
