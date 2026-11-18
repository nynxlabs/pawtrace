# `pawtrace serve`: the local JSON API

`pawtrace serve` is the backend for pawtrace's web front end, and for anything else that
wants pawtrace's views over HTTP. It serves two things:

- **pawtrace views:** the overview, papers, notes and forms, figures and provenance,
  built from the project's plain files on every request;
- **a host interface:** spaces, files (list / stat / read / write), "open in my editor" and
  a small state store. A front end's host adapter implements its file, settings and
  persistence hooks with these.

The front end ([web/](../web/README.md), built on densk-core) is a static build, committed in
`src/pawtrace/webui/app/` and served at `/`: `index.html`, `assets/`, `pdfjs/` and its license
notices. Those files load without the token, since they hold no project data; every data route
needs it. Without a build the server shows a small placeholder page, and
`--frontend DIR` serves another build. The desktop app (`desktop/`) shows `/` and adds the token
to every request itself. Nothing here calls a model.

## Running it

```bash
pawtrace serve [PROJECT] [--notes DIR]... [--edit-cmd 'CMD {path}'] [--read-only | --write-checkout]
               [--theme FILE] [--by NAME] [--frame-ancestor ORIGIN]... [--port N] [--url-file PATH]
```

The server binds `127.0.0.1` only. Each run makes a new random token, and the server URL is
`http://127.0.0.1:<port>/?t=<token>`. We print the URL only to a terminal. Under systemd or a
pipe, `--url-file` writes it to a 0600 file for the front end (or its launcher) to read.
Settings that belong to one machine can also go in `$XDG_CONFIG_HOME/pawtrace/ui.yaml`
(`edit_cmd`, `theme`, `by`, `frame_ancestors`, `read_only`, `write_checkout`, `branches`,
`hide`, `projects.<name>.notes`), never in the project. `hide` is a list of file or folder
name globs (`["__init__.py", "*.pyc"]`) that the file tree, `/api/files/list` and find skip;
a hidden file still opens by its path. A project's own `projects.<name>.hide` wins over the
machine-wide list.

### Theme file

`/api/theme` sends the colours and sizes from a JSON theme file, re-read when it changes. The file
is `--theme FILE` (or `theme` in `ui.yaml`), else `$PAWTRACE_THEME`, else
`$XDG_CONFIG_HOME/pawtrace/theme.json`; without one the front end keeps its built-in dark theme.
`$XDG_CONFIG_HOME/nynx-theme/active.json`, which has the same shape, is also read if present.

```json
{
  "id": "my-theme", "label": "My theme", "appearance": "dark", "updatedAt": 1763400000000,
  "colors": {"bg": "#1c1c1c", "sidebar": "#282828", "text": "#dadada", "accent": "#dadada", "link": "#a68af9"},
  "text": {"desktop": {"base": 13, "ui": 13, "uiLg": 14, "uiSm": 12, "uiXs": 11, "ui2xs": 10, "meta": 11,
                       "timestamp": 10, "chat": 13, "code": 12, "chatMeta": 10, "tiny": 9}},
  "oled": {"desktop": false, "laptop": true}
}
```

Only `colors` is required (`#rrggbb` values; missing keys keep the built-in ones); `syntax`
colours code. `text`, `layout`, `tabs`, `window` and `oled` hold one block per device class
(`desktop`, `laptop`, `tablet`, `phone`); a `text` block counts only with all its keys, in whole px,
and a laptop without a block uses the desktop's. The desktop app names its class from
`~/.config/pawtrace/device.json` ([desktop/README.md](../desktop/README.md)).

## Guards

- `Host` must be `127.0.0.1`, `localhost` or `::1` (with any port), otherwise 403. This
  blocks DNS rebinding.
- Data routes (`/api/…`, `/f/…`, `/dz/…`) need the token, from any of: the `X-Pawtrace-Token`
  header; `?t=` on a URL; or the session cookie. Loading any front-end page with `?t=<token>` sets
  the cookie (`pawtrace_<port>`, HttpOnly, SameSite=Strict, no expiry, so it ends with the
  browser session) and redirects to the same page without the token, so the address bar,
  history and the page's scripts never hold it. The front end's own requests (fetch,
  EventSource, pdf.js ranges, images) then carry it by themselves. Otherwise 403.
- POST bodies must be `Content-Type: application/json` and a JSON object (otherwise 415 or
  400), at most 32 MiB.
- Responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and `Cross-Origin-Resource-Policy: same-origin`. File
  bodies (`/api/files/read`, `/f/*`) get `Content-Security-Policy: …; sandbox`.
