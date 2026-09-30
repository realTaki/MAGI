import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const shellDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtimeBin = path.join(shellDirectory, "runtime", "bin");
const node = path.join(runtimeBin, process.platform === "win32" ? "node.exe" : "node");
const require = createRequire(import.meta.url);

execFileSync(process.execPath, [path.join(shellDirectory, "scripts", "prepare-runtime.mjs")], {
  cwd: shellDirectory,
  stdio: "inherit",
});
execFileSync(node, [require.resolve("electron-builder/out/cli/cli.js"), ...process.argv.slice(2)], {
  cwd: shellDirectory,
  env: { ...process.env, PATH: [runtimeBin, process.env.PATH ?? ""].join(path.delimiter) },
  stdio: "inherit",
});
