// Pack native DSH packages without mutating a checkout's node_modules.
import { cpSync, existsSync, globSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../', import.meta.url));
const json = file => JSON.parse(readFileSync(file, 'utf8'));
function inside(root, target) {
  const rel = path.relative(root, target);
  if (rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) throw new Error(`Path escapes package: ${target}`);
}
function pattern(value) {
  if (typeof value !== 'string' || !value || value.startsWith('!') || path.isAbsolute(value) || value.split(/[\\/]/).includes('..')) throw new Error(`Unsafe package file/reference: ${value}`);
  return value.replace(/^\.\//, '');
}
function copyResource(source, target, packageRoot) {
  inside(packageRoot, realpathSync(source));
  const stat = lstatSync(source);
  if (stat.isSymbolicLink()) throw new Error(`Package resources must not be symlinks: ${source}`);
  if (!stat.isFile() && !stat.isDirectory()) throw new Error(`Unsupported package resource: ${source}`);
  // Check descendants too: cpSync must never copy an escaping nested symlink.
  if (stat.isDirectory()) {
    const inspect = directory => {
      for (const entry of readdirSync(directory)) {
        const child = path.join(directory, entry);
        inside(packageRoot, realpathSync(child));
        const childStat = lstatSync(child);
        if (childStat.isSymbolicLink()) throw new Error(`Package resources must not be symlinks: ${child}`);
        if (childStat.isDirectory()) inspect(child);
        else if (!childStat.isFile()) throw new Error(`Unsupported package resource: ${child}`);
      }
    };
    inspect(source);
  }
  mkdirSync(path.dirname(target), { recursive: true });
  cpSync(source, target, { recursive: true });
}
function references(value) {
  if (typeof value === 'string') return [value];
  if (value && typeof value === 'object') return Object.values(value).flatMap(references);
  return [];
}

/** Copy only a package's explicit allowlist and its declared bundled file dependencies. */
export function stagePackage(source, destination, { root = repository, expectedName, ancestors = [] } = {}) {
  source = realpathSync(source);
  destination = path.resolve(destination);
  if (destination === source || destination.startsWith(source + path.sep)) throw new Error('Staging must not modify the source package');
  if (existsSync(destination)) throw new Error('Staging destination must not already exist');
  root = realpathSync(root);
  inside(root, source);
  if (ancestors.includes(source)) throw new Error(`Cyclic bundled dependency: ${source}`);
  const manifest = json(path.join(source, 'package.json'));
  if (expectedName && manifest.name !== expectedName) throw new Error(`Bundled dependency name mismatch: ${expectedName}`);
  if (!Array.isArray(manifest.files) || !manifest.files.length) throw new Error(`Explicit package files required: ${manifest.name}`);
  mkdirSync(destination, { recursive: true });
  writeFileSync(path.join(destination, 'package.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  for (const entry of manifest.files) {
    const selected = globSync(pattern(entry), { cwd: source });
    if (!selected.length) throw new Error(`Missing package resource: ${manifest.name}/${entry}`);
    for (const relative of selected) copyResource(path.join(source, relative), path.join(destination, relative), source);
  }
  const declaredReferences = [
    ...references(manifest.exports),
    ...references(manifest.dsh?.bundle?.patch),
    ...[manifest.main].filter(Boolean),
  ];
  for (const ref of declaredReferences) {
    if (!globSync(pattern(ref), { cwd: destination }).length) throw new Error(`Unpacked package reference: ${manifest.name}/${ref}`);
  }
  const bundles = manifest.bundledDependencies ?? manifest.bundleDependencies ?? [];
  if (!Array.isArray(bundles)) throw new Error('bundledDependencies must be an explicit array');
  for (const name of bundles) {
    if (!/^(?:@[a-z0-9_.-]+\/)?[a-z0-9_.-]+$/i.test(name)) throw new Error(`Unsafe bundled package name: ${name}`);
    const spec = manifest.dependencies?.[name];
    if (typeof spec !== 'string' || !spec.startsWith('file:')) throw new Error(`Bundled dependency must declare a local file: source: ${name}`);
    const dependency = path.resolve(source, spec.slice(5));
    stagePackage(dependency, path.join(destination, 'node_modules', name), { root, expectedName: name, ancestors: [...ancestors, source] });
  }
  return manifest;
}

export function resolveNpmCli(execPath = process.execPath, explicit) {
  const candidate = explicit ?? path.resolve(path.dirname(execPath), '../npm/node_modules/npm/bin/npm-cli.js');
  if (!existsSync(candidate)) throw new Error('MAGI-owned npm CLI not found; pass --npm /absolute/path/to/npm-cli.js');
  return path.resolve(candidate);
}

/** outputDir is mandatory for library callers; only the CLI defaults to repo/dist. */
export function packDsh({ root = repository, app = 'eva', outputDir, npmCli, execPath = process.execPath } = {}) {
  const locations = { eva: 'apps/eva', 'magi-shell': 'apps/magi-shell', memory: 'packages/memory', 'bot-mode': 'packages/bot-mode' };
  if (!Object.hasOwn(locations, app)) throw new Error(`Unsupported DSH app: ${app}`);
  if (!outputDir) throw new Error('An explicit outputDir is required');
  const npm = resolveNpmCli(execPath, npmCli);
  const output = path.resolve(outputDir);
  mkdirSync(output, { recursive: true });
  const scratch = mkdtempSync(path.join(tmpdir(), 'magi-pack-dsh-'));
  try {
    const stage = path.join(scratch, 'package');
    const manifest = stagePackage(path.join(root, locations[app]), stage, { root });
    const result = spawnSync(execPath, [npm, 'pack', '--ignore-scripts', '--offline', '--json', '--pack-destination', output], {
      cwd: stage, encoding: 'utf8', env: {
        ...process.env, npm_config_cache: path.join(scratch, 'cache'),
        npm_config_userconfig: path.join(scratch, 'user.npmrc'), npm_config_globalconfig: path.join(scratch, 'global.npmrc'),
        npm_config_update_notifier: 'false',
      },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`npm pack failed (${result.status}): ${result.stderr || result.stdout}`);
    const report = JSON.parse(result.stdout);
    const details = Array.isArray(report) ? report[0] : report.filename ? report : Object.values(report)[0];
    if (!details?.filename) throw new Error('npm pack did not report an archive');
    const archive = path.resolve(output, details.filename);
    inside(output, archive);
    if (!existsSync(archive)) throw new Error('npm pack archive is missing');
    for (const name of manifest.bundledDependencies ?? manifest.bundleDependencies ?? []) {
      if (!details.bundled?.includes(name)) throw new Error(`npm pack omitted bundled dependency: ${name}`);
    }
    return { archive, manifest, report: details };
  } finally {
    // mkdtemp generated this exact absolute directory; never clean checkout paths.
    if (path.dirname(scratch) !== path.resolve(tmpdir()) || !path.basename(scratch).startsWith('magi-pack-dsh-')) throw new Error('Unsafe temporary cleanup path');
    rmSync(scratch, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = { app: 'eva', outputDir: path.join(repository, 'dist') };
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    const key = { '--app': 'app', '--out': 'outputDir', '--npm': 'npmCli' }[args[i]];
    if (!key || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Usage: pack-dsh.mjs [--app eva|magi-shell|memory|bot-mode] [--out directory] [--npm npm-cli.js]');
    options[key] = args[++i];
  }
  console.log(packDsh(options).archive);
}
