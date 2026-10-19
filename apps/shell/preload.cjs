"use strict";

const { contextBridge, ipcRenderer } = require("electron");

function aspBase() {
  const explicit = process.env.MAGI_ASP && process.env.MAGI_ASP.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  const host = (process.env.MAGI_ASP_HOST && process.env.MAGI_ASP_HOST.trim()) || "127.0.0.1";
  const port = (process.env.MAGI_ASP_PORT && process.env.MAGI_ASP_PORT.trim()) || "42069";
  return "http://" + host + ":" + port;
}

contextBridge.exposeInMainWorld("magiDesktop", {
  aspBase: aspBase(),
  reloadInterface: () => ipcRenderer.invoke("shell:reload-interface"),
  retryStartup: () => ipcRenderer.invoke("startup:retry"),
  copyText: (text) => ipcRenderer.invoke("shell:copy-text", text),
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
