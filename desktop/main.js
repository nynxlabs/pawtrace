// pawtrace desktop: one window over `pawtrace serve` (docs/api.md). The window shows whatever front end
// the server serves at /, so a new front end needs no change here.
//
//   pawtrace                         local: start `pawtrace serve` for the last project (or pick a folder)
//   pawtrace --project DIR           local, for DIR
//   pawtrace --remote [FILE]         remote: a helper on the machine with the data opened a loopback tunnel to
//                                    this machine and wrote the server URL to FILE (default
//                                    $XDG_RUNTIME_DIR/pawtrace/remote.json); the app follows the file
//   PAWTRACE_URL=http://127.0.0.1:P/?t=…  pawtrace    an already running server
//
// Settings (no secrets) live in <config>/pawtrace/desktop.json: {"project", "command": ["pawtrace"],
// "serveArgs": [...], "bounds", "windowSeams"}. `command` is how to run pawtrace here (e.g. ["uv", "run", "--project",
// "/path/to/pawtrace", "pawtrace"]); serveArgs go after `pawtrace serve <project>` (e.g. --notes DIR);
// windowSeams turns on shared window edges with neighbouring apps (seams.js; off without it).
//
// Secrets stay in memory: the token is held by this process and added to the server's requests
// (X-Pawtrace-Token), never put in a URL the page keeps. On Linux Chromium's own profile (cache,
// history, local storage) goes to $XDG_RUNTIME_DIR, a RAM-backed folder, and the page's session is
// in-memory everywhere.
"use strict";

const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const conn = require("./connection");
const { deviceClassOf, userAgentSuffix } = require("./device");
const { startSeams } = require("./seams");

const PARTITION = "pawtrace"; // no "persist:" prefix: in-memory
const TITLE_BAR_HEIGHT = 40; // the frameless window's title row height (densk-core title-bar mode), as in densk
const configDir = path.join(app.getPath("appData"), "pawtrace");
const configFile = path.join(configDir, "desktop.json");

// Chromium's profile in RAM where the OS gives us a RAM folder (before anything touches userData).
const ram = conn.runtimeDir();
if (ram) {
  fs.mkdirSync(ram, { recursive: true, mode: 0o700 });
  app.setPath("userData", path.join(ram, "desktop-profile"));
  app.setPath("sessionData", path.join(ram, "desktop-profile"));
}
app.commandLine.appendSwitch("disable-features", "MediaRouter");

function flag(name) {
  const i = process.argv.indexOf(name);
  if (i < 0) return { present: false };
  const next = process.argv[i + 1];
  return { present: true, value: next && !next.startsWith("--") ? next : undefined };
}

function loadConfig() {
  try {
    const c = JSON.parse(fs.readFileSync(configFile, "utf8"));
    return c && typeof c === "object" ? c : {};
  } catch {
    return {};
  }
}

