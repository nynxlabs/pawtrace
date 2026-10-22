# Agent notes — pawtrace

Machine policy: `~/AGENTS.md` (worktrees, ship, heavy jobs, secrets, no PRs). This file
covers this repo only.

- **What it is:** a modular pipeline for rebuilding and checking published computational
  research (`README.md`; scope: `~/vault/research/pawtrace/README.md` — keep that doc
  updated when design decisions land; design notes in `docs/design.md`).
- **Public repo** (`nynxlabs/pawtrace`, public since oct 22 2026; Apache-2.0,
  Copyright (c) 2026 nynxlabs LLC — `LICENSE`, `NOTICE`). Public-repo rules from
  `~/AGENTS.md` apply: commit locally in a worktree, but every push to `main` needs Devin's
  or nynx's ok — no auto-ship. Before any push, scan the outgoing diff for secrets,
  personal info, and private machine paths. No PyPI / npm / crates names until Devin says so.
- **No field-specific code in `src/pawtrace`.** Nothing DBS-, BCI- or model-specific: the
  paper's model code lives in each project's repo and pawtrace calls it through the panel
  contract. Worked examples under `examples/` hold config only.
- **Never copy in** code derived from licensed sources we can't redistribute (e.g. the
  Kumaravelu model port, the authors' CL-DBS-RL code), and never open Fleming 2020's GPL
  source. Porting our own rl-adaptive-dbs helpers (also nynxlabs LLC's) is fine.
- **A tool, not a harness** (Devin, oct 19 2026). No LLM/model calls, no agent loop, no
  spawning threads or sessions, no provider or model config anywhere in pawtrace. It's a
  CLI + library + plain files that any harness or a person calls. Intake creates and
  validates the spec skeleton — the reading and filling is the user's. Output modules
  (author questions, paper tables, site pages) are templates over the ledger, not
  generated prose; playbooks are markdown docs; panel claims will be a lock file. An
  orchestrator, if ever, is a separate optional package on top.
- **Not a framework.** No model base classes. The panel contract
  (`fn(config, seed) -> PanelData`) is the only framework-like piece.
- **Gates are config.** New gate logic goes into `src/pawtrace/gates/library.py` as a
  general op or check with a docstring and a test — never a per-paper gate module. Keep
  ops x-based (real units), not sample-fraction based; make them work for label x
  (bars/tables) where it makes sense.
- **Plain files are the source of truth** (YAML + markdown). Every CLI command keeps a
  readable output and a `--json` output. Generated files (STATUS.md, SPEC.md) are never
  hand-edited.
- **Parity:** `examples/mehregan-4a/parity.py` must stay at 0 verdict mismatches; run
  `uv run --group dev pytest -q` before shipping (fast; no heavy jobs needed).
- **rl-adaptive-dbs is read-only from here** — the example reads its artifacts but never
  writes there.
