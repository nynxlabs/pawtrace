# Stage 2 — foundations: what the paper builds on

**Goal:** every model, simulator, dataset or library the paper builds on is a
`kind: foundation` item with a license and a route.

## Steps

1. From the paper's methods and citations, list each foundation (cited models,
   simulators, datasets, toolkits). Add a `kind: foundation` item per foundation with the
   citation and `section` where the paper uses it.
2. Record the foundation's **paper** rights the same way as the main paper's (intake
   step 2): give the foundation item a `rights:` block — `license`, `copy_url`,
   `open_version`, `access`, `rules` (`redistribute`, `quotes_in_spec`, `ai_tools`),
   `permissions`. Lint checks its shape. If you extract a foundation paper, add it as its
   own paper (`pawtrace add-paper <id>`) so it gets its own gitignored `source/`.
3. For each, record the **code or data** license and pick a route in the ledger (`type: interpretation`,
   `source: cited-work`):
   - **reuse as-is** — a library or dataset whose license allows it;
   - **port from reference code** — needs a license that allows it, or written
     permission (record it);
   - **rebuild from its paper** — required when the code is off limits (e.g. GPL source
     we can't open). Add it to `policy.off_limits`.
4. Where a reference implementation exists, plan **reference-equivalence tests** (same
   inputs → same outputs within a stated tolerance) and keep them in the project repo.
5. Build field-specific foundation code in the project's own repo (or a private package
   if its license requires), never inside pawtrace.
6. Several papers can share one foundation through adapters; note which papers use it.

## Done when

Every foundation has its paper's rights, a code/data license, a route with a reason, and (where possible) an
equivalence test plan. (The `foundations` module will automate the survey; until then,
this playbook is the procedure.)