function saveConfig(c) {
  fs.mkdirSync(configDir, { recursive: true });
  const tmp = `${configFile}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(c, null, 2));
  fs.renameSync(tmp, configFile);
}

const config = loadConfig();
const remote = flag("--remote");
const projectFlag = flag("--project");
const mode = process.env.PAWTRACE_URL ? "url" : remote.present ? "remote" : "local";
const sessionFile = remote.value || conn.defaultSessionFile();

let win = null;
let seams = null; // seams.js, when the settings turn them on
let server = null; // {origin, token, child?}
let tokenFor = { origin: null, token: null }; // what the request hook adds

async function waiting(message) {
  if (!win || win.isDestroyed()) return;
  const file = path.join(__dirname, "waiting.html");
  if (!win.webContents.getURL().startsWith("file:")) await win.loadFile(file).catch(() => {});
  await win.webContents.executeJavaScript(`document.getElementById("msg").textContent = ${JSON.stringify(message)}`).catch(() => {});
}

async function reachable(s) {
  try {
    const r = await fetch(`${s.origin}/api/meta`, { headers: { "X-Pawtrace-Token": s.token }, signal: AbortSignal.timeout(4000) });
    return r.ok;
  } catch {
    return false;
  }
}

async function show(s) {
  server = s;
  tokenFor = { origin: s.origin, token: s.token };
  try {
    const r = await fetch(`${s.origin}/api/theme`, { headers: { "X-Pawtrace-Token": s.token } });
    const snap = await r.json(); // {available, theme: {colors}} (densk-core's theme snapshot)
    const cls = deviceClassOf(app.userAgentFallback);
    const oled = snap && snap.oled && (typeof snap.oled[cls] === "boolean" ? snap.oled[cls] : snap.oled.desktop === true);
    const bg = oled ? "#000000" : snap && snap.available && snap.theme && snap.theme.colors && snap.theme.colors.bg;
    if (typeof bg === "string" && /^#[0-9a-f]{6}$/i.test(bg)) win.setBackgroundColor(bg);
  } catch {
    /* default colour */
  }
  await win.loadURL(`${s.origin}/`);
}

// remote: follow the session file; reconnect when it changes or the server stops answering.
async function followRemote() {
  let current = null; // the server on screen, as origin|token
  const missing = `Waiting for the pawtrace server.\n\nA helper on the machine with your data writes its address to\n${sessionFile}\nThis window connects on its own once it's there.`;
  await waiting(missing);
  for (;;) {
    if (!win || win.isDestroyed()) return;
    const s = conn.readSessionFile(sessionFile);
    const key = s ? `${s.origin}|${s.token}` : null;
    if (!s) {
      if (current !== null) await waiting(missing);
      current = null;
    } else if (await reachable(s)) {
      if (key !== current) {
        current = key;
        await show(s);
      }
    } else if (current !== null || key !== current) {
      current = null;
      await waiting("The pawtrace server isn't answering. Waiting for it…");
    }
    await new Promise((r) => setTimeout(r, 2500));
  }
}

async function startLocal() {
  let project = projectFlag.value || config.project;
  if (!project || !fs.existsSync(path.join(project, "pawtrace.yaml"))) {
    await waiting("Pick a pawtrace project folder (the one with pawtrace.yaml).");
    const pick = await dialog.showOpenDialog(win, { title: "Open a pawtrace project", properties: ["openDirectory"] });
    if (pick.canceled || !pick.filePaths[0]) return app.quit();
    project = pick.filePaths[0];
  }
  config.project = project;
  saveConfig(config);
  const command = Array.isArray(config.command) && config.command.length ? config.command.map(String) : ["pawtrace"];
  const args = Array.isArray(config.serveArgs) ? config.serveArgs.map(String) : [];
  await waiting(`Starting pawtrace serve for ${project}…`);
  try {
    const s = await conn.startLocal({ command, project, args, log: (line) => process.stderr.write(line) });
    s.child.on("exit", () => {
      if (!app.isQuitting) void waiting("pawtrace serve stopped. Reopen the app to start it again.");
    });
    await show(s);
  } catch (err) {
    await waiting(`${err.message}\n\nInstall pawtrace (pip install pawtrace) or set "command" in\n${configFile}`);
  }
}

function buildMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "File",
        submenu: [
          {
            label: "Open project…",
            visible: mode === "local",
            click: () => {
              config.project = undefined;
              saveConfig(config);
              app.relaunch();
              app.quit();
            },
          },
          { label: "Open settings file", click: () => void shell.openPath(configFile) },
          { type: "separator" },
          { role: "quit", registerAccelerator: process.platform === "darwin" }, // Ctrl+Q is the page's close-tab key
        ],
      },
      { role: "editMenu" },
      {
        label: "View",
        // F5 reloads: Ctrl+R is the page's replace-tab key, Ctrl+0 its last-tab key.
        submenu: [{ role: "reload", accelerator: "F5" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "togglefullscreen" }],
      },
    ]),
  );
}

