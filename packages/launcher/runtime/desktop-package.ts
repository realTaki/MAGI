/** Build a local desktop artifact from an already verified, immutable revision. */
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { resolveToolchain, toolchainEnv } from './toolchain.js';

export type DesktopManifest = {
  schema: 1; platform: 'linux'; arch: string; revision: string; previous: string;
  dir: string; installer: string; sha256: string;
};

export async function packageDesktopRevision(candidate: { revision: string; previous: string; dir: string }, env: NodeJS.ProcessEnv = process.env): Promise<DesktopManifest> {
  if (process.platform !== 'linux' || env.MAGI_ROLE !== 'shell' || env.MAGI_PACKAGED !== '1') throw new Error('Desktop self-update requires the packaged Linux Shell');
  const dir = realpathSync(candidate.dir);
  if (!/^[a-f0-9]{40}$/.test(candidate.revision) || basename(dir) !== candidate.revision || basename(dirname(dir)) !== 'revisions') throw new Error('Desktop package must come from a verified revision directory');
  const { node, npm, git } = resolveToolchain(env);
  const run = (file: string, args: string[]) => new Promise<string>((resolve, reject) => {
    execFile(file, args, { cwd: dir, env: toolchainEnv(env), timeout: 20 * 60_000, maxBuffer: 32 * 1024 * 1024 }, (error, stdout, stderr) => error ? reject(new Error(`${stderr}${stdout}`.slice(-16000) || error.message)) : resolve(String(stdout).trim()));
  });
  if (await run(git, ['rev-parse', 'HEAD']) !== candidate.revision) throw new Error('Desktop source revision identity changed');
  const output = join(dir, 'desktop-artifacts', randomUUID());
  mkdirSync(output, { recursive: true });
  await run(node, [npm, 'run', 'build', '--prefix', 'apps/shell', '--', '--linux', 'AppImage', '--publish', 'never', `--config.directories.output=${output}`, `--config.extraMetadata.magiRevision=${candidate.revision}`]);
  // Never guess an older artifact or pick an arbitrary architecture from a shared release directory.
  const images = readdirSync(output).filter((name) => name.endsWith('.AppImage'));
  if (images.length !== 1) throw new Error(`Desktop build must produce exactly one AppImage, found ${images.length}`);
  const installer = realpathSync(join(output, images[0]!));
  if (dirname(installer) !== resolve(output) || !statSync(installer).isFile()) throw new Error('Desktop artifact escaped its build directory');
  if (await run(git, ['rev-parse', 'HEAD']) !== candidate.revision || await run(git, ['status', '--porcelain', '--untracked-files=no'])) throw new Error('Desktop build changed the verified source');
  const manifest: DesktopManifest = { schema: 1, platform: 'linux', arch: process.arch, ...candidate, dir, installer, sha256: createHash('sha256').update(readFileSync(installer)).digest('hex') };
  writeFileSync(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2), { flag: 'wx', mode: 0o600 });
  return manifest;
}
