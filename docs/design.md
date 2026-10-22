# pawtrace design notes (v0.1)

This file records how v0.1 implements the core and which defaults were picked.

## Shape

- **A tool, not a harness** (Devin, oct 19 2026). People and agents *use* pawtrace; it
  never drives them: no model calls, no agent loop, no sessions, no provider config.
  Agent-friendliness = `--json` on every command, deterministic output (generated files
  carry no timestamps), clear errors, and validators that say exactly what's missing.

- **Pipeline, not framework.** pawtrace owns plain files and a CLI. The only contract
  with project code is the panel function `fn(config: dict, seed: int) -> PanelData`
  (or `{series: values}`), named in the panel YAML as `run.entry`.
- **Projects** have one `pawtrace.yaml` (profile + module toggles + paths) and a
  `papers/<paper>/` folder per paper: `spec.yaml`, `ledger.yaml`, `panels/<id>.yaml`.
- **Panel data** is named series: numeric x for curves/scatter, string labels for
  bars and table rows. References (digitizations) and run outputs share the same type,
  so gates don't care which side a series comes from.

## Gates as config

A panel's `metrics:` build named values with **ops** (window means by real x, head/tail
means, block means per episode/trial, resample at another series' x, within-block std,
Pearson r, half-drop crossing, relative error, value at a label, arithmetic). `gates:`
test them with **checks** (equals, less_than, at_least, at_most, in_range, rel_close,
ratio_in_band, at_least_fraction, abs_diff_at_most, ordered, label_order). Names resolve
as `run.<series>`, `ref.<reference>`, or a metric name. `pawtrace gates` lists them all.

Semantics worth knowing:

- Every check fails on NaN. A metric that raises fails the gates that depend on it, with
  the error in the report; the report itself never crashes.
- `block_means(..., min_count=N)` marks incomplete blocks missing (NaN). `pearson` drops
  trailing missing entries (a shorter run) but is undefined with interior gaps (broken
  data). `slice_mean` and `max_abs` propagate missing entries.
- Paper-side metrics never depend on how long our run was (this differs from the old
  rl-adaptive-dbs code, which truncated the paper's episode vector to ours; verdicts are
  identical because our side is missing either way).
- `required: false` gates are reported but don't count toward the panel verdict.
- `gates_hash` covers metric + gate definitions but not `why`/`history` notes — the
  preregistration module will freeze it.

## Defaults for the open questions (oct 19 2026)

| question | v0.1 default | notes |
| --- | --- | --- |
| spec/ledger format | YAML, with a generated markdown view (`pawtrace spec render --write` → `SPEC.md`) | |
| gate tolerances | explicit per gate | `evaluate(..., tolerance_hook=fn)` is the hook for the digitization-error module |
| run records | one JSON per run under gitignored `runs/<paper>/<panel>/`, id `<commit12>-<confighash8>-s<seed>` (+`+d<diff8>` when dirty) | imported artifacts: `imp-<sha12>` of the file; re-checks append to `checks` |

## Lessons from rl-adaptive-dbs, as implemented

| lesson | v0.1 |
| --- | --- |
| details found months late (e.g. hidden-layer count) | intake lists every detail with its section; lint warns on stated items without a section; build playbook asks for spec tests from day 1 |
| code-use policy decided mid-project | `policy:` is required in `spec.yaml`; lint errors without it and when an adopted entry violates `authors_code.use` or cites an `off_limits` source |
| deviation adopted to pass gates, later dropped | lint errors on an adopted deviation without `tested_on` (fresh seeds/runs) |
| gates tightened after results | each gate has `why`, `added`, `history`; `gates_hash` recorded with every check |
| hand-coded per-paper gate files | shared op/check library; gates are config |
| no text-only baseline | panel lint warns without a `paper_only` config |
| stale README counts, `.bak` files | `pawtrace status` is generated; run records append, never copy |

## Not built yet

All 17 optional modules. Next in the build order: sensitivity, preregistration, source
log, dependency graph; then paper tables + release bundle. Module dependencies in
`modules.py` (scaffolding → foundations, dependency-graph → foundations, paper-tables →
seed-stats) are a first proposal.
