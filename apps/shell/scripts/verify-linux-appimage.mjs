import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import { extractFile, listPackage } from "@electron/asar";

const artifact = path.resolve(process.argv[2] ?? "");
assert.equal(process.platform, "linux", "AppImage must be verified on native Linux");
assert.equal(process.arch, "x64");
const dir = mkdtempSync(path.join(tmpdir(), "magi-appimage-verify-"));
try {
  execFileSync(artifact, ["--appimage-extract"], { cwd: dir, stdio: "ignore" });
  const appDir = path.join(dir, "squashfs-root");
  const resources = path.join(appDir, "resources");
  const archive = path.join(resources, "app.asar");
  const entries = listPackage(archive);
  for (const file of ["/main.mjs", "/process.mjs", "/process-owner.mjs", "/installation.mjs", "/profile-layout.mjs", "/app-spec.mjs", "/dependency-state.mjs", "/runtime-environment.mjs", "/shell-path.mjs", "/preload.cjs"]) assert.ok(entries.includes(file), `${file} missing from package`);
  const helper = path.join(dir, "shell-path.mjs");
  writeFileSync(helper, extractFile(archive, "shell-path.mjs"));
  const probe = path.join(dir, "probe.mjs");
  writeFileSync(probe, `import {shellArtifactPath} from ${JSON.stringify(pathToFileURL(helper).href)}; console.log(JSON.stringify({platform:process.platform,arch:process.arch,electron:process.versions.electron,execPath:process.execPath,appImage:process.env.APPIMAGE,artifactPath:shellArtifactPath({platform:process.platform,execPath:process.execPath,appImage:process.env.APPIMAGE})}));`);
  const environment = { ...process.env, ELECTRON_RUN_AS_NODE: "1", APPIMAGE: artifact, APPDIR: appDir, LD_LIBRARY_PATH: [path.join(appDir, "usr", "lib"), process.env.LD_LIBRARY_PATH ?? ""].filter(Boolean).join(":") };
  const result = JSON.parse(execFileSync(path.join(appDir, "MAGI"), [probe], { env: environment, encoding: "utf8" }));
  assert.equal(result.platform, "linux"); assert.equal(result.arch, "x64");
  assert.equal(result.electron, "44.4.5");
  assert.notEqual(result.execPath, artifact, "Electron runs inside the extracted AppDir");
  assert.equal(result.artifactPath, artifact, "updates must target the outer artifact");
  const node = path.join(resources, "runtime", "bin", "node");
  const nodeVersion = execFileSync(node, ["--version"], { encoding: "utf8" }).trim();
  const npmVersion = execFileSync(node, [path.join(resources, "runtime", "npm", "node_modules", "npm", "bin", "npm-cli.js"), "--version"], { encoding: "utf8" }).trim();
  const git = path.join(resources, "app.asar.unpacked", "node_modules", "dugite", "git", "bin", "git");
  const gitVersion = execFileSync(git, ["--version"], { encoding: "utf8" }).trim();
  assert.equal(nodeVersion, "v24.21.0"); assert.equal(npmVersion, "12.0.2");
  for (const binary of [node, git, path.join(appDir, "MAGI")]) {
    const header = readFileSync(binary).subarray(0, 20);
    assert.equal(header.subarray(0, 4).toString("hex"), "7f454c46");
    assert.equal(header.readUInt16LE(18), 62, "packaged executable must be Linux x86-64");
  }
  const metadata = { artifact: path.basename(artifact), bytes: statSync(artifact).size, sha256: createHash("sha256").update(readFileSync(artifact)).digest("hex"), ...result, nodeVersion, npmVersion, gitVersion, verification: "native Linux; official extraction without FUSE; embedded Electron Node mode; no GUI" };
  console.log(JSON.stringify(metadata, null, 2));
} finally { rmSync(dir, { recursive: true, force: true }); }
