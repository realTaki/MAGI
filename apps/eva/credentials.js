import { createRequire } from 'node:module';
import { isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

// Version-bound adapter for the official local provider. Its abstract service
// intentionally has no filesystem location; use the concrete public config and
// exported resolver, never its private snapshot or any credential values.
export async function credentialStorePath(ctx) {
  const provider = ctx.credentials;
  const require = createRequire(ctx.profileContext.installAnchor);
  const { default: LocalCredentialProvider, resolveSpec } = await import(
    pathToFileURL(require.resolve('@deepseek-ai/dsh-credentials-local')).href);
  if (!(provider instanceof LocalCredentialProvider) || typeof resolveSpec !== 'function') {
    throw new Error('EVA local credential sharing requires the official local credentials provider.');
  }
  if (provider.config.watch === false) {
    throw new Error('EVA local credential sharing requires watch:true on the parent credentials provider so sign-out and account changes propagate.');
  }
  const path = resolveSpec(provider.config).filename;
  if (typeof path !== 'string' || !isAbsolute(path)) {
    throw new Error('The official credentials provider did not resolve an absolute store path.');
  }
  // A missing file is valid while signed out. Do not inspect, copy, canonicalize,
  // or create it here; the official provider owns reads, locking and permissions.
  return path;
}