async function main() {
  if (!app.requestSingleInstanceLock()) return app.quit();
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  await app.whenReady();
  app.userAgentFallback = app.userAgentFallback + userAgentSuffix(app.getPath("home"));
  const ses = session.fromPartition(PARTITION);
  // The token rides on every request to the server, and only to it.
  ses.webRequest.onBeforeSendHeaders((details, cb) => {
    const headers = details.requestHeaders;
    if (tokenFor.origin && details.url.startsWith(tokenFor.origin + "/")) headers["X-Pawtrace-Token"] = tokenFor.token;
    cb({ requestHeaders: headers });
  });
  ses.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === "clipboard-sanitized-write"));

  ipcMain.on("pawtrace:info", (event) => {
    event.returnValue = { version: app.getVersion(), mode, platform: process.platform, titleBarHeight: TITLE_BAR_HEIGHT };
  });
  ipcMain.on("pawtrace:show", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.show();
    }
  });
  ipcMain.on("pawtrace:reconnect", () => {
    if (server) void show(server);
  });
  ipcMain.handle("pawtrace:seams", () => (seams ? seams.current() : { left: false, right: false, joinedLeft: false, joinedRight: false }));
  ipcMain.on("pawtrace:tab-edges", (_event, edges) => {
    if (seams && edges && typeof edges === "object") seams.setEdges(edges);
  });

  const bounds = config.bounds || {};
  win = new BrowserWindow({
    title: "pawtrace",
    width: bounds.width || 1280,
    height: bounds.height || 850,
    x: bounds.x,
    y: bounds.y,
    backgroundColor: "#1c1c1c",
    autoHideMenuBar: true,
    titleBarStyle: "hidden", // the front end draws the title row
    icon: [path.join(process.resourcesPath || "", "icon.png"), path.join(__dirname, "resources", "icon.png")].find((f) => fs.existsSync(f)),
    show: false,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, partition: PARTITION, spellcheck: false },
  });
  if (process.platform === "darwin") win.setWindowButtonVisibility(false);
  win.once("ready-to-show", () => {
    if (!process.env.PAWTRACE_DESKTOP_SCREENSHOT) win.show();
  });
  win.on("close", () => {
    config.bounds = win.getNormalBounds();
    saveConfig(config);
  });
  seams = startSeams(win, config.windowSeams, (payload) => {
    if (!win.isDestroyed()) win.webContents.send("pawtrace:seams", payload);
  });
  win.on("closed", () => {
    if (seams) seams.stop();
    seams = null;
  });
  // Stay on the server: anything else opens in the system browser (http and https only).
  const external = (url) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
  };
  win.webContents.setWindowOpenHandler(({ url }) => {
    external(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!server || !url.startsWith(server.origin + "/")) {
      event.preventDefault();
      external(url);
    }
  });
  buildMenu();

  // Test hook: capture the window to a PNG once the front end has loaded, then quit (no window shown).
  const shot = process.env.PAWTRACE_DESKTOP_SCREENSHOT;
  if (shot) {
    win.webContents.on("did-finish-load", () => {
      if (win.webContents.getURL().startsWith("file:")) return;
      setTimeout(async () => {
        const img = await win.webContents.capturePage();
        fs.writeFileSync(shot, img.toPNG());
        app.quit();
      }, Number(process.env.PAWTRACE_DESKTOP_SCREENSHOT_DELAY || 2500));
    });
  }

  if (mode === "url") {
    const s = conn.parseServerUrl(process.env.PAWTRACE_URL);
    delete process.env.PAWTRACE_URL;
    if (!s) await waiting("PAWTRACE_URL must be a loopback pawtrace URL with ?t=<token>.");
    else await show(s);
  } else if (mode === "remote") {
    void followRemote();
  } else {
    await startLocal();
  }
}

app.on("before-quit", () => {
  app.isQuitting = true;
  if (server && server.child) server.child.kill("SIGTERM");
});
app.on("window-all-closed", () => app.quit());
main().catch((err) => {
  dialog.showErrorBox("pawtrace couldn't start", String((err && err.stack) || err));
  app.quit();
});
