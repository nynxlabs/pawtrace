// Window seams (optional, off unless the settings file turns them on): where the window is tiled
// against another app that wears the same colours, the front end drops its window outline on that
// side, and when one of the two touching edge tabs is active the two windows share one line, each
// drawing half (densk-core's shell.ts, data-window-seam-*). The shell's part is learning the
// neighbours and passing them on, through two kinds of small files:
//
// - **neighbours** — which sides touch one of the partner apps, and that app's id per side.
//   Linux: a seams service of your own writes <neighborsDir>/<pid>.json for each window
//   ({"left":bool,"right":bool,"leftPid":int|null,"rightPid":int|null}, optionally
//   "window" + "windows":{addr:{"screenLeft":bool,"screenRight":bool}}); we read the one for our pid.
//   Windows: we ask the komorebi tiling window manager (`komorebic state`) and count the windows of
//   the `apps` executables as partners; ids are window handles.
// - **tab edges** — <edgesDir>/<id>.json = {"left":bool,"right":bool}: whether that window's first /
//   last tab is active and flush at that edge. We write ours (atomically, only on change, removed on
//   quit) and read the neighbour's. Two booleans, no titles or content.
//
// Settings (<config>/pawtrace/desktop.json), all optional; no "windowSeams" key = no seams:
//   "windowSeams": {
//     "neighborsDir": "{runtime}/my-seams",       Linux: where the seams service writes
//     "edgesDir": "{runtime}/my-tab-edges",       shared lines (left out: outlines only)
//     "komorebic": "komorebic.exe",               Windows: komorebic to run (default from PATH)
//     "apps": ["other.exe", "pawtrace.exe"]       Windows: partner executables
//   }
// `{runtime}` = $XDG_RUNTIME_DIR (else the OS temp dir), `{tmp}` = the OS temp dir, a leading `~` =
// home. macOS: nothing (no tiler reports window rects).
"use strict";

const { execFile } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const NO_SEAMS = Object.freeze({ left: false, right: false, leftId: null, rightId: null });
const NO_EDGES = Object.freeze({ left: false, right: false });
const TOUCH_PX = 2; // komorebi: edges this close (physical px) touch
const WATCH_DEBOUNCE_MS = 8;

function expandPath(value, env = process.env) {
  if (typeof value !== "string" || !value.trim()) return null;
  const runtime = env.XDG_RUNTIME_DIR || os.tmpdir();
  let out = value.trim().replaceAll("{runtime}", runtime).replaceAll("{tmp}", os.tmpdir());
  if (out === "~" || out.startsWith("~/")) out = path.join(os.homedir(), out.slice(1));
  return path.resolve(out);
}

/** The usable seam settings for this platform, or null (off). */
function seamSettings(raw, platform = process.platform, env = process.env) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const edgesDir = expandPath(raw.edgesDir, env);
  if (platform === "linux") {
    const neighborsDir = expandPath(raw.neighborsDir, env);
    return neighborsDir ? { kind: "file", neighborsDir, edgesDir } : null;
  }
  if (platform === "win32") {
    const apps = Array.isArray(raw.apps) ? raw.apps.filter((a) => typeof a === "string" && a).map((a) => a.toLowerCase()) : [];
    if (!apps.length) return null;
    const komorebic = typeof raw.komorebic === "string" && raw.komorebic ? raw.komorebic : "komorebic.exe";
    return { kind: "komorebi", apps: new Set(apps), komorebic, edgesDir };
  }
  return null;
}

