# Stage 1 — intake: paper → spec skeleton

**Goal:** `papers/<paper>/spec.yaml` lists every equation, parameter, protocol detail,
architecture choice and figure panel the replication needs, each **stated** (with its
section) or **missing / ambiguous / contradicted**, plus the code-use policy — before
any code exists.

**You need:** the paper (and its version: preprint, published, erratum), supplementary
material, and the authors' code location if any (don't read it yet — see policy).

## Steps

1. `pawtrace init --paper <id>` (or copy `templates/spec.yaml`). Fill `paper:` including
   `version`.
2. **Set the policy first.** `policy.when_silent` (preference order when the paper is
   silent), `policy.authors_code` (url, license, `use: allowed | reference-only |
   off-limits`), and `policy.off_limits` (sources nobody may open — e.g. GPL code we must
   rebuild from its paper). Check the license before deciding `use`.
3. Read the paper section by section. For each detail, add an item:
   - `kind` (equation, parameter, protocol, architecture, data, panel, claim, foundation)
   - `text` in the paper's own words, `section` (§, Eq., Alg. line, Table, Fig caption)
   - `status`: `stated` only if the paper says it; `missing` if the replication needs it
     and the paper doesn't say; `ambiguous` if two readings fit; `contradicted` if two
     places disagree (list both sections).
   - `value` when stated.
   Architecture counts too: layer counts and widths are exactly the kind of detail that
   goes unchecked for months.
4. One `kind: panel` item per figure panel you'll replicate, with what it claims.
5. Numeric claims in the text (e.g. "reduces β power by 40%") → `kind: claim` items;
   they become gates later.
6. `pawtrace lint <paper>` until there are no errors. Open gaps (warnings) are expected
   now — stage 3 fills them.
7. `pawtrace spec render <paper> --write` for the readable view (SPEC.md is generated;
   never edit it by hand).

## Done when

Every detail the build will need has an item; lint has no errors; the policy is set.

## Don't

- Don't fill gaps here — record them as missing.
- Don't open the authors' code to "check" a detail unless the policy allows it, and log
  what you read once the source-log module is on.
