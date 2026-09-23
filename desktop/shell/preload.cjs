"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("magiDesktop", {
  retryStartup: () => ipcRenderer.invoke("asp:retry"),
  onStartupProgress: (listener) =>
    ipcRenderer.on("asp:startup-progress", (_event, progress) => listener(progress)),
  onStartupError: (listener) =>
    ipcRenderer.on("asp:startup-error", (_event, message) => listener(message)),
  onGitHubRequired: (listener) =>
    ipcRenderer.on("github:required", (_event, info) => listener(info)),
  onGitHubStatus: (listener) =>
    ipcRenderer.on("github:status", (_event, status) => listener(status)),
  startGitHubDeviceSignIn: () => ipcRenderer.invoke("github:start-device"),
  submitGitHubToken: (token) => ipcRenderer.invoke("github:submit-token", token),
  openGitHubVerification: () => ipcRenderer.invoke("github:open-verification"),
});
