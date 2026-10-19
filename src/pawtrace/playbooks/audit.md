# Stage 7 — audit: trace every failure to its cause

**Goal:** each failing gate is traced to one of: the **paper** (unclear, inconsistent,
or the figure doesn't match the text), a **foundation** (cited model differs from what
the paper used), the **build** (our code doesn't match the spec), or a **gap** (a ledger
choice) — and goes back to stage 3 as an author question or a new ledger entry.

## Steps

1. `pawtrace check <panel> --all --json` — which gates fail, on how many seeds.
2. Before sweeping knobs, re-read the spec items the panel depends on (`spec_items`)
   and check the build against each **stated** item. Audit at intake-level detail:
   counts, sizes, schedules, equations.
3. Check the lineage: is the mechanism the panel shows actually present in the
   foundation the paper built on, or was it changed? A missing pathway can't be fixed
   by tuning.
4. Cheap diagnostics first (one seed, minutes): chance baselines, ordering checks,
   paper-vs-ours at the same x.
5. After two or three failed families of fixes, stop and write up the structural
   options for a person to decide; don't start another sweep by default.
6. Record the outcome: a ledger entry (proposed → testing → adopted), an author
   question, or a documented "paper inconsistency" note on the spec item.

## Done when

Every failing gate has a recorded cause and next step; nothing is "fixed" by loosening
a gate without a logged reason.
