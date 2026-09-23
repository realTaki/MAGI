"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("magiDesktop", {
  retryStartup: () => ipcRenderer.invoke("asp:retry"),
  onStartupProgress: (listener) =>
    ipcRenderer.on("asp:startup-progress", (_event, progress) => listener(progress)),
  onStartupError: (listener) =>
    ipcRenderer.on("asp:startup-error", (_event, message) => listener(message)),
  // Local-machine capabilities driven by the operator UI. They are absent when
  // the same UI is opened outside the desktop app, so the UI feature-detects.
  githubState: () => ipcRenderer.invoke("github:state"),
  startGitHubSignIn: () => ipcRenderer.invoke("github:sign-in"),
  connectGitHub: () => ipcRenderer.invoke("github:connect"),
  onGitHubEvent: (listener) =>
    ipcRenderer.on("github:event", (_event, event) => listener(event)),
});
