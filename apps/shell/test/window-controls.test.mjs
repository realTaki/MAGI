import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { loadApp } from "../interface.mjs";

// Exercise the actual window factory without starting Electron, the backend,
// or a user profile. Native decoration/hit testing still needs desktop QA.
const source = readFileSync(new URL("../main.mjs", import.meta.url), "utf8");
const factory = source.slice(source.indexOf("function createWindow() {"), source.indexOf("// One generic bridge:"));

for (const platform of ["darwin", "linux", "win32"]) {
  for (const maximized of [false, true]) {
    test(`${platform}: shared window keeps native controls (saved maximized=${maximized})`, async () => {
      const bounds = { x: 10, y: 20, width: 900, height: 600 };
      const actions = [];
      class Window extends EventEmitter {
        constructor(options) {
          super();
          this.options = options;
          this.webContents = { setWindowOpenHandler() {} };
        }
        setMenuBarVisibility() {}
        removeMenu() {}
        maximize() { actions.push("maximize"); }
        show() { actions.push("show"); }
        async loadFile(file) { this.page = file; }
        async loadURL(url) { this.page = url; }
      }
      let saved;
      const win = runInNewContext(`${factory}\ncreateWindow()`, {
        BrowserWindow: Window, process: { platform }, path,
        initialWindowState: () => ({ bounds, maximized }),
        MINIMUM_WINDOW_SIZE: { width: 800, height: 500 },
        SHELL_DIR: "/fixture/shell", mainWindow: null,
        clearTimeout, setTimeout, writeWindowState: (value) => { saved = value; },
      });
      assert.notEqual(win.options.frame, false);
      assert.equal(win.options.titleBarStyle, platform === "darwin" ? "hiddenInset" : undefined);
      assert.equal(win.options.trafficLightPosition, undefined);
      for (const capability of ["closable", "minimizable", "maximizable", "resizable", "movable"]) {
        assert.notEqual(win.options[capability], false, capability);
      }
      assert.equal(win.options.width, bounds.width);
      assert.equal(win.options.height, bounds.height);
      assert.ok(win.options.minWidth <= bounds.width);
      assert.ok(win.options.minHeight <= bounds.height);
      win.emit("ready-to-show");
      assert.deepEqual(actions, maximized ? ["maximize", "show"] : ["show"]);

      const options = win.options;
      await win.loadFile("boot/index.html"); // Errors are rendered in this same page.
      await loadApp(win, "/fixture/ui/index.html");
      await loadApp(win, "http://localhost:5173");
      assert.equal(win.options, options, "navigation retains the native window frame");
      win.emit("close");
      assert.equal(saved, win, "closing still persists normal bounds/maximized state");
    });
  }
}
