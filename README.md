# pawtrace

> A modular pipeline for rebuilding and checking published computational research, for people and AI agents.

pawtrace takes a paper from reading to checked figures and a faithfulness audit:
**intake → foundations → fill the gaps → build → run → check → audit**. It's a pipeline,
not a framework: the paper's model code (simulators, controllers, training loops) lives
in your project's repo; pawtrace records what the paper says, how each gap was filled,
what ran, and whether the results match the paper's figures.

Work in progress (v0.1: the core plus the extract, seed-stats, preregistration,
panel-claims and author-questions modules). Scope and design notes: `docs/design.md`.

## What's in the core

| piece | files | what it does |
| --- | --- | --- |
| spec + ledger | `papers/<paper>/spec.yaml`, `ledger.yaml` | every detail the paper states (with its section) or leaves out; the code-use policy; how each gap was filled, with source and reason; deviations proposed → tested → adopted |
| panel contract | `papers/<paper>/panels/<panel>.yaml` | names your function `fn(config, seed) -> data`, with a `paper_only` and an `adopted` config |
| run records | `runs/<paper>/<panel>/<id>.json` (gitignored) | one JSON per run, keyed by commit + config hash + seed |
| gates | `metrics:` + `gates:` in the panel YAML | a shared library of ops and checks; each panel's gates are config with explicit tolerances and a `why` |
| digitization formats | `references:` in the panel YAML | `curves-json`, `wpd-json`, `wpd-csv`, `engauge-csv`, `table-csv` and `json-table` (bars/tables) |
| digitizer | `pawtrace digitize` | trace a figure by hand in a local web page, or check and fix an existing digitization (`--review`); saves curves-json with provenance |
| overlays | `pawtrace overlay` | the run and the digitized paper on the same axes, versioned PNGs |
| status | `pawtrace status --write` | a generated status page — never hand-edited |
| playbooks | `pawtrace playbook <stage>` | step-by-step instructions per stage that a person or an agent can follow alone |
| paper rights | `rights:` in `papers/<paper>/spec.yaml` | the paper's license, access terms, and what the project may do with its content; lint errors without it and when the rules don't allow what the project does |

Optional modules (extract, sensitivity, preregistration, source log, dependency graph,
seed stats, paper tables, …) are listed by `pawtrace modules` and turned on in
`pawtrace.yaml`; profiles `minimal`, `standard`, `strict` are presets. Built so far:
the core plus extract, seed-stats, preregistration, panel-claims and author-questions;
the rest are registered so configs can already name them.

## Extraction (optional module)

The `extract` module (group `intake`; on in `standard` and `strict`) pulls a paper's
text, equations and figures out of its PDF, so they don't have to be retyped.

```bash
uv run pawtrace extract run smith2024 path/to/paper.pdf
uv run pawtrace extract show smith2024
uv run pawtrace extract seed smith2024 --kind equation --dry-run
```

- **output:** `papers/<paper>/source/` holds the PDF, `manifest.json` (backend, hashes,
  the rights snapshot, counts, warnings), `text.md`, `sections.json`, `equations.json`,
  `figures.json`, the equation and figure images, and `candidates.yaml`. The folder has
  its own `.gitignore` (`*`), and `pawtrace init` ignores `papers/*/source/` too.
- **seed:** adds candidate items to `spec.yaml` with `status: candidate` and a pointer to
  the extraction. Nothing is `stated` until a person reviews it. Lint warns on unreviewed
  candidates and on candidates seeded from an older extraction.
- **backends:**
  - `pdfium` (default): offline and CPU-only, using pypdfium2 (`uv sync --extra extract`).
    It reads text in reading order, drops running heads, finds numbered display
    equations (text and a PNG crop), and splits figures into panels by letter. It's a
    heuristic: scanned PDFs, unnumbered equations and unusual layouts need the other backend.
  - `command`: runs a PDF-to-markdown converter you installed (marker, MinerU, docling, or
    a script around a local OCR model). You set it as an argv list in `pawtrace.yaml`
    under `extract.command`, with `{pdf}` and `{out}`. `extract.formula_command` (with
    `{image}`) can fill in LaTeX for equation images from any backend. pawtrace never
    calls a model itself and needs no GPU or network.
- **rights:** extraction refuses without a `rights:` block. Extracted content stays local
  unless the paper's license or a recorded permission allows it.

