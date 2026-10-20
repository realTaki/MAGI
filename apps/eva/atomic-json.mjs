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
    io.renameSync(temporary, file);
  } finally {
    if (fd !== undefined) io.closeSync(fd);
    io.rmSync(temporary, { force: true });
  }
}
