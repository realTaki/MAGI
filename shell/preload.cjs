"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("magiDesktop", {
  retryStartup: () => ipcRenderer.invoke("startup:retry"),
  copyText: (text) => ipcRenderer.invoke("shell:copy-text", text),
  shellRelease: () => ipcRenderer.invoke("shell:release"),
  installShellRelease: () => ipcRenderer.invoke("shell:install-release"),
  onStartupProgress: (listener) =>
    ipcRenderer.on("startup:progress", (_event, progress) => listener(progress)),
  onStartupError: (listener) =>
    ipcRenderer.on("startup:error", (_event, message) => listener(message)),
  // Everything after bootstrap belongs to the app, which the shell loads from
  // the checkout: the bridge only forwards calls, so a new capability needs no
  // change here or in the shell. Absent when the app runs outside the shell.
  invokeLocal: (method, payload) => ipcRenderer.invoke("local:invoke", method, payload),
  onLocalEvent: (listener) =>
    ipcRenderer.on("local:event", (_event, message) => listener(message)),
});
