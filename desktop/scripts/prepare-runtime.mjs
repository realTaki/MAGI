import { execFileSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
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
  cpSync(path.join(temporary, python.name), path.join(RUNTIME_DIR, "python"), {
    recursive: true,
  });
  cpSync(uv, path.join(RUNTIME_DIR, "bin", process.platform === "win32" ? "uv.exe" : "uv"));
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
