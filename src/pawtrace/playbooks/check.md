# Stage 6 — check: digitize, gate, overlay

**Goal:** every panel has digitized paper references, gates written as config from the
digitization **before results exist**, and verdicts on recorded runs.

## Digitize

1. Digitize the paper panel (WebPlotDigitizer, Engauge, or a color-mask seed refined by
   hand). Store it in a format pawtrace reads: `curves-json` (thin schema: `n` + full
   `xy`), `wpd-json`, `wpd-csv`, `engauge-csv`, or `table-csv` for bars/tables.
2. Add it under the panel's `references:` (`path`, `format`, `series`,
   `bin_near_duplicates: true` for hand-digitized curves with double passes). Mark
   `status: needs_redo` if the digitization is known to be off — lint and overlays
   flag it.
3. Never read levels off a PNG; gates read the digitization.

## Gate

1. Write `metrics:` from ops (`pawtrace gates` lists them): window means by real x,
   block (episode/trial) means, ratios, correlations, `value_at` a label for bars.
2. Write `gates:` with checks and **explicit tolerances**, each with a `why` naming the
   paper claim it tests. Prefer shape, ordering and ratios over absolute levels when
   units or seeds move levels.
3. Gates go in before the results they judge. Any later change (tolerance, window,
   new gate) gets a `history` entry with date and reason.
4. `pawtrace check <panel>` (latest run), `--run <id>`, or `--all` for per-gate pass
   counts across runs/seeds. `--json` for agents.

## Overlay

`pawtrace overlay <panel>` draws the run with the digitized references on the same axes
and writes the next versioned PNG (`replication_vN.png`) — never overwrite an image.

## Done when

Gates evaluated on the adopted and paper-only configs; overlay written; `pawtrace status
--write` regenerated.
