import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync, linkSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { attachmentText, MAX_ATTACHMENT_BYTES, nativeImageMime, readStoredAttachment, storeAttachments, wireAttachments } from "../attachments.js";

const input = (name = "notes.txt", text = "hello") => ({ name, mime_type: "text/plain", data_base64: Buffer.from(text).toString("base64") });
function workspace(run: (path: string) => void) {
  const path = mkdtempSync(join(tmpdir(), "magi-attachments-"));
  try { run(path); } finally { rmSync(path, { force: true, recursive: true }); }
}

test("uploads are random scoped files, roundtrip wire bytes and never use the supplied name as a path", () => workspace(path => {
  const [one, two] = storeAttachments(path, "12", [input("../../private\\hello\n.txt"), input()]);
  assert.equal(one.name, "hello.txt");
  assert.equal(one.mime_type, "text/plain");
  assert.equal(one.size, 5);
  assert.match(one.path, /^attachments\/[0-9a-f]{64}\/[0-9a-f-]{36}$/);
  assert.notEqual(one.id, two.id);
  assert.equal(readFileSync(join(path, one.path), "utf8"), "hello");
  assert.equal(readStoredAttachment(path, "12", one).toString(), "hello");
  assert.deepEqual(wireAttachments(path, "12", [one]), [{ ...input("hello.txt") }]);
  assert.match(attachmentText([one]), /a path alone does not expose the file contents/);
  assert.ok(attachmentText([one]).includes(one.path));
  assert.throws(() => readStoredAttachment(path, "13", one), /out-of-scope/);
  assert.throws(() => readStoredAttachment(path, "12", { ...one, path: "../../secret" }), /out-of-scope/);
  assert.throws(() => readStoredAttachment(path, "12", { ...one, id: "../../secret" }), /out-of-scope/);
  assert.throws(() => readStoredAttachment(path, "12", one, 4), /limit/);
}));

test("the full upload is validated before any file is written, with strict base64 and a total limit", () => workspace(path => {
  for (const data_base64 of ["not base64", "a===", "Zh==", "Zg", "Zg==\n", "===="]) {
    assert.throws(() => storeAttachments(path, "12", [input(), { ...input(), data_base64 }]), /Invalid attachment/);
  }
  assert.throws(() => storeAttachments(path, "12", Array.from({ length: 9 }, () => input())), /At most 8/);
  assert.throws(() => storeAttachments(path, "12", [{ ...input(), mime_type: "text/plain\ninjection" }]), /MIME/);
  const half = Buffer.alloc(MAX_ATTACHMENT_BYTES / 2 + 1).toString("base64");
  assert.throws(() => storeAttachments(path, "12", [{ ...input(), data_base64: half }, { ...input(), data_base64: half }]), /total limit/);
  assert.equal(existsSync(join(path, "attachments")), false);
  assert.deepEqual(storeAttachments(path, "12", []), []);
}));

test("empty files and the full 10 MiB boundary are supported without unbounded reads", () => workspace(path => {
  const [empty] = storeAttachments(path, "12", [input("empty", "")]);
  assert.equal(readStoredAttachment(path, "12", empty).length, 0);
  const [full] = storeAttachments(path, "12", [{ ...input(), data_base64: Buffer.alloc(MAX_ATTACHMENT_BYTES).toString("base64") }]);
  assert.equal(readStoredAttachment(path, "12", full).length, MAX_ATTACHMENT_BYTES);
  writeFileSync(join(path, empty.path), "changed");
  assert.throws(() => readStoredAttachment(path, "12", empty), /changed/);
}));

test("attachment reads reject final symlinks, hard links and symlinked storage directories", () => workspace(path => {
  const [stored] = storeAttachments(path, "12", [input()]);
  const secret = join(path, "private.txt");
  writeFileSync(secret, "hello");
  unlinkSync(join(path, stored.path));
  symlinkSync(secret, join(path, stored.path));
  assert.throws(() => readStoredAttachment(path, "12", stored), /regular private file/);
  unlinkSync(join(path, stored.path));
  linkSync(secret, join(path, stored.path));
  assert.throws(() => readStoredAttachment(path, "12", stored), /regular private file/);
  unlinkSync(join(path, stored.path));
  const folder = dirname(join(path, stored.path));
  rmSync(folder, { recursive: true });
  const outside = join(path, "elsewhere");
  mkdirSync(outside);
  writeFileSync(join(outside, stored.id), "hello");
  symlinkSync(outside, folder, "dir");
  assert.throws(() => readStoredAttachment(path, "12", stored), /regular directory/);
  assert.throws(() => storeAttachments(path, "12", [input()]), /regular directory/);
}));

test("attachment storage root cannot be redirected with a symlink", () => workspace(path => {
  const outside = join(path, "elsewhere");
  mkdirSync(outside);
  symlinkSync(outside, join(path, "attachments"), "dir");
  assert.throws(() => storeAttachments(path, "12", [input()]), /regular directory/);
}));

test("native image MIME requires a supported matching signature, not a filename or claim", () => {
  const png = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(nativeImageMime(png, "image/png"), "image/png");
  assert.equal(nativeImageMime(png, "image/jpeg"), undefined);
  assert.equal(nativeImageMime(Buffer.from("<svg/>"), "image/svg+xml"), undefined);
  assert.equal(nativeImageMime(Buffer.from("not an image"), "image/png"), undefined);
  assert.equal(nativeImageMime(Buffer.from([255, 216, 255]), "image/jpeg"), "image/jpeg");
  assert.equal(nativeImageMime(Buffer.from("GIF89a"), "image/gif"), "image/gif");
  assert.equal(nativeImageMime(Buffer.from("RIFFxxxxWEBP"), "image/webp"), "image/webp");
});
