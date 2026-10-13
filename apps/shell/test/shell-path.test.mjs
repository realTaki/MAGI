import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { shellArtifactPath } from "../shell-path.mjs";
import { updateScript } from "../update-script.mjs";

test("Linux update resolves outer AppImage rather than extracted Electron", () => {
  assert.equal(shellArtifactPath({ platform: "linux", execPath: "/tmp/appimage_extracted_123/MAGI", appImage: "/home/user/MAGI.AppImage" }), "/home/user/MAGI.AppImage");
  for (const appImage of [undefined, "relative.AppImage", "/tmp/appimage_extracted_123/MAGI"]) {
    assert.throws(() => shellArtifactPath({ platform: "linux", execPath: "/tmp/appimage_extracted_123/MAGI", appImage }), /outer APPIMAGE/);
  }
  assert.equal(shellArtifactPath({ platform: "darwin", execPath: "/Applications/MAGI.app/Contents/MacOS/MAGI" }), "/Applications/MAGI.app");
  assert.equal(shellArtifactPath({ platform: "win32", execPath: "C:\\MAGI\\MAGI.exe" }), "C:\\MAGI\\MAGI.exe");
});

test("Linux swap changes only the outer file and relaunches without FUSE", { skip: process.platform !== "linux" }, (t) => {
  const directory = mkdtempSync(path.join(tmpdir(), "magi-appimage-swap-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const outer = path.join(directory, "MAGI's original.AppImage"), inner = path.join(directory, "inner MAGI"), installer = path.join(directory, "new AppImage");
  const marker = path.join(directory, "launched");
  writeFileSync(outer, "old image"); writeFileSync(inner, "inner stays intact");
  writeFileSync(installer, `#!/bin/sh\nprintf '%s' "$APPIMAGE_EXTRACT_AND_RUN" > '${marker}'\n`);
  const script = path.join(directory, "swap.sh");
  writeFileSync(script, updateScript({ platform: "linux", pid: 2147483647, installer, appBundle: shellArtifactPath({ platform: "linux", execPath: inner, appImage: outer }) }));
  execFileSync("/bin/sh", [script]);
  assert.equal(readFileSync(inner, "utf8"), "inner stays intact");
  assert.equal(readFileSync(outer, "utf8"), readFileSync(installer, "utf8"));
  assert.equal(readFileSync(marker, "utf8"), "1");
});
