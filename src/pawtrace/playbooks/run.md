# Stage 5 — run: panel contract → run records

**Goal:** recorded runs, each tied to the commit, the exact config and the seed.

## Commands

- `pawtrace run <panel> [--config adopted|paper_only] [--seed N ...] [--set k=v]` — calls
  the panel function, writes `runs/<paper>/<panel>/<commit12>-<cfghash8>-s<seed>.json`
  (`+d<diff8>` when the tree is dirty), then checks gates.
- `pawtrace import <panel> <artifact.json ...>` — record artifacts produced elsewhere
  (e.g. by an existing training script) through the panel's `import:` mapping; keyed by
  the file's content hash.
- `pawtrace runs <panel>` — list records with their latest verdict.

## Rules

- Commit before runs you'll cite: a dirty-tree run id says so, but the diff itself isn't
  stored.
- Seeds: lock a default seed for one-seed gates; paper panels are usually one
  realization, so don't tune to one seed's wiggles.
- Record, don't overwrite: re-checking appends to the record's `checks` list. No `.bak`
  copies.
- Heavy runs go through the machine's queue (`heavy` on nynxbox) with a timeout.
