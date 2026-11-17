# pawtrace web front end

pawtrace's front end, built on [densk-core](https://github.com/nynxlabs/densk-core) (the shared UI core
of densk: a workbench with tabs, split panes and a sidebar, plus PDF, markdown, code, image and
drawing viewers and editors). `pawtrace serve` speaks densk's file API, so the core's
`createHttpHost()` talks to it directly. pawtrace adds its own pages as tabs with `pawtrace:` paths
(`src/App.tsx` lists them). The sidebar's "pawtrace" folder holds pawtrace's own pages (Settings, a
form over the server's user config); the project's folder starts with the project's pages (Overview,
Reviews, Figures), and each paper's page sits first inside that paper's folder.

```bash
git submodule update --init web/densk-core   # densk-core is private for now (see below)
cd web
bun install
bun run build        # → ../src/pawtrace/webui/app/ (gitignored; ships in the wheel as package data)
bun run typecheck
bun run check        # our CSS against the class names pdf.js and Excalidraw style globally
```

With a build in place, `pawtrace serve` serves it at `/` (else a small placeholder page). For
development, `bun run dev` proxies `/api`, `/f` and `/dz` to a `pawtrace serve --port 8457`. Open
the server's URL once with `?t=<token>` so the browser gets its session cookie.

Review forms are markdown, so marking one is editing it in the core's editor: tick a box, type a
note, and it saves as you type. The core's conflict bar ("Changed outside this app") steps in when
the file changed elsewhere, so another editor's work is never overwritten.

**densk-core is private for now.** Until it's public, this folder can't be built from a public
clone, and the branch that adds it waits. The other way would be to commit a pinned build of the
front end instead of the submodule, so the package would ship it without the core's source;
rebuilding it would still need the core.
