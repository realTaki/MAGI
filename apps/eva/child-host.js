import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Host composition and Agent capabilities are intentionally separate layers.
// The official web profile supplies Sessions, persistence, permissions and UI;
// this overlay owns only EVA's child lifecycle, DM, preset and model/auth route.
export const DEFAULT_EVA_PRESET = 'eva';
export const CHILD_HOST_VERSION = 2;

export async function readEvaPreset() {
  const text = await readFile(new URL('./eva-preset.yml', import.meta.url), 'utf8');
  return { text, digest: createHash('sha256').update(text).digest('hex').slice(0, 16) };
}

const desktopRoles = [
  { id: 'magi-shell', name: '@local/magi-shell', disabled: true },
  { id: 'eva', name: '@local/eva', disabled: true },
];

// Existing child profiles remain user-owned. Exclude known desktop roles only
// when their bundles are selected; never rewrite manifests or patch absent IDs.
// This is a product-role default, not a filter/sandbox for arbitrary plugins.
export async function desktopRoleExclusions(profileDir) {
  let manifest;
  try { manifest = JSON.parse(await readFile(join(profileDir, 'package.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  const bundles = manifest?.dsh?.profile?.bundles ?? [];
  if (!Array.isArray(bundles)) throw new Error('Invalid EVA profile bundle selection.');
  return desktopRoles.filter(row => bundles.includes(row.name)).map(row => ({ ...row }));
}

export function createChildHostPatch({ workspace, profile, presetPath, credentialsPath, desktopExclusions = [] }) {
  const sharing = credentialsPath === undefined ? {} : { credentialsPath, credentialSharingVersion: 1 };
  const overlay = [{ insert: [{
    id: 'eva-child', name: fileURLToPath(new URL('./child-entry.js', import.meta.url)),
    config: { workspace, profile, ...sharing },
  }, {
    id: 'eva-agent-preset', name: 'cordis:include', config: { path: presetPath },
  }] }, {
    id: 'agent-preset-registry', name: '@deepseek-ai/dsh-agent-preset-registry',
    // Saved user defaults still take precedence in the native registry.
    config: { default: DEFAULT_EVA_PRESET },
  }, {
    // A shared OAuth grant does not register a model route. Activate the native
    // catalog without copying parent headers/endpoints/secrets/default model.
    id: 'llm-pi-ai', name: '@deepseek-ai/dsh-llm-pi-ai', disabled: false,
    config: { providers: { 'openai-codex': {} } },
  }];
  if (credentialsPath !== undefined) overlay.push({
    id: 'credentials', name: '@deepseek-ai/dsh-credentials-local', disabled: false,
    config: { path: credentialsPath, watch: true },
  });
  return [...overlay, ...desktopExclusions];
}
