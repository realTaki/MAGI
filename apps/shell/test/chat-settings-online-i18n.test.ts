import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const locales = ["zh", "en", "ja"] as const;

function chatSettingsBlock(source: string) {
  const start = source.indexOf("  chatSettings: {");
  const end = source.indexOf("\n  landing: {", start);
  assert.ok(start >= 0 && end > start, "chatSettings locale block");
  return source.slice(start, end);
}

function stringValue(block: string, key: string) {
  const match = block.match(new RegExp(`\\b${key}: "([^"]*)"`));
  assert.ok(match, `chatSettings.${key}`);
  return match[1];
}

test("profile runtime status keys exist in every locale", () => {
  const page = readFileSync(join(root, "web/src/RuntimeToggle.tsx"), "utf8");
  assert.ok(page.includes('"chatSettings.online"'));
  assert.ok(page.includes('"chatSettings.offline"'));

  for (const locale of locales) {
    const block = chatSettingsBlock(
      readFileSync(join(root, "web/src/i18n/locales", `${locale}.ts`), "utf8"),
    );
    const online = stringValue(block, "online");
    const offline = stringValue(block, "offline");
    assert.notEqual(online, "chatSettings.online");
    assert.notEqual(offline, "chatSettings.offline");
    assert.ok(online.length > 0);
    assert.ok(offline.length > 0);
  }
});
