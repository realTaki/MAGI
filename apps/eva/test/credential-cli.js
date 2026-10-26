// Test-only wrapper: add an IPC probe to a COPY of EVA's generated overlay.
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join, basename } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export async function runDesktopCli(runtimeDir, supportDir) {
  if (process.env.EVA_CREDENTIAL_TEST !== '1') throw new Error('Isolated credential test required.');
  const index = process.argv.indexOf('--patch') + 1;
  const original = process.argv[index];
  const patch = JSON.parse(await readFile(original, 'utf8'));
  patch.push({ insert: [{ id: 'eva-credential-test', name: fileURLToPath(new URL('./credential-probe.js', import.meta.url)) }] });
  const copy = join(process.env.DSH_HOME, 'eva', `test-${basename(original)}`);
  await writeFile(copy, JSON.stringify(patch), { flag: 'wx', mode: 0o600 });
  process.argv[index] = copy;
  const cli = createRequire(join(runtimeDir, 'package.json')).resolve('@deepseek-ai/dsh-desktop-host/lib/cli.js');
  const { runDesktopCli: realCli } = await import(pathToFileURL(cli).href);
  return realCli(runtimeDir, supportDir);
}
