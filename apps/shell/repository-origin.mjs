import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const invalid = () => new Error("MAGI repository origin must identify a GitHub owner/repository without credentials, query, or fragment");

/** Canonical public clone URL; never copy remote credentials into a package. */
export function normalizeRepositoryOrigin(value) {
  if (typeof value !== "string" || !value.trim()) throw invalid();
  const input = value.trim();
  if (/[\s%?#\\]/.test(input) || /\/\.{1,2}(?:\/|$)/.test(input)) throw invalid();
  let slug;
  if (/^[^\s/:]+\/[^\s/]+$/.test(input)) {
    slug = input;
  } else {
    const scp = /^git@github\.com:([^\s]+)$/i.exec(input);
    if (scp) slug = scp[1];
    else {
      let url;
      try { url = new URL(input); } catch { throw invalid(); }
      if (!['https:', 'ssh:'].includes(url.protocol) || url.hostname.toLowerCase() !== 'github.com'
        || url.port || url.password || url.search || url.hash
        || (url.username && !(url.protocol === 'ssh:' && url.username === 'git'))) throw invalid();
      slug = url.pathname.slice(1);
    }
  }
  slug = slug.replace(/\.git$/, '');
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\/[A-Za-z0-9_.-]+$/.test(slug)
    || ['.', '..'].includes(slug.split('/')[1])) throw invalid();
  return `https://github.com/${slug}.git`;
}

export function localRepositoryOrigin({ cwd, git = 'git' }) {
  let remote;
  try { remote = execFileSync(git, ['config', '--get', 'remote.origin.url'], { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }); }
  catch { throw new Error('Cannot determine MAGI repository origin: configure a valid GitHub git remote origin before building or launching source.'); }
  return normalizeRepositoryOrigin(remote);
}

export function buildRepositoryMetadata({ cwd, env = process.env, git = 'git' }) {
  // Actions supplies the repository running this workflow, independently of checkout remotes.
  const repository = env.GITHUB_ACTIONS === 'true'
    ? normalizeRepositoryOrigin(env.GITHUB_REPOSITORY)
    : localRepositoryOrigin({ cwd, git });
  return { schemaVersion: 1, repository };
}

export function writeRepositoryMetadata(options) {
  const metadata = buildRepositoryMetadata(options);
  writeFileSync(path.join(options.shellDirectory, 'build-origin.json'), `${JSON.stringify(metadata, null, 2)}\n`);
  return metadata;
}

export function resolveRepositoryOrigin({ shellDirectory, packaged, override, git = 'git' }) {
  // Retain the explicitly requested advanced runtime override (including private/local remotes).
  if (typeof override === 'string' && override.trim()) return override.trim();
  if (!packaged) return localRepositoryOrigin({ cwd: shellDirectory, git });
  let metadata;
  try { metadata = JSON.parse(readFileSync(path.join(shellDirectory, 'build-origin.json'), 'utf8')); }
  catch { throw new Error('MAGI package is missing valid build-origin.json; rebuild the desktop package from its source repository.'); }
  if (metadata?.schemaVersion !== 1) throw new Error('Unsupported MAGI build-origin.json schema; rebuild the desktop package.');
  return normalizeRepositoryOrigin(metadata.repository);
}
