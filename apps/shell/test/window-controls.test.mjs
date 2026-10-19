import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { runInNewContext } from "node:vm";

// Regression: Linux/Windows previously hid the frame without replacement controls.
// This checks native-decoration configuration; actual hit testing needs desktop QA.
const source = readFileSync(new URL("../main.mjs", import.meta.url), "utf8");
const factory = source.slice(source.indexOf("function createWindow() {"), source.indexOf("// One generic bridge:"));
for (const platform of ["darwin", "linux", "win32"]) {
  test(`${platform}: window retains native controls`, () => {
    class Window extends EventEmitter {
      constructor(options) {
        super(); this.options = options;
        this.webContents = { setWindowOpenHandler() {} };
      }
      setMenuBarVisibility() {}
      removeMenu() {}
    }
    const win = runInNewContext(`${factory}\ncreateWindow()`, {
      BrowserWindow: Window, process: { platform }, path,
      initialWindowState: () => ({ bounds: { x: 0, y: 0, width: 900, height: 600 }, maximized: false }),
      MINIMUM_WINDOW_SIZE: { width: 800, height: 500 }, SHELL_DIR: "/fixture/shell",
    });
    assert.notEqual(win.options.frame, false);
    assert.equal(win.options.titleBarStyle, platform === "darwin" ? "hiddenInset" : undefined);
    for (const capability of ["closable", "minimizable", "maximizable"]) {
      assert.notEqual(win.options[capability], false, capability);
    }
  });
}
