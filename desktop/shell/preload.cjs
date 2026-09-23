"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("magiDesktop", {
  retryStartup: () => ipcRenderer.invoke("asp:retry"),
  onStartupProgress: (listener) =>
    ipcRenderer.on("asp:startup-progress", (_event, progress) => listener(progress)),
  onStartupError: (listener) =>
    ipcRenderer.on("asp:startup-error", (_event, message) => listener(message)),
});
