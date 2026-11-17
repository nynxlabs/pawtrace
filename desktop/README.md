# pawtrace desktop

A desktop window over `pawtrace serve` ([docs/api.md](../docs/api.md)). The shell is small on
purpose: it finds or starts a server, keeps the session token in memory, and shows whatever
front end that server serves at `/`. So when the front end changes, the app doesn't.
That front end is pawtrace's web app on densk-core (`../web/`). The preload also hands the page
densk-core's desktop bridge, so the tab strip becomes the frameless window's title row.

Electron, plain CommonJS (no build step for the shell), Apache-2.0 like the rest of the repo.

## Running it

```bash
cd desktop
npm install
npm start                         # local mode: pick a project folder the first time
npx electron . --project ../path/to/project
npx electron . --remote           # remote mode (below)
```

**Local mode** (the default) starts `pawtrace serve <project>` itself and stops it on quit.
The app remembers the project. How to run pawtrace and any extra `serve` flags go in the
settings file, `<config>/pawtrace/desktop.json` (on Linux `~/.config/pawtrace/desktop.json`):

```json
{
  "project": "/home/me/research/my-project",
  "command": ["uv", "run", "--project", "/home/me/src/pawtrace", "pawtrace"],
  "serveArgs": ["--notes", "/home/me/notes/my-project", "--edit-cmd", "myeditor {path}"]
}
```

Without `command`, the app runs `pawtrace` from `PATH`.

**Remote mode** (`--remote [FILE]`) is for data that lives on another machine. A helper on
that machine runs `pawtrace serve`, opens an ssh tunnel *to this machine's loopback*
(`ssh -R 127.0.0.1:P:127.0.0.1:P`), and writes the server URL to the session file over ssh:
`{"url": "http://127.0.0.1:P/?t=<token>"}`. The default file is
`$XDG_RUNTIME_DIR/pawtrace/remote.json`. The app follows that file: it connects when the
file appears, reconnects when it changes, and waits while the server is unreachable. The
machine with the data starts the connection, so this one needs no ssh key and no open
port, and nothing listens beyond loopback on either end.

**An already running server:** `PAWTRACE_URL=http://127.0.0.1:P/?t=<token> pawtrace`.

## What stays where

- **The token** is held by the main process and added to requests for the server's origin
  only (`X-Pawtrace-Token`). The page never gets it in a URL, so it isn't in history.
  Local mode reads it from `serve`'s URL file and deletes the file at once.
- **The session file** belongs in RAM: `$XDG_RUNTIME_DIR` is a tmpfs on Linux. Write it
  with mode 0600.
- **Chromium's profile** (cache, local storage, history) goes to `$XDG_RUNTIME_DIR` on
  Linux, and the page's session is in-memory on every platform, so nothing from a session
  is left on disk. Tabs and preferences that should survive go through the server's
  `/api/state`, on the machine with the data.
- **The settings file** holds paths and the window size, never secrets.
- Navigation stays on the server. Other links open in the system browser (http and https only).

## Machine identity (optional)

If `~/.config/pawtrace/device.json` exists (`{"name": "laptop1", "class": "laptop"}`), the
window's User-Agent ends with `PawtraceDevice/<name> PawtraceDeviceClass/<class>`, and the front
end sizes itself for that device class (a theme file's `laptop` blocks, [docs/api.md](../docs/api.md)
§ Theme file). The front end also takes the `NynxDeviceClass/<class>` token other apps' windows
send. `~/.config/nynx/device.json` (same shape) is also read if present. Without either file,
nothing changes.

## Window seams (optional)

If you tile pawtrace beside other apps that share its colours, the window can drop its outline
on the side it shares with them, and when the touching tab on either side is the active one,
the two windows draw one shared line between them (each draws half). It's off unless
`windowSeams` is in the settings file:

```json
{
  "windowSeams": {
    "neighborsDir": "{runtime}/my-seams",
    "edgesDir": "{runtime}/my-tab-edges"
  }
}
```

- **Linux:** something you run (for example a small service on your window manager's IPC)
  writes `<neighborsDir>/<pid>.json` for each window, keyed by the app's main process id:
  `{"left": bool, "right": bool, "leftPid": int|null, "rightPid": int|null}`. `left`/`right`
  say that side touches a partner app; `leftPid`/`rightPid` name it. It may add
  `"window": addr, "windows": {addr: {"screenLeft": bool, "screenRight": bool}}` to say which
  sides are the monitor's edge.
- **Windows:** pawtrace asks the komorebi tiling window manager (`komorebic state`) and treats
  the windows of `"apps": ["other.exe", …]` as partners. `"komorebic"` sets the path (default:
  from `PATH`).
- **Shared lines:** with `edgesDir`, pawtrace writes `<edgesDir>/<id>.json` =
  `{"left": bool, "right": bool}` (its first / last tab is active at that edge; id = pid on
  Linux, window handle on Windows) and reads its neighbour's. Without `edgesDir` you get the
  dropped outline only.

`{runtime}` is `$XDG_RUNTIME_DIR` (else the OS temp folder), `{tmp}` the OS temp folder, and a
leading `~` your home. The files hold booleans and ids only, no titles or content. Not on macOS.
Code: `seams.js`.

## Building

```bash
npm run dist:linux          # release/linux-unpacked (x64)
npm run dist:linux-arm64    # release/linux-arm64-unpacked
npm run dist:win            # release/win-unpacked
npm run dist:mac            # release/mac-arm64 (unsigned)
npm test                    # the connection tests (plain node)
```

The window has no frame, and the front end draws its own title row. Keys follow the front
end's tab keys: Ctrl+Q, Ctrl+R and Ctrl+0 belong to the page, so reload is F5. For a
test, `PAWTRACE_DESKTOP_SCREENSHOT=out.png` captures the window once the front end has
loaded, then quits without showing it.
