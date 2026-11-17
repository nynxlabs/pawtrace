// Where the app's pawtrace server is, and its session token. Plain Node (no Electron), so it's testable.
//
// Three ways in, tried in this order:
//   1. $PAWTRACE_URL: a server URL with ?t=<token> (a launcher that already started one).
//   2. remote: a session file, JSON {"url": "http://127.0.0.1:<port>/?t=<token>"}. A helper on the
//      machine that holds the data writes it here over ssh after opening a tunnel to this machine's
//      loopback. Keep the file in RAM ($XDG_RUNTIME_DIR, a tmpfs on Linux), never on disk.
//   3. local: start `pawtrace serve <project> --url-file <file>` here and read its URL.
// The URL must point at this machine's loopback: the token never goes over a network in the clear.
"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost|\[::1\]):(\d{1,5})\/?(\?.*)?$/;

/** {origin, token} from a server URL, or null when it isn't a loopback URL with a token. */
function parseServerUrl(raw) {
  if (typeof raw !== "string") return null;
  const url = raw.trim();
  if (!LOOPBACK.test(url)) return null;
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  const token = u.searchParams.get("t");
  if (!token || !/^[A-Za-z0-9_-]{16,128}$/.test(token)) return null;
  return { origin: u.origin, token };
}

/** The session file's server, or null (missing, unreadable, or not a loopback URL). */
function readSessionFile(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return null;
  }
  let url = text.trim();
  if (url.startsWith("{")) {
    try {
      url = JSON.parse(url).url;
    } catch {
      return null;
    }
  }
  return parseServerUrl(url);
}

/** A folder for things that must not reach disk: $XDG_RUNTIME_DIR (tmpfs) when there is one. */
function runtimeDir() {
  const base = process.env.XDG_RUNTIME_DIR;
  if (base && fs.existsSync(base)) return path.join(base, "pawtrace");
  return null;
}

function defaultSessionFile() {
  const dir = runtimeDir() || path.join(os.tmpdir(), `pawtrace-${process.getuid ? process.getuid() : "user"}`);
  return path.join(dir, "remote.json");
}

/** Start `pawtrace serve` for a project; resolves {origin, token, child} once its URL file appears. */
function startLocal({ command, project, args = [], timeoutMs = 30000, log = () => {} }) {
  const dir = runtimeDir() || fs.mkdtempSync(path.join(os.tmpdir(), "pawtrace-"));
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const urlFile = path.join(dir, `serve-${process.pid}.url`);
  fs.rmSync(urlFile, { force: true });
  const argv = [...command.slice(1), "serve", project, "--url-file", urlFile, ...args];
  const child = spawn(command[0], argv, { stdio: ["ignore", "ignore", "pipe"], cwd: project });
  child.stderr.on("data", (chunk) => log(String(chunk)));
  return new Promise((resolve, reject) => {
    const started = Date.now();
    let done = false;
    const finish = (err, value) => {
      if (done) return;
      done = true;
      clearInterval(timer);
      if (err) reject(err);
      else resolve(value);
    };
    child.on("error", (err) => finish(new Error(`couldn't run ${command.join(" ")}: ${err.message}`)));
    child.on("exit", (code) => finish(new Error(`pawtrace serve exited (code ${code}) before it was ready`)));
    const timer = setInterval(() => {
      const server = readSessionFile(urlFile);
      if (server) {
        fs.rmSync(urlFile, { force: true }); // the token stays in this process's memory only
        finish(null, { ...server, child });
      } else if (Date.now() - started > timeoutMs) {
        child.kill("SIGTERM");
        finish(new Error("pawtrace serve didn't start in time"));
      }
    }, 150);
  });
}

module.exports = { parseServerUrl, readSessionFile, runtimeDir, defaultSessionFile, startLocal };
