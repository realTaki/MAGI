import assert from "node:assert/strict";
import test from "node:test";

import {
  compareVersions,
  githubRepo,
  releaseAsset,
  shellRelease,
} from "../main/shell-update.ts";
import { updateScript } from "../../shell/update-script.mjs";

test("a newer release asset is chosen for this machine", () => {
  assert.deepEqual(githubRepo("https://github.com/AgenticSocietyLab/MAGI.git"), {
    owner: "AgenticSocietyLab",
    name: "MAGI",
  });
  assert.equal(compareVersions("0.1.0", "v0.2.0"), -1);
  assert.equal(compareVersions("v0.2.0", "0.2.0"), 0);
  assert.equal(compareVersions("1.0.0", "1.0.0-beta.1"), 1);
  const asset = releaseAsset(
    [
      { name: "MAGI-0.2.0-arm64.dmg", browser_download_url: "https://github.com/AgenticSocietyLab/MAGI/releases/download/v0.2.0/MAGI-0.2.0-arm64.dmg" },
      { name: "MAGI-0.2.0-x64.dmg", browser_download_url: "https://github.com/AgenticSocietyLab/MAGI/releases/download/v0.2.0/MAGI-0.2.0-x64.dmg" },
      { name: "MAGI-0.2.0-x64.exe", browser_download_url: "https://github.com/AgenticSocietyLab/MAGI/releases/download/v0.2.0/MAGI-0.2.0-x64.exe" },
    ],
    "darwin",
    "arm64",
  );
  assert.equal(asset?.name, "MAGI-0.2.0-arm64.dmg");
  assert.equal(asset.browser_download_url, "https://github.com/AgenticSocietyLab/MAGI/releases/download/v0.2.0/MAGI-0.2.0-arm64.dmg");
});

test("the app accepts its configured fork's release asset", async () => {
  const release = await shellRelease({
    repository: "https://github.com/example-user/MAGI.git",
    currentVersion: "0.1.0",
    packaged: true,
    platform: "darwin",
    arch: "arm64",
    fetchImpl: async () => Response.json({
      tag_name: "v0.2.0",
      html_url: "https://github.com/example-user/MAGI/releases/tag/v0.2.0",
      assets: [{ name: "MAGI-0.2.0-arm64.dmg", browser_download_url: "https://downloads.example/MAGI.dmg" }],
    }),
  });
  assert.equal(release.updateAvailable, true);
  assert.equal(release.assetUrl, "https://downloads.example/MAGI.dmg");
});

test("the swap script waits for this shell to exit", () => {
  const script = updateScript({
    platform: "darwin",
    pid: 42,
    installer: "/tmp/MAGI-0.2.0-arm64.dmg",
    mount: "/tmp/magi-shell-mount",
    appBundle: "/Applications/MAGI.app",
    stagedApp: "/tmp/magi-shell-mount/MAGI.app",
    nextBundle: "/tmp/MAGI.app.next",
  });
  assert.match(script, /while kill -0 42/);
  assert.match(script, /cp -R '\/tmp\/magi-shell-mount\/MAGI.app' '\/tmp\/MAGI.app.next'/);
  assert.match(script, /mv '\/tmp\/MAGI.app.next' '\/Applications\/MAGI.app'/);
  assert.throws(() => updateScript({ platform: "darwin", pid: "42; rm -rf /", installer: "", mount: "", appBundle: "", stagedApp: "" }));
});
