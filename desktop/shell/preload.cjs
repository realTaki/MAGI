"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("magiDesktop", {
  startLocal: () => ipcRenderer.invoke("asp:start-local"),
  windowControl: (action) => ipcRenderer.invoke("window:control", action),
  showChooser: () => ipcRenderer.invoke("app:show-chooser"),
});