- Errors are `{"error": "…"}` with a 4xx/5xx status.

## densk's file API (what the front end uses)

The front end is densk-core's workbench, and the core's `createHttpHost()` talks to these routes
as they are. They show one tree: each notes folder and each checkout is a top-level folder, named
by its label and addressed by a slug (`notes`, `main`, `feature-x`, …; `/api/meta`'s
`spaces[].tree`). The pawtrace views give every file's tree path as `file.tree`.

| Method | Path | Returns / does |
|---|---|---|
| GET | `/api/tree` | `[{name, path, dir?}]`: notes folders first, then checkouts; hidden files and symlinks that leave a space are left out |
| GET | `/api/file?path=P` | the bytes. Text files (markdown, code, data, text) carry `x-version` (their sha256) and `x-densk-writable` (`1`/`0`). One `Range: bytes=…` gives 206 / 416 (pdf.js) |
| PUT | `/api/file?path=P` | body = the new text (UTF-8, up to 8 MiB), `x-base-version: <version read>` or `new`. 200 `{version}`; 409 `{error, version}` when the file changed (or exists, for `new`), with the version on disk now; 403 for a read-only space or a generated file |
| GET | `/api/events` | server-sent events: `change` `{paths, tree, all?}` (a watcher polls the spaces every 1.5 s), `theme` (a snapshot, when the theme file changes); a keep-alive comment every 15 s |
| GET | `/api/find?q=&limit=` | `[{path}]`, file names that start with the query first |
| GET | `/api/theme` | densk-core's theme snapshot: `{available: true, rev, theme: {id, colors, syntax}, text?, layout?, tabs?}` from the theme file, or `{available: false}` |

`/api/tree?path=` (lazy folders), `/api/backlinks`, `/api/mounts` and `/api/git/*` answer 404
(not offered).

## Space-addressed file access

### Spaces

Every file lives in a **space**, addressed by a path relative to the space's root:

- `co:<id>`: one checkout of the project (the git worktrees that hold a `pawtrace.yaml`;
  `co:0` is the one the server was started on);
- `notes:<id>`: a notes folder given with `--notes` (intake and review forms, figure
  images, kept outside the repo).

