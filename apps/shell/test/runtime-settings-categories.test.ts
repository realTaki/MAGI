import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const page = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../web/src/SettingsPage.tsx"),
  "utf8",
);

function runtimeSection() {
  const start = page.indexOf('{section === "runtime" ? (');
  const end = page.indexOf('{section === "about" ? (');
  assert.ok(start >= 0 && end > start, "runtime settings section");
  return page.slice(start, end);
}

/** Visible 运行与构建 cards, in DOM order, with the invoke methods each button still calls. */
function cards() {
  const section = runtimeSection();
  const labels = [...section.matchAll(/className="settings-card__label">([^<]+)/g)];
  return labels.map((match, index) => {
    const from = match.index ?? 0;
    const to = labels[index + 1]?.index ?? section.length;
    const body = section.slice(from, to);
    const methods = [...body.matchAll(/onRun=\{\(\) => \w+\("([^"]+)"\)\}/g)].map((entry) => entry[1]);
    return { label: match[1].trim(), methods };
  });
}

test("运行与构建 host cards are shell, ASP, then all MAGIs", () => {
  const host = cards().filter(
    (card) => !card.label.includes("sourceGroup") && !card.label.includes("installer"),
  );
  assert.deepEqual(
    host.map((card) => card.label),
    ["shell", 'ASP · {runtimeStatus || "—"}', '{t("appSettings.magiGroup")}'],
  );
  assert.deepEqual(host[0].methods, ["runtime.syncApp", "runtime.rebuildApp"]);
  assert.deepEqual(host[1].methods, [
    "runtime.stopAsp",
    "runtime.startAsp",
    "runtime.syncAsp",
    "runtime.rebuildAsp",
  ]);
  assert.deepEqual(host[2].methods, [
    "magi.startAll",
    "magi.stopAll",
    "magi.syncAll",
    "magi.rebuildAll",
  ]);
});
