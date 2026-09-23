import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readlinkSync,
  readdirSync,
  rmSync,
  symlinkSync,
  unlinkSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_DIR = path.join(DESKTOP_ROOT, "runtime");

function executableOnPath(name) {
  const names = process.platform === "win32" ? [`${name}.exe`, name] : [name];
  for (const directory of (process.env.PATH ?? "").split(path.delimiter)) {
    for (const candidate of names) {
      const resolved = path.join(directory, candidate);
      if (existsSync(resolved)) {
        return resolved;
      }
    }
  }
  throw new Error(`${name} is required to prepare the bundled runtime`);
}

function makeSymlinksRelative(directory, original, copied) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const item = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      makeSymlinksRelative(item, original, copied);
      continue;
    }
    if (!lstatSync(item).isSymbolicLink()) {
      continue;
    }
    const target = readlinkSync(item);
    if (!path.isAbsolute(target) || !target.startsWith(`${original}${path.sep}`)) {
      continue;
    }
    const rebased = path.join(copied, path.relative(original, target));
    unlinkSync(item);
    symlinkSync(path.relative(path.dirname(item), rebased), item);
  }
}

function removeDanglingSymlinks(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const item = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      removeDanglingSymlinks(item);
      continue;
    }
    if (!lstatSync(item).isSymbolicLink()) {
      continue;
    }
    if (!existsSync(item)) {
      unlinkSync(item);
    }
  }
}

const uv = process.env.MAGI_UV_BIN || executableOnPath("uv");
const temporary = mkdtempSync(path.join(os.tmpdir(), "magi-python-"));
try {
  execFileSync(
    uv,
    ["python", "install", "3.12", "--install-dir", temporary, "--no-bin", "--quiet"],
    { stdio: "inherit" },
  );
  const python = readdirSync(temporary, { withFileTypes: true }).find(
    (entry) => entry.isDirectory() && /^cpython-3\.12\.\d+-/.test(entry.name),
  );
  if (!python) {
    throw new Error("uv did not produce a Python 3.12 runtime");
  }

  rmSync(RUNTIME_DIR, { recursive: true, force: true });
  mkdirSync(path.join(RUNTIME_DIR, "bin"), { recursive: true });
  const originalPython = path.join(temporary, python.name);
  const copiedPython = path.join(RUNTIME_DIR, "python");
  cpSync(originalPython, copiedPython, {
    recursive: true,
  });
  makeSymlinksRelative(copiedPython, originalPython, copiedPython);
  removeDanglingSymlinks(copiedPython);
  cpSync(uv, path.join(RUNTIME_DIR, "bin", process.platform === "win32" ? "uv.exe" : "uv"));
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
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