Paths are resolved, symlinks included, and must stay inside the space. Hidden files and
folders are never listed or served. Notes folders are writable unless `--read-only`;
checkouts are read-only unless `--write-checkout`. Generated files (`STATUS.md`,
`QUESTIONS.md`, each paper's `SPEC.md`) are never writable.

| Method | Path | Returns / does |
|---|---|---|
| GET | `/api/spaces` | `{spaces: [{id, kind: checkout|notes, label, path, writable}]}` |
| GET | `/api/files/list?space=&path=` | one folder: `{space, path, entries: [{name, path, type: dir|file, mtime, size?, kind?}]}`; `kind` is `markdown`, `pdf`, `image`, `data`, `code`, `text` or `binary` |
| GET | `/api/files/stat?space=&path=` | `{name, path, type, mtime, size, kind, space, abspath, writable, sha256}` |
| GET | `/api/files/read?space=&path=` | the raw bytes with their content type, plus `X-Pawtrace-Sha256` and `X-Pawtrace-Mtime` headers (up to 64 MiB) |
| POST | `/api/files/write` | `{space, path, text, base_sha256}`: UTF-8 text, up to 8 MiB. `base_sha256` is the sha256 the editor read; `null` creates a new file. 409 when the file changed (or exists, or vanished), 403 when the space or file isn't writable, 404 when the folder doesn't exist. The write is atomic (temp file + rename); a symlinked path is written at its target, so the link stays. Returns `{space, path, sha256, mtime}` |
| POST | `/api/files/open` | `{space, path}`: runs `--edit-cmd` with `{path}` = the absolute path (argv, no shell); 409 when there's no `--edit-cmd` |
| GET | `/api/state?key=` | `{key, value}` (without `key`: `{state: {…}}`) |
| POST | `/api/state` | `{key, value}`: stores a JSON value (`null` deletes it). Keys are 1–64 chars of `a-z 0-9 . _ -`; each value is at most 256 KiB, the whole store at most 1 MiB. One 0600 file per project under `$XDG_STATE_HOME/pawtrace/ui/`, outside the project |
| GET | `/api/stamp?co=` | `{stamp, theme}`: both change when the project's files, the notes or the theme change, so a front end knows when to refetch |
| POST | `/api/quit` | stops the server |

### Which file routes to use

The front end uses densk's file API (above). The space-addressed routes here are for tools that
think in spaces (a checkout or a notes folder plus a path) and for `POST /api/files/open`, which
opens a file in the person's editor. Both enforce the same rules: they stay inside the spaces,
writes are compare-and-swap on the sha256, the read-only flags apply, and generated files are
never written.

## pawtrace views

`co` is a checkout id from `/api/meta` (default `"0"`).

| Method | Path | Returns / does |
|---|---|---|
| GET | `/`, `/assets/…`, `/pdfjs/…` | the front end's files (no token; host check; strict CSP; unknown extensionless paths get `index.html`; `/app/…` redirects here) |
| GET | `/api/meta` | `{version, project, checkouts: [{id, label, branch, head, path}], notes: [{id, label, path}], spaces, can_edit, frame_ancestors, by}` |
| GET | `/api/home?co=` | `{project, profile, modules_on, papers: [{id, title, year, stages: [{name, state, note}], items, items_by_status, candidates, open_items, open_questions, panels: {n, pass, fail, unchecked}, rights: {license, access, ai_tools, redistribute, open_version, copy_url}, folder}]}`; `folder` is the paper's folder in the file tree (null when the tree doesn't show it) |
| GET | `/api/settings` | the Settings page's view of the user config (`ui.yaml`): `{values: {edit_cmd, theme, by, read_only, write_checkout, branches, frame_ancestors, hide, notes}, running, pending, overrides, version, exists, comments, writable, project, file}`; `notes` is this project's `projects.<name>.notes`; `running` is what serve runs with now (flags included); `pending` names saved keys that need a restart; 404 without a settings file, 400 when it doesn't parse |
| POST | `/api/settings` | `{values, version}` → the same view after a compare-and-swap write (`version`: the sha256 read, or `new`; 409 when it changed). Keys the page doesn't know and other projects are kept, empty values drop their key, comments are not kept. `edit_cmd` and `by` apply at once |
| GET | `/api/paper?co=&id=` | `{id, title, year, authors, stages, rights, rights_lines, ai_never, items: [{id, kind, status, section, text, value, units, plain, entries}], statuses, entries, questions: [{…, status}], panels: [{id, title, runs, latest, report: {pass, n_passed, n_required, gates: [{id, pass, required, check, inputs, why, error}], metrics}, overlay, overlay_path, overlay_file, paper_figure, paper_figure_note, references}], lint, summary}` |
| GET | `/api/notes` | `{roots, notes: [{root, rel, file, title, kind: review|interview|note, counts, total, mtime}]}` |
| GET | `/api/note?root=&rel=` | `{root, rel, path, file, title, text, images, kind, blocks: [{kind: mark|answer, id, head, start, line, end, options: [{label, checked}], choice, note}], counts, total}`; `start` / `line` / `end` are 1-based lines (block head, mark or answer line, last note line) |
| GET | `/api/figures?co=` | `{references: [{panel, paper, reference, ai_never, path, abspath, file, format, series, digitized_from, figure, provenance: {exists, series, method, tool, by, date, assist_used, image, reviewed_by, review_date, review_complete, residual, history, readme}}], figures: [{root, rel, file, name, ext, size, path, url}], readmes: [{root, rel, file, title}], sessions}` |
| GET | `/f/<key>?t=` | an image or PDF a view listed, by the opaque key in its `url` field (also for files outside every space, e.g. an images folder set with `${var}`) |
| POST | `/api/overlay` | `{co, panel}`: `pawtrace overlay` for the latest run; returns `{out, run, url, file, warnings}` |
| POST | `/api/digitize/start` | `{co, mode: new|review, image: {root, rel}, panel, reference, out, by, page, crop, options}` → `{id, title, out, note}`. The output must land in a checkout or a notes folder |
| GET/POST | `/dz/<id>/…` | one digitize session: its page (`/dz/<id>/?t=`), `image`, `api/session`, `api/place`, `api/preview`, `api/save`, `api/quit` |

The digitize page is the one page the server still serves. A front end shows it in a frame.
Its CSP allows framing by this server's own origin and by any origin listed with
`--frame-ancestor` (wildcards are refused).

**Review forms** in notes folders are markdown: a block per item with a bold head line
(``**`item.id`** · §2``) and a mark line (`- [ ] agree  - [ ] disagree  - [ ] unsure  note: …`).
An interview is bold question heads, each followed by `answer:`. `/api/note` gives each
block's line numbers, so a front end can show marks next to the text, or rewrite one mark
line and save with `/api/files/write`.
