import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SecretStore } from "../secrets.js";

test("secret partial write and rename failure preserve previous disk and memory", () => {
  for (const failure of ["write", "rename"]) {
    const dir = fs.mkdtempSync(join(tmpdir(), "magi-secret-atomic-"));
    const file = join(dir, "secrets.json"); const store = new SecretStore(file);
    store.set("provider.api_key", "mock-existing"); const previous = fs.readFileSync(file, "utf8");
    const write = fs.writeFileSync; const rename = fs.renameSync;
    try {
      if (failure === "write") fs.writeFileSync = ((fd: number, body: string) => {
        write(fd, body.slice(0, 12)); throw new Error("mock ENOSPC");
      }) as typeof fs.writeFileSync;
      else fs.renameSync = (() => { throw new Error("mock rename failure"); }) as typeof fs.renameSync;
      syncBuiltinESMExports();
      assert.throws(() => store.set("oauth-candidate", "mock-token"));
      assert.equal(store.get("oauth-candidate"), null);
      assert.equal(store.get("provider.api_key"), "mock-existing");
      assert.equal(fs.readFileSync(file, "utf8"), previous);
      assert.equal(new SecretStore(file).get("provider.api_key"), "mock-existing");
      assert.deepEqual(fs.readdirSync(dir), ["secrets.json"]);
    } finally {
      fs.writeFileSync = write; fs.renameSync = rename; syncBuiltinESMExports();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
});
test("successful secret rotation and deletion are durable", () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "magi-secret-atomic-"));
  try {
    const file = join(dir, "secrets.json"); const store = new SecretStore(file);
    store.set("oauth", "old-mock"); store.set("oauth", "rotated-mock");
    assert.equal(new SecretStore(file).get("oauth"), "rotated-mock");
    if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    store.set("oauth", ""); assert.equal(new SecretStore(file).get("oauth"), null);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