## Digitizing figures by hand

`pawtrace digitize` is a small figure digitizer for people. It serves one page on
loopback (`127.0.0.1`, a random port, a session token in the URL) and writes curves-json,
which panels read as a reference like any other format. Everything stays local: no
model calls and no uploads, and the image itself is never written into the output.

```bash
uv run pawtrace digitize figs/fig3.png --out papers/smith2024/digitization/fig3.json
uv run pawtrace digitize paper.pdf --page 4 --crop 60,80,300,260 --out fig3.json   # PDF page + crop (points, top-left)
uv run pawtrace digitize --review smith2024/fig3 paper        # check and fix a panel reference over its figure
```

- **calibrate:** click two x ticks and two y ticks and type their values; each axis is
  linear or log. The four points define the axes, so rotated and skewed plots work too.
- **trace:** named series, each a `curve`, `scatter` or `bars` series (bars need a label
  per point; list them once and clicks take them in order). Error bars per point. Drag to
  move, Delete to remove, Ctrl+Z / Ctrl+Shift+Z, Ctrl+S to save. Pinch or Ctrl+wheel
  zooms, two-finger scroll or drag pans, a loupe shows the pixels under the cursor.
- **colour snap (off by default):** moves a click to the nearest pixel of a picked colour.
  It's plain pixel matching, and the saved file records when it was used. For papers whose
  `rights.rules.ai_tools` is `never`, the command prints a note.
- **provenance** in `meta.digitization`: `tool: pawtrace digitize`, `method: manual`,
  `by: human` (plus `--by NAME`), date, the calibration, the image's file name and
  sha256, and the snap settings. Each series keeps its clicked pixels in `meta.px`.
