// Keep the capability list native YAML: parsing/stringifying it with an ordinary
// YAML schema would erase deferred !!js expressions. Only metadata differs for
// EVA's recorded legacy preset ID; there is no native alias/inheritance API.
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

export const DEFAULT_BOT_PRESET = 'bot-mode';
export const LEGACY_EVA_PRESET = 'eva';

function document(text) {
  return { text, digest: createHash('sha256').update(text).digest('hex').slice(0, 16) };
}

export async function readBotPreset() {
  return document(await readFile(new URL('./presets/bot-preset.yml', import.meta.url), 'utf8'));
}

export async function readEvaPresets() {
  const { text } = await readBotPreset();
  const marker = '    plugins:\n';
  const offset = text.indexOf(marker);
  // Fail closed if the package's declaration shape changes; never silently
  // publish a compatibility definition with an empty/different capability list.
  if (offset < 0 || text.indexOf(marker, offset + marker.length) !== -1 ||
      !/^\s*- id: preset-bot-mode\n  name: '@deepseek-ai\/dsh-agent-preset'\n  config:\n    id: bot-mode\n/m.test(text.slice(0, offset))) {
    throw new Error('Invalid package-owned Bot preset declaration.');
  }
  const legacy = [
    '- id: preset-eva',
    "  name: '@deepseek-ai/dsh-agent-preset'",
    '  config:',
    `    id: ${LEGACY_EVA_PRESET}`,
    '    name: EVA',
    '    description: Compatibility identity for existing EVA sessions; uses Bot capabilities.',
    '    order: 5',
    '',
  ].join('\n') + text.slice(offset);
  return document(`${text}\n${legacy}`);
}
