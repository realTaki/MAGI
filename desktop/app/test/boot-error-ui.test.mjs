import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import test from "node:test";

const boot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../shell/boot");

test("startup error uses one text box and icon-only actions", async () => {
  const html = readFileSync(path.join(boot, "index.html"), "utf8");
  const css = readFileSync(path.join(boot, "styles.css"), "utf8");
  assert.doesNotMatch(html, /class="error-title"/);
  assert.match(html, /id="copy-startup-error"[^>]*aria-label="Copy error"[^>]*>\s*<svg/);
  assert.match(html, /id="retry-startup"[^>]*aria-label="Retry startup"[^>]*>\s*<svg/);
  assert.match(css, /\.error-actions\s*\{[^}]*position: absolute;/);

  const elements = new Map();
  const element = (id) => {
    const value = {
      hidden: true, textContent: "", title: "", value: "", dataset: {}, style: {},
      attributes: {}, listeners: {}, focused: false, selected: false,
      setAttribute(name, content) { this.attributes[name] = content; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      focus() { this.focused = true; },
      select() { this.selected = true; },
    };
    elements.set(id, value);
    return value;
  };
  for (const id of ["startup-step", "startup-progress", "startup-percent", "startup-error-panel", "startup-error", "copy-startup-error", "copy-status", "retry-startup", "progress-track"]) element(id);
  let onError;
  let onProgress;
  let copied = "";
  let retried = 0;
  const window = { magiDesktop: {
    onStartupError(callback) { onError = callback; },
    onStartupProgress(callback) { onProgress = callback; },
    async copyText(value) { copied = value; },
    async retryStartup() { retried++; },
  } };
  runInNewContext(readFileSync(path.join(boot, "app.js"), "utf8"), {
    document: {
      getElementById(id) { return elements.get(id); },
      querySelector(selector) { return selector === ".progress-track" ? elements.get("progress-track") : null; },
    },
    window,
  });

  onError("Something failed");
  assert.equal(elements.get("startup-error-panel").hidden, false);
  assert.equal(elements.get("startup-error").value, "Something failed");
  await elements.get("copy-startup-error").listeners.click();
  assert.equal(copied, "Something failed");
  assert.equal(elements.get("copy-startup-error").dataset.copied, "true");
  assert.equal(elements.get("copy-startup-error").attributes["aria-label"], "Error copied");
  onProgress({ message: "Starting", percent: 0.4 });
  assert.equal(elements.get("copy-startup-error").dataset.copied, "false");
  onError("Try again");
  elements.get("retry-startup").listeners.click();
  assert.equal(retried, 1);
  assert.equal(elements.get("startup-error-panel").hidden, true);
});
