# pawtrace web front end

pawtrace's front end, built on [densk-core](https://github.com/nynxlabs/densk-core) (the shared UI core
of densk: a workbench with tabs, split panes and a sidebar, plus PDF, markdown, code, image and
drawing viewers and editors). `pawtrace serve` speaks densk's file API, so the core's
`createHttpHost()` talks to it directly. pawtrace adds its own pages as tabs with `pawtrace:` paths
(`src/App.tsx` lists them). The sidebar's "pawtrace" folder holds pawtrace's own pages (Settings, a
form over the server's user config); the project's folder starts with the project's pages (Overview,
Reviews, Figures), and each paper's page sits first inside that paper's folder.

Review forms are markdown, so marking one is editing it in the core's editor: tick a box, type a
note, and it saves as you type. The core's conflict bar ("Changed outside this app") steps in when
the file changed elsewhere, so another editor's work is never overwritten.

**The build is committed.** `src/pawtrace/webui/app/` holds a ready-made build of this folder, so
`pip install`, `uv sync` and `pawtrace serve` work from a plain clone, and nothing here needs to be
built to use pawtrace. `pawtrace serve` serves it at `/` (or `--frontend DIR` for another build; with
no build at all, a small placeholder page). The build carries its own license notices:
`THIRD-PARTY-NOTICES.txt` (densk-core's LICENSE and NOTICE, and the license text of every npm package
compiled in, plus pdf.js's run-time assets) and `licenses.json` (the same list, with the densk-core
commit it was built from).

The build carries permissive licenses only. pdf.js's Liberation Sans fonts (GPL-2.0 with a font
exception) are left out (`pdfjsAssets({ liberationFonts: false })` in `vite.config.ts`), so a PDF
that names Helvetica or Arial without embedding it is drawn with the system's own sans font
(Helvetica, Arial, Liberation Sans, Arimo, Nimbus Sans, DejaVu Sans, … whichever is installed, else
the browser's sans-serif); pdf.js never asks for the missing files. Embedded fonts and pdf.js's
other standard fonts (Foxit's, BSD) are unchanged.

### Rebuilding it

Only needed after changing `web/src/` or bumping densk-core. densk-core is a submodule
(`web/densk-core`) that clones skip (`update = none`); it's private for now, so this step needs
access to it. Without it the folder stays empty, and nothing else uses it.

```bash
git submodule update --init --checkout web/densk-core
cd web
bun install --frozen-lockfile
bun run build        # → ../src/pawtrace/webui/app/, with THIRD-PARTY-NOTICES.txt and licenses.json
bun run typecheck
bun run check        # our CSS against the class names pdf.js and Excalidraw style globally
cd .. && uv run --group dev pytest -q tests/test_webui_bundle.py
```

Commit the whole `src/pawtrace/webui/app/` folder together with the change that needed it (file
names carry content hashes, so a rebuild replaces most of `assets/`); a densk-core bump and its
rebuild go in one commit, and `tests/test_webui_bundle.py` fails when the build's densk-core commit
isn't the pinned one. The build fails on a package whose license isn't a permissive one
(`web/licenses.ts` lists them); review it and add it to `reviewed` there with the reason, or drop
the package.

For development, `bun run dev` proxies `/api`, `/f` and `/dz` to a `pawtrace serve --port 8457`. Open
the server's URL once with `?t=<token>` so the browser gets its session cookie.
