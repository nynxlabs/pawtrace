# Stage 3 — fill the gaps: missing items → ledger

**Goal:** every `missing`, `ambiguous` or `contradicted` spec item has a ledger entry
with a value, a source and a reason; every deviation from the paper follows the
deviation protocol.

## Steps

1. `pawtrace spec show <paper>` lists open gaps.
2. For each, add a `ledger.yaml` entry:
   - `type`: `gap-fill` (paper silent), `interpretation` (ambiguous → pick a reading),
     or `deviation` (we knowingly differ from what the paper states).
   - `source`, in the policy's `when_silent` order: `authors-code` (only if
     `policy.authors_code.use` allows it), `convention` (field default — cite it),
     `cited-work`, `author` (an answer from the authors), `deviation`.
   - `reason`: one or two sentences someone else could check.
   - `status`: `proposed` until decided.
3. If no good source exists, add an author question under `questions:` and fill the gap
   with a labelled convention meanwhile.
4. **Deviation protocol:** proposed (with a reason) → `testing` on **fresh seeds** that
   weren't used to choose it → `adopted` with `tested_on: {seeds, runs}` and `evidence`.
   Never adopt a deviation because it makes a gate pass on the seed you tuned on; lint
   blocks an adopted deviation without `tested_on`.
5. Keep two configs per panel: `paper_only` (stated values + conventions, no
   authors'-code or deviation entries) and `adopted`. The paper-only config must stay
   runnable — it's the text-only baseline the reproducibility claim needs.
6. `pawtrace lint <paper>`.

## Done when

No open gaps without an entry; lint clean; both configs defined for each panel.
