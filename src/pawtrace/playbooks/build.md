# Stage 4 — build: the paper's own pieces, in the project repo

**Goal:** the environment/model/controller the paper describes, built on the stage-2
foundations, with every parameter traceable to a spec item or ledger entry, and one
**panel contract** function per panel.

## Steps

1. Code lives in the project's repo, not in pawtrace. pawtrace only calls it.
2. Write each panel's function: `fn(config: dict, seed: int) -> PanelData` (or a dict
   `{series_name: values}`; x defaults to 0..n-1). Name it in the panel YAML:
   `run.entry: "pkg.module:function"` or `"path/file.py:function"`.
3. Every parameter in code points at its spec item / ledger id (a comment or a config
   key named after it). Values come from the panel's `run.configs`, not constants buried
   in code.
4. Write tests against the spec from day 1: e.g. "network has one hidden layer of N
   units (§III.B)". These catch drift that months of tuning won't.
5. Long jobs: launch through your machine's job queue (a batch queue or low-priority job slice), not in the
   foreground.

## Done when

`pawtrace run <panel> --config paper_only` and `--config adopted` both produce a run
record (gates may still fail — that's stage 6's job).
