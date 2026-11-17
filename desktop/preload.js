// What the front end can learn about the desktop shell (window.pawtraceDesktop). No secrets: the
// session token never reaches the page; the shell adds it to the server's requests itself.
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

const info = ipcRenderer.sendSync("pawtrace:info");

// Also densk-core's desktop bridge (platform.ts DesktopBridge): the front end passes this object to
// configurePlatform, so the tab strip becomes the frameless window's title row.
contextBridge.exposeInMainWorld("pawtraceDesktop", {
  version: info.version,
  mode: info.mode, // "local" | "remote" | "url"
  platform: info.platform,
  titleBarHeight: info.titleBarHeight,
  windowControls: "hidden", // no system buttons drawn over the title row (Linux, Windows); macOS hides its own
  show: () => ipcRenderer.send("pawtrace:show"),
  // Window seams (desktop/seams.js; always "none" unless the settings file turns them on): which
  // sides touch a neighbouring app, whether the line there is shared, and this window's own tab edges.
  seams: () => ipcRenderer.invoke("pawtrace:seams"),
  onSeams: (callback) => ipcRenderer.on("pawtrace:seams", (_event, seams) => callback(seams)),
  tabEdges: (edges) => ipcRenderer.send("pawtrace:tab-edges", { left: !!(edges && edges.left === true), right: !!(edges && edges.right === true) }),
  // Ask the shell to reconnect (e.g. after the server restarted).
  reconnect: () => ipcRenderer.send("pawtrace:reconnect"),
});
