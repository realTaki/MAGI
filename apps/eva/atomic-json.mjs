import { randomUUID } from 'node:crypto';
import * as fs from 'node:fs';
import { dirname } from 'node:path';

/** Publish a complete snapshot, never a truncated destination, to other processes. */
export function writeJsonAtomic(file, value, io = fs) {
  io.mkdirSync(dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  let fd;
  try {
    fd = io.openSync(temporary, 'wx', 0o600);
    io.writeFileSync(fd, JSON.stringify(value));
    io.fsyncSync(fd);
    io.closeSync(fd);
    fd = undefined;
    // Windows readers/antivirus can briefly hold a non-delete-sharing handle.
    // Retry replacement only; never unlink the valid destination or overwrite it.
    const wait = new Int32Array(new SharedArrayBuffer(4));
    for (let attempt = 0; ; attempt++) {
      try { io.renameSync(temporary, file); break; }
      catch (error) {
        if (attempt >= 49 || !['EPERM', 'EACCES', 'EBUSY'].includes(error?.code)) throw error;
        Atomics.wait(wait, 0, 0, 10);
      }
    }
  } finally {
    if (fd !== undefined) io.closeSync(fd);
    io.rmSync(temporary, { force: true });
  }
}