/** Linux: a seams-service file's text → seams. Anything malformed = no seams. */
function seamsFromFile(text) {
  try {
    const v = JSON.parse(text);
    if (!v || typeof v !== "object") return NO_SEAMS;
    const { left, right, leftPid, rightPid, window: key, windows } = v;
    const idOk = (id) => id === null || id === undefined || Number.isInteger(id);
    if (typeof left !== "boolean" || typeof right !== "boolean" || !idOk(leftPid) || !idOk(rightPid)) return NO_SEAMS;
    const seams = {
      left,
      right,
      leftId: left && Number.isInteger(leftPid) ? leftPid : null,
      rightId: right && Number.isInteger(rightPid) ? rightPid : null,
    };
    // Which sides are the monitor's edge, when the service says (the page can't tell on Wayland).
    if (windows && typeof windows === "object") {
      const keys = Object.keys(windows);
      const k = typeof key === "string" ? key : keys.length === 1 ? keys[0] : undefined;
      const rec = k !== undefined ? windows[k] : undefined;
      if (rec && typeof rec.screenLeft === "boolean" && typeof rec.screenRight === "boolean") {
        seams.screenLeft = rec.screenLeft;
        seams.screenRight = rec.screenRight;
      }
    }
    return seams;
  } catch {
    return NO_SEAMS;
  }
}

const focusedOf = (ring) => ring && ring.elements && ring.elements[ring.focused || 0];

/** Windows: seams of window `hwnd` in a komorebi state, partners = windows of `apps` (lowercase exe names). */
function seamsFromKomorebi(state, hwnd, apps) {
  for (const monitor of (state && state.monitors && state.monitors.elements) || []) {
    const ws = focusedOf(monitor.workspaces);
    const containers = (ws && ws.containers && ws.containers.elements) || [];
    const ours = containers.find((c) => c.windows && (c.windows.elements || []).some((w) => w.hwnd === hwnd));
    const rect = ours && ours.windows.elements.find((w) => w.hwnd === hwnd).rect;
    if (!ours || !rect) continue;
    const sides = { left: [], right: [] };
    for (const c of containers) {
      if (c === ours) continue;
      const other = focusedOf(c.windows);
      const r = other && other.rect; // komorebi: right/bottom are width/height
      if (!r || !(r.top < rect.top + rect.bottom && rect.top < r.top + r.bottom)) continue;
      if (Math.abs(r.left + r.right - rect.left) <= TOUCH_PX) sides.left.push(other);
      if (Math.abs(r.left - (rect.left + rect.right)) <= TOUCH_PX) sides.right.push(other);
    }
    const partners = (ws) => ws.length > 0 && ws.every((w) => apps.has(String(w.exe || "").toLowerCase()));
    const left = partners(sides.left);
    const right = partners(sides.right);
    return {
      left,
      right,
      leftId: left && sides.left.length === 1 ? sides.left[0].hwnd : null,
      rightId: right && sides.right.length === 1 ? sides.right[0].hwnd : null,
    };
  }
  return NO_SEAMS;
}

/** A tab edges file's text → {left, right}, or null unless both are booleans. */
function parseEdges(text) {
  try {
    const v = JSON.parse(text);
    return v && typeof v.left === "boolean" && typeof v.right === "boolean" ? { left: v.left, right: v.right } : null;
  } catch {
    return null;
  }
}

/** Joined = the seam's line is shared on that side: a seam, and our edge tab or the neighbour's facing one active. */
function joinedOf(seams, own, edgesOf) {
  return {
    joinedLeft: seams.left && (own.left || (seams.leftId !== null && (edgesOf(seams.leftId) || {}).right === true)),
    joinedRight: seams.right && (own.right || (seams.rightId !== null && (edgesOf(seams.rightId) || {}).left === true)),
  };
}

/** What the page gets (densk-core DesktopSeams). */
function payloadOf(seams, own, edgesOf) {
  const out = { left: seams.left, right: seams.right };
  if (typeof seams.screenLeft === "boolean" && typeof seams.screenRight === "boolean") {
    out.screenLeft = seams.screenLeft;
    out.screenRight = seams.screenRight;
  }
  return { ...out, ...joinedOf(seams, own, edgesOf) };
}

function windowId(win, platform = process.platform) {
  if (platform === "linux") return process.pid; // the pid the seams service keys by
  const h = win.getNativeWindowHandle();
  return h.length >= 8 ? Number(h.readBigUInt64LE(0)) : h.readUInt32LE(0);
}