- **review:** `--review <panel> <reference>` reads the reference file (any format), places
  its points on the figure once the axes are calibrated, and shows the original points
  under the edited ones. Points you don't move keep their exact values. The page shows the
  residual live (points moved, added and removed, max |dy|, RMS); saving records
  `meta.review` (`reviewed_by: human` once you tick *mark reviewed*, the date, the
  original's path, format, sha256 and git commit, the residual). A curves-json reference is
  rewritten in place with the previous version in `meta.history`; other formats are saved
  next to it as `<name>.json`, and the command prints the `path:`/`format:` change for
  the panel. `--options '{...}'` replaces the reference's reader options for the review
  (e.g. a table-csv `err:` mapping), and `--out` writes the result somewhere else. A later review of the same image starts from the saved calibration.
- **the figure:** pass the image, or set `figure: {image, page, crop, dpi}` on the panel or
  on one reference (`${var}` paths work, so restricted figures can stay outside the repo).
- **opening the page elsewhere:** `--browser` opens it here; `--open-cmd 'CMD {url}'` runs
  any opener (`{url}`, `{port}`), e.g. a script that forwards the port over ssh to another
  machine's loopback and opens it there. The URL is printed only to a terminal: under
  systemd or a pipe we print the port without the token (`--url-file PATH` writes the full
  URL to a 0600 file), and `--open-cmd` also gets it in `$PAWTRACE_URL`.

## The local API

`pawtrace serve [PROJECT]` serves pawtrace's views and the project's files as a local JSON
API, for a web front end or any other tool. It uses the same guards as `digitize`
(loopback only, a session token, JSON-only POSTs), makes no model calls, and reads the
files fresh on every request. It writes only when asked: a file in a writable space, an
overlay PNG, or a digitization.

```bash
uv run pawtrace serve                                   # the project around the current folder
uv run pawtrace serve ../myproject --notes ~/notes/myproject --edit-cmd 'myeditor {path}' --url-file /run/user/1000/pawtrace.url
```

- **views:** the overview (stages, items, gaps, questions, gates and rights per paper), each
  paper (spec items, ledger, questions, panels with gate verdicts and overlays), intake and
  review forms in notes folders with their marks, and every panel reference with how it was
  made. `pawtrace digitize` sessions run inside it too.
- **host interface:** spaces (the project's checkouts and the notes folders), list / stat /
  read / write files (writes are compare-and-swap on the file's sha256), open a file in your
  editor, and a small state store for a front end's tabs and preferences.
- **settings that belong to one machine** (notes folders outside the repo, the editor,
  whether checkouts are writable) go on the command line or in
  `$XDG_CONFIG_HOME/pawtrace/ui.yaml`, never in the project.

Routes, fields and guards: [docs/api.md](docs/api.md).

The front end ([web/](web/README.md)) is built on densk-core, the shared UI core of densk: tabs,
split panes, a sidebar over the notes folders and checkouts, PDF / markdown / code / image viewers
and editors, and pawtrace's own pages (overview, papers, panels, reviews, figures, digitize).
Review forms open in the editor, so marking one is editing it, with a conflict check against other
editors. `pawtrace serve` serves its build at `/` (committed, so a plain clone needs no build
step). The desktop app ([desktop/](desktop/README.md)) is a window over it: it starts `pawtrace
serve` for a local project, or follows a server on another machine through a loopback tunnel, and
keeps the session token in memory.

## Seed stats, preregistration, panel claims, author questions (optional modules)

Turn each on with `pawtrace modules enable <name>`. All four read and write plain files
and never run anything themselves.

```bash
uv run pawtrace seed-stats smith2024/fig2 --target-halfwidth 0.1   # pass rates across seeds
uv run pawtrace prereg freeze smith2024/fig2 --seeds 100-119      # before the final runs
uv run pawtrace prereg check                                      # also part of `pawtrace lint`
uv run pawtrace claim smith2024/fig2 --by agent-a --task "tune the window gate" --ttl 2h
uv run pawtrace claims
uv run pawtrace release smith2024/fig2 --by agent-a
uv run pawtrace questions --write                                 # QUESTIONS.md, send-ready
```

- **seed-stats:** per panel and config, from the run records' latest checks: each seed's
  verdict (the newest record per seed), the pass rate k/n with a Wilson interval, per-gate
  pass rates, and n / mean / std / min / median / max of every scalar metric. Records last
  checked with other gates than the panel has now are left out (`--any-gates` keeps them);
  `--target-halfwidth` says how many seeds would narrow the interval that far; `--final`
  counts only preregistered final runs.
- **preregistration:** `prereg freeze` writes `papers/<paper>/prereg/<panel>.yaml` (commit
  it): the gate config and its `gates_hash`, the run config, the adopted deviations, and a
  set of fresh seeds reserved for the final runs. It refuses seeds already used in run
  records or in a ledger entry's `tested_on`. Freezing again needs `--reason`, and the old
  freeze stays in `history`. `prereg check` errors when the gates, config or deviations
  changed after the freeze, a reserved seed was used before it, or a final run was checked
  with other gates; it warns on runs outside the reserved seeds and on reserved seeds run
  twice.
- **panel-claims:** one lock file per panel, `{panel, by, task, claimed_at, expires_at}`.
  A second live claim is refused (exit 1); your own claim renews; claims expire (default
  4h) and the next claimer takes over. Files live in the git common dir
  (`.git/pawtrace-claims/`), so every worktree of the repo sees the same claims; set
  `paths.claims` to put them elsewhere. Writes are atomic (`os.link`), so two claimers
  can't both win.
- **author-questions:** questions live in each paper's `ledger.yaml` under `questions:`
  (`question`, `why`, `items`, `panels`, `evidence`, `status: open | answered-by-us | asked
  | answered`, `asked`, `to`, `answer`, `answer_source`). `pawtrace questions` renders them
  grouped by paper and panel as markdown you can paste into an email (default: the open
  ones; `--all`, `--status`). It's a template: what to ask is up to you. The
  `## author questions` section of `SPEC.md` uses the same renderer.

## Quick start

```bash
uv sync --group dev
uv run pawtrace init myproject --paper smith2024 --profile minimal
cd myproject
uv run --project .. pawtrace playbook intake
uv run --project .. pawtrace lint
```

Every command prints readable text, or JSON with `--json`. Exit codes: 0 ok / gates
pass, 1 gates fail or lint errors, 2 bad input.

```text
pawtrace init | add-paper | modules | lint | spec show|render | extract run|show|seed | panels | run | import | check | runs | overlay | digitize | status
         | seed-stats | prereg freeze|show|check | claim | release | claims | questions | playbook | gates
```

## Worked examples

Coming soon: each one will run for an outside reader, with no paper content.

## Tests

```bash
uv run --group dev pytest -q
```

## License

Apache License 2.0, Copyright (c) 2026 nynxlabs LLC — see `LICENSE` and `NOTICE`.
