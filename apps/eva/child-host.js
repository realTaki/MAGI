import { readFile } from 'node:fs/promises';
import { DEFAULT_BOT_PRESET, readEvaPresets } from '@magi/bot-mode/presets';
import { isAbsolute, join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

// Host composition and Agent capabilities are intentionally separate layers.
// The official web profile supplies Sessions, persistence, permissions and UI;
// this overlay composes EVA lifecycle, independent DM/memory/bot-mode, preset
// and model/auth route; native persistence and Sessions remain Host-owned.
// Retain the exported name for integrations; only new-session deployment fallback
// changes. Saved defaults and recorded legacy `eva` Session identities still win.
export const DEFAULT_EVA_PRESET = DEFAULT_BOT_PRESET;
export const CHILD_HOST_VERSION = 8;

export function resolveHistoryEntry() {
  return createRequire(import.meta.url).resolve('@magi/history-search');
}

export function resolveDmEntry() {
  return createRequire(import.meta.url).resolve('@magi/dm');
}

export function resolveMemoryEntry() {
  return createRequire(import.meta.url).resolve('@magi/memory');
}

export function resolveSubconsciousEntry() {
  return createRequire(import.meta.url).resolve('@magi/subconscious');
}

export function resolveBotModeEntry() {
  return createRequire(import.meta.url).resolve('@magi/bot-mode');
}

// Compatibility export: EVA owns caching/composition, not the capability source.
export const readEvaPreset = readEvaPresets;

const desktopRoles = [
  { id: 'magi-shell', name: '@local/magi-shell', disabled: true },
  { id: 'eva', name: '@local/eva', disabled: true },
  // A selected standalone memory bundle also contributes a root; replace it
  // with the explicit installed child entry below, without editing the profile.
  { id: 'magi-memory', name: '@magi/memory', disabled: true },
  { id: 'magi-subconscious', name: '@magi/subconscious', disabled: true },
  // A standalone bot-mode bundle defaults to parent; replace it with child role.
  { id: 'magi-bot-mode', name: '@magi/bot-mode', disabled: true },
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

export function createChildHostPatch({ workspace, profile, presetPath, credentialsPath, desktopExclusions = [], dmEntry = resolveDmEntry(), memoryEntry = resolveMemoryEntry(), botModeEntry = resolveBotModeEntry(), subconsciousEntry = resolveSubconsciousEntry(), historyEntry = resolveHistoryEntry() }) {
  if (typeof dmEntry !== 'string' || !isAbsolute(dmEntry)) throw new Error('DM child entry must resolve to an installed absolute package entry.');
  if (typeof memoryEntry !== 'string' || !isAbsolute(memoryEntry)) throw new Error('Memory child entry must resolve to an installed absolute package entry.');
  // Existing profiles can select either an older EVA bundle (without a DM row)
  // or the new one. Always mount the installed DM root explicitly. Disable the
  // bundle's optional DM row by id only: Loader names assert, never rename.
  // Older bundles warn/skip the absent target while the explicit root still runs.
  if (typeof botModeEntry !== 'string' || !isAbsolute(botModeEntry)) throw new Error('Bot mode child entry must resolve to an installed absolute package entry.');
  if (typeof subconsciousEntry !== 'string' || !isAbsolute(subconsciousEntry)) throw new Error('Subconscious child entry must resolve to an installed absolute package entry.');
  if (typeof historyEntry !== 'string' || !isAbsolute(historyEntry)) throw new Error('History search child entry must resolve to an installed absolute package entry.');
  const selectedEva = desktopExclusions.some(row => row.id === 'eva');
  const selectedShell = desktopExclusions.some(row => row.id === 'magi-shell');
  const selectedBotMode = desktopExclusions.some(row => row.id === 'magi-bot-mode');
  const dmRow = { id: 'magi-dm-child', name: dmEntry, config: {} };
  const sharing = credentialsPath === undefined ? {} : { credentialsPath, credentialSharingVersion: 1 };
  const overlay = [{ insert: [{
    id: 'eva-child', name: fileURLToPath(new URL('./child.js', import.meta.url)),
    config: { workspace, profile, ...sharing },
  }, dmRow, {
    // child.js asserts workspace === profileContext.home === cwd. Memory derives
    // its Bot home from that native service, never from a Session workspace.
    id: 'magi-memory-child', name: memoryEntry, config: {},
  }, {
    id: 'magi-bot-mode-child', name: botModeEntry, config: { role: 'eva-child', workspace, profile },
  }, {
    id: 'eva-agent-preset', name: 'cordis:include', config: { path: presetPath },
  }, {
    // Availability only: the independent plugin defaults to paused.
    id: 'magi-subconscious-child', name: subconsciousEntry, config: {},
  }, {
    id: 'magi-history-search-child', name: historyEntry, config: {},
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
  const selectedSubconscious = desktopExclusions.some(row => row.id === 'magi-subconscious');
  const selectedMemory = desktopExclusions.some(row => row.id === 'magi-memory');
  return [...overlay,
    ...(selectedEva ? [{ id: 'magi-dm', disabled: true }] : []),
    ...(selectedEva && !selectedMemory ? [{ id: 'magi-memory', disabled: true }] : []),
    // Shell bundles now supply a parent identity root. Older versions may lack it;
    // id-only disabling safely warns/skips rather than asserting a package rename.
    ...(selectedShell && !selectedBotMode ? [{ id: 'magi-bot-mode', disabled: true }] : []),
    // A selected parent bundle may contribute the canonical preset too. Its
    // common root ID is last-wins in native EntryGroup, just like identity rows;
    // disable it before mounting our authoritative copied two-ID declaration.
    ...(selectedShell || selectedBotMode ? [{ id: 'magi-bot-mode-preset', disabled: true }] : []),
    ...(selectedEva && !selectedSubconscious ? [{ id: 'magi-subconscious', disabled: true }] : []),
    ...desktopExclusions,
    ...(selectedEva ? [{ id: 'magi-history-search', disabled: true }] : [])];
}