function watchDir(dir, onChange) {
  let timer;
  let watcher;
  try {
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    watcher = fs.watch(dir, () => {
      clearTimeout(timer);
      timer = setTimeout(onChange, WATCH_DEBOUNCE_MS);
    });
    watcher.on("error", () => watcher.close());
  } catch {
    /* the 1 s poll still runs */
  }
  return () => {
    clearTimeout(timer);
    if (watcher) watcher.close();
  };
}

/**
 * Starts seams for `win` with the settings' `raw` windowSeams block. `send(payload)` gets the page's
 * seams whenever they change. Returns {current(), setEdges(edges), stop()}, or null when off.
 */
function startSeams(win, raw, send) {
  const settings = seamSettings(raw);
  if (!settings) return null;
  const id = windowId(win);
  const edgesFile = (other) => (settings.edgesDir ? path.join(settings.edgesDir, `${other}.json`) : null);
  const readEdges = (other) => {
    const f = edgesFile(other);
    if (!f) return null;
    try {
      return parseEdges(fs.readFileSync(f, "utf8"));
    } catch {
      return null;
    }
  };
  let seams = NO_SEAMS;
  let own = NO_EDGES;
  let written = "";
  let sent = "";
  let stopped = false;
  let timer;
  let busy = false;

  const writeOwn = () => {
    const f = edgesFile(id);
    if (!f) return;
    const text = JSON.stringify({ left: own.left, right: own.right });
    if (text === written && fs.existsSync(f)) return;
    try {
      fs.mkdirSync(settings.edgesDir, { recursive: true, mode: 0o700 });
      fs.writeFileSync(`${f}.tmp`, text);
      fs.renameSync(`${f}.tmp`, f);
      written = text;
    } catch {
      written = ""; // a neighbour reading it can block the rename on Windows; retried next update
    }
  };
  const current = () => payloadOf(seams, own, readEdges);
  const update = () => {
    if (stopped || win.isDestroyed()) return;
    writeOwn();
    const payload = current();
    const key = JSON.stringify(payload);
    if (key === sent) return;
    sent = key;
    send(payload);
  };

  const readNeighbors = () =>
    new Promise((resolve) => {
      if (settings.kind === "file") {
        fs.readFile(path.join(settings.neighborsDir, `${id}.json`), "utf8", (err, text) => resolve(err ? NO_SEAMS : seamsFromFile(text)));
        return;
      }
      execFile(settings.komorebic, ["state"], { windowsHide: true, timeout: 1500, maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
        if (err) return resolve(null);
        try {
          resolve(seamsFromKomorebi(JSON.parse(stdout), id, settings.apps));
        } catch {
          resolve(null);
        }
      });
    });
  const schedule = (ms) => {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(poll, ms);
  };
  const poll = async () => {
    if (busy || stopped || win.isDestroyed()) return;
    if (!win.isVisible() || win.isMinimized()) return schedule(1000);
    busy = true;
    const next = await readNeighbors();
    busy = false;
    seams = next || NO_SEAMS;
    update();
    schedule(next ? 1000 : 10_000); // komorebi not running: check back less often
  };
  const soon = () => schedule(150);
  for (const ev of ["move", "resize", "show", "restore"]) win.on(ev, soon);
  const unwatch = [
    settings.kind === "file" ? watchDir(settings.neighborsDir, () => void poll()) : () => {},
    settings.edgesDir ? watchDir(settings.edgesDir, update) : () => {},
  ];
  schedule(0);

  return {
    current,
    setEdges(edges) {
      own = { left: !!(edges && edges.left === true), right: !!(edges && edges.right === true) };
      update();
    },
    stop() {
      stopped = true;
      clearTimeout(timer);
      for (const u of unwatch) u();
      const f = edgesFile(id);
      for (const p of f ? [f, `${f}.tmp`] : []) {
        try {
          fs.rmSync(p, { force: true });
        } catch {
          /* locked or gone */
        }
      }
    },
  };
}

module.exports = { startSeams, seamSettings, seamsFromFile, seamsFromKomorebi, parseEdges, joinedOf, payloadOf, expandPath, NO_SEAMS };
