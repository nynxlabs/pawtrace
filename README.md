# pawtrace

> A modular pipeline for rebuilding and checking published computational research, for people and AI agents.

pawtrace takes a paper from reading to checked figures and a faithfulness audit:
**intake → foundations → fill the gaps → build → run → check → audit**. It's a pipeline,
not a framework: the paper's model code (simulators, controllers, training loops) lives
in your project's repo; pawtrace records what the paper says, how each gap was filled,
what ran, and whether the results match the paper's figures.

Private, work in progress (v0.1: core only). Scope and design notes:
`docs/design.md`.

## What's in the core

| piece | files | what it does |
| --- | --- | --- |
| spec + ledger | `papers/<paper>/spec.yaml`, `ledger.yaml` | every detail the paper states (with its section) or leaves out; the code-use policy; how each gap was filled, with source and reason; deviations proposed → tested → adopted |
| panel contract | `papers/<paper>/panels/<panel>.yaml` | names your function `fn(config, seed) -> data`, with a `paper_only` and an `adopted` config |
| run records | `runs/<paper>/<panel>/<id>.json` (gitignored) | one JSON per run, keyed by commit + config hash + seed |
| gates | `metrics:` + `gates:` in the panel YAML | a shared library of ops and checks; each panel's gates are config with explicit tolerances and a `why` |
| digitization formats | `references:` in the panel YAML | `curves-json`, `wpd-json`, `wpd-csv`, `engauge-csv`, `table-csv` (bars/tables) |
| overlays | `pawtrace overlay` | the run and the digitized paper on the same axes, versioned PNGs |
| status | `pawtrace status --write` | a generated status page — never hand-edited |
| playbooks | `pawtrace playbook <stage>` | step-by-step instructions per stage that a person or an agent can follow alone |

Optional modules (sensitivity, preregistration, source log, dependency graph, seed
stats, paper tables, …) are listed by `pawtrace modules` and turned on in
`pawtrace.yaml`; profiles `minimal`, `standard`, `strict` are presets. In v0.1 only the
core is built; the rest are registered so configs can already name them.

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
pawtrace init | add-paper | modules | lint | spec show|render | panels | run | import | check | runs | overlay | status | playbook | gates
```

## Worked example: Mehregan Fig 4a

`examples/mehregan-4a/` expresses one rl-adaptive-dbs panel through the core: the
panel's 13 gates as config, its WPD-refined digitization as the reference, and saved
training artifacts imported as runs. `parity.py` runs pawtrace's gates and the original
hand-coded gate function side by side on every saved artifact and checks that every
verdict matches (it needs an rl-adaptive-dbs checkout and only reads from it).

```bash
cd examples/mehregan-4a
uv run --project ../.. pawtrace import 4a ~/research/rl-adaptive-dbs/artifacts/figures/papers/mehregan/4a/series.json
uv run --project ../.. python parity.py
```

## Tests

```bash
uv run --group dev pytest -q
```

## License

Apache License 2.0, Copyright (c) 2026 nynxlabs LLC — see `LICENSE` and `NOTICE`.
