import { createHash, randomUUID } from "node:crypto";
import { constants, closeSync, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readSync, realpathSync, unlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/** Keep the decoded batch below the peer HTTP body's base64-expanded limit. */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
export const MAX_ATTACHMENTS = 8;
export type AttachmentInput = { name: string; mime_type: string; data_base64: string };
/** Only relative, application-generated paths cross message/database boundaries. */
export type StoredAttachment = { id: string; name: string; mime_type: string; size: number; path: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MIME = /^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$/;
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;
const MAX_ENCODED_BYTES = Math.ceil(MAX_ATTACHMENT_BYTES / 3) * 4;

function scopeDirectory(scope: string): string {
  if (typeof scope !== "string" || !scope.trim() || scope.length > 256) throw new Error("Invalid attachment scope");
  return createHash("sha256").update(scope).digest("hex");
}

function safeName(name: string): string {
  if (typeof name !== "string") throw new Error("Invalid attachment name");
  // Display names are not used as paths. Remove terminal/control and bidi controls
  // as well, so a filename cannot forge additional lines in the chat transcript.
  const result = (name.split(/[/\\]/).at(-1) ?? "").replace(/[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/g, "").trim().slice(0, 180);
  return result && result !== "." && result !== ".." ? result : "attachment";
}

function mimeType(value: string): string {
  if (typeof value !== "string") throw new Error("Invalid attachment MIME type");
  const mime = value.trim().toLowerCase() || "application/octet-stream";
  if (!MIME.test(mime)) throw new Error("Invalid attachment MIME type");
  return mime;
}

function directory(path: string, create = false): void {
  if (create) {
    try { mkdirSync(path, { mode: 0o700 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
  }
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("Attachment directory is not a regular directory");
}

function storage(workspace: string, scope: string, create = false): { root: string; folder: string; relative: string } {
  const relative = `attachments/${scopeDirectory(scope)}`;
  // The workspace is application configuration, never a value from a message.
  const root = realpathSync(resolve(workspace));
  directory(join(root, "attachments"), create);
  const folder = join(root, relative);
  directory(folder, create);
  return { root, folder, relative };
}

function checkedDescriptor(attachment: StoredAttachment, scope: string): void {
  if (!attachment || typeof attachment !== "object" || typeof attachment.id !== "string" || !UUID.test(attachment.id)
    || typeof attachment.name !== "string" || attachment.name !== safeName(attachment.name)
    || typeof attachment.mime_type !== "string" || attachment.mime_type !== mimeType(attachment.mime_type)
    || !Number.isSafeInteger(attachment.size) || attachment.size < 0 || attachment.size > MAX_ATTACHMENT_BYTES
    || attachment.path !== `attachments/${scopeDirectory(scope)}/${attachment.id}`) {
    throw new Error("Invalid or out-of-scope attachment");
  }
}

function decodeAttachments(inputs: unknown): Array<{ name: string; mime_type: string; data: Buffer }> {
  if (!Array.isArray(inputs) || inputs.length > MAX_ATTACHMENTS) throw new Error(`At most ${MAX_ATTACHMENTS} attachments are allowed`);
  let total = 0;
  return inputs.map(input => {
    if (!input || typeof input !== "object" || typeof input.data_base64 !== "string"
      || input.data_base64.length > MAX_ENCODED_BYTES || input.data_base64.length % 4 !== 0 || !BASE64.test(input.data_base64)) throw new Error("Invalid attachment data");
    const data = Buffer.from(input.data_base64, "base64");
    if (data.toString("base64") !== input.data_base64) throw new Error("Invalid attachment base64");
    total += data.length;
    if (total > MAX_ATTACHMENT_BYTES) throw new Error("Attachments exceed the 10 MiB total limit");
    return { name: safeName(input.name), mime_type: mimeType(input.mime_type), data };
  });
}

/** Check an incoming peer/group payload before recording any durable event. */
export function validateAttachmentInputs(inputs: unknown): asserts inputs is AttachmentInput[] {
  decodeAttachments(inputs);
}

/** Validate the complete batch before writing anything. No caller-supplied path is accepted. */
export function storeAttachments(workspace: string, scope: string, inputs: readonly AttachmentInput[]): StoredAttachment[] {
  scopeDirectory(scope);
  const decoded = decodeAttachments(inputs);
  if (!decoded.length) return [];
  const { folder, relative } = storage(workspace, scope, true);
  const written: string[] = [];
  try {
    return decoded.map(({ name, mime_type, data }): StoredAttachment => {
      const id = randomUUID();
      const target = join(folder, id);
      // UUID + exclusive creation prevents collision/overwrite. O_NOFOLLOW also
      // rejects a last-component symlink on platforms that implement it.
      const fd = openSync(target, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
      written.push(target);
      try {
        directory(join(realpathSync(resolve(workspace)), "attachments"));
        directory(folder);
        if (realpathSync(target) !== target) throw new Error("Attachment path changed while storing");
        writeFileSync(fd, data);
        fsyncSync(fd);
      } finally { closeSync(fd); }
      return { id, name, mime_type, size: data.length, path: `${relative}/${id}` };
    });
  } catch (error) {
    for (const path of written) { try { unlinkSync(path); } catch { /* Preserve the original error. */ } }
    throw error;
  }
}

/**
 * Open only an application-created file in this conversation. Never follow a
 * path supplied in chat text, absolute path, symlink, directory, or hard link.
 */
export function readStoredAttachment(workspace: string, scope: string, attachment: StoredAttachment, maxBytes = MAX_ATTACHMENT_BYTES): Buffer {
  checkedDescriptor(attachment, scope);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0 || attachment.size > maxBytes) throw new Error("Attachment exceeds the read limit");
  const { root, folder } = storage(workspace, scope);
  const path = join(folder, attachment.id);
  const before = lstatSync(path);
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1) throw new Error("Attachment is not a regular private file");
  const fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    directory(join(root, "attachments"));
    directory(folder);
    if (realpathSync(path) !== path) throw new Error("Attachment path changed while opening");
    const stat = fstatSync(fd);
    const after = lstatSync(path);
    if (!stat.isFile() || stat.nlink !== 1 || stat.dev !== before.dev || stat.ino !== before.ino
      || stat.dev !== after.dev || stat.ino !== after.ino || stat.size !== attachment.size || stat.size > maxBytes) {
      throw new Error("Attachment changed or exceeds the read limit");
    }
    // A bounded read also protects against a file growing after fstat.
    const data = Buffer.alloc(attachment.size + 1);
    let length = 0;
    while (length < data.length) {
      const count = readSync(fd, data, length, data.length - length, length);
      if (!count) break;
      length += count;
    }
    if (length !== attachment.size) throw new Error("Attachment changed while reading");
    return data.subarray(0, length);
  } finally { closeSync(fd); }
}

/** Re-materialize only validated conversation files for an explicitly addressed peer. */
export function wireAttachments(workspace: string, scope: string, attachments: readonly StoredAttachment[]): AttachmentInput[] {
  if (!Array.isArray(attachments) || attachments.length > MAX_ATTACHMENTS) throw new Error("Too many attachments");
  let total = 0;
  return attachments.map(attachment => {
    const data = readStoredAttachment(workspace, scope, attachment);
    total += data.length;
    if (total > MAX_ATTACHMENT_BYTES) throw new Error("Attachments exceed the 10 MiB total limit");
    return { name: attachment.name, mime_type: attachment.mime_type, data_base64: data.toString("base64") };
  });
}

/** Stable text fallback for every provider, including text-only custom endpoints. */
export function attachmentText(attachments: readonly StoredAttachment[]): string {
  if (!attachments.length) return "";
  return "Attachments saved in the workspace (a path alone does not expose the file contents):\n"
    + attachments.map(file => `- ${JSON.stringify(file.name)} (${file.mime_type}, ${file.size} bytes): ${file.path}`).join("\n");
}

/** Restrict native image parts to formats supported across pi-ai providers. */
export function nativeImageMime(data: Buffer, declaredMime: string): string | undefined {
  const actual = data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ? "image/png"
    : data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff ? "image/jpeg"
    : data.length >= 6 && ["GIF87a", "GIF89a"].includes(data.subarray(0, 6).toString("ascii")) ? "image/gif"
    : data.length >= 12 && data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP" ? "image/webp" : undefined;
  return actual === declaredMime ? actual : undefined;
}
