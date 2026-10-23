import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { packagedElectronVersion } from "../scripts/electron-version.mjs";

test("desktop packaging and the lockfile agree on one exact Electron version", () => {
  const metadata = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const lock = JSON.parse(readFileSync(new URL("../../../package-lock.json", import.meta.url), "utf8"));
  const version = packagedElectronVersion(metadata);
  assert.equal(lock.packages["apps/shell"].devDependencies.electron, version);
  assert.equal(lock.packages["node_modules/electron"].version, version);
});

test("an old packaging override cannot silently win over a patched dependency", () => {
  assert.throws(() => packagedElectronVersion({
    devDependencies: { electron: "44.5.0" },
    build: { electronVersion: "44.4.5" },
  }), /differs from the dependency pin/);
});

test("artifact verification uses a pinned version rather than a range", () => {
  assert.equal(packagedElectronVersion({
    devDependencies: { electron: "44.5.0" },
    build: { electronVersion: "44.5.0" },
  }), "44.5.0");
  for (const version of ["^44.5.0", "44.x", "latest", undefined]) {
    assert.throws(() => packagedElectronVersion({
      devDependencies: { electron: version },
      build: { electronVersion: version },
    }), /exact stable version pin/);
  }
});
