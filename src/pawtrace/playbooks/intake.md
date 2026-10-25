# Stage 1 — intake: paper → spec skeleton

**Goal:** `papers/<paper>/spec.yaml` lists every equation, parameter, protocol detail,
architecture choice and figure panel the replication needs, each **stated** (with its
section) or **missing / ambiguous / contradicted**, plus the paper's rights and the
code-use policy — before any code exists.

**You need:** the paper (and its version: preprint, accepted manuscript, published,
erratum), supplementary material, and the authors' code location if any (don't read it
yet — see policy).

## Steps

1. `pawtrace init --paper <id>` (or `pawtrace add-paper <id>` in an existing project).
   Fill `paper:` including `version`.
2. **Record the paper's rights first** — before anyone (person or agent) works from the
   paper. In `rights:`:
   - `license` from the journal page or arXiv listing (CC-BY-4.0, CC-BY-NC-4.0, CC0-1.0,
     public-domain, arXiv-nonexclusive, publisher-copyright, …) and `license_url`.
   - **Which copy:** prefer an open-access or arXiv version when one exists. `copy_url` is
     the copy you work from; `open_version` is the open copy's URL, or `"none found"`
     after you looked. Say in `paper.version` which version it is.
   - `access`: how you got it — `open-access`, `subscription` (institutional),
     `personal-copy`, `author-provided`, or `under-review`.
   - `access_terms`: the access license's text-and-data-mining and AI clauses, in brief,
     with a link (publisher and institutional licenses often have them).
   - `rules`, which follow from the above:
     - `redistribute` — may the PDF, full text, extracted text/equations or figure crops
       be committed or published? **Default `false`.** `true` only when the license
       allows it (CC BY, CC0, public domain) or a permission says so. Digitized curves
       are our own measurements and aren't covered by this rule.
     - `quotes_in_spec` — short quotes with section citations in `spec.yaml`: yes by
       default. Set `false` if a license forbids even that; then paraphrase.
     - `ai_tools` — may the paper go to AI tools? New specs start at `never`, so nothing
       reaches an AI tool until someone fills in the license and access and opens it up.
       A published paper read for research:
       usually `allowed`, subject to the access license's text-mining/AI terms and the
       AI tool's data-training terms (use `local-only` if either is a problem). A
       manuscript under review (peer review, confidential drafts): **always `never`** —
       a person does intake, and agents don't read the paper or its `source/` folder.
   - `permissions`: anything the authors or publisher granted (`from`, `date`, `grants`:
     e.g. `redistribute`, `ai-tools`, `figures`; `ref` to the email or letter).
   `pawtrace lint` errors without this block, and when the rules contradict the license.
3. **Set the code-use policy.** `policy.when_silent` (preference order when the paper is
   silent), `policy.authors_code` (url, license, `use: allowed | reference-only |
   off-limits`), and `policy.off_limits` (sources nobody may open — e.g. GPL code we must
   rebuild from its paper). Check the code's license before deciding `use`.
4. **Extract (optional `extract` module, on in standard/strict).** Pull the paper apart
   so you read from text, not from the PDF viewer:
   - `pawtrace extract run <paper> <paper.pdf>` copies the PDF into
     `papers/<paper>/source/` (gitignored — it ignores itself) and writes `text.md`
     (text by section), `equations.json` + `equations/eq-N.png`, `figures.json` +
     `figures/fig-N.png` and per-panel `fig-Na.png`, and `manifest.json` (PDF hash,
     backend, file hashes → the extraction id). It refuses without a `rights:` block.
   - `pawtrace extract show <paper>` lists sections, equations and figures with their
     files. Check the warnings: the built-in `pdfium` backend is heuristic and misses
     scanned PDFs, unnumbered equations and odd layouts. For those, or for LaTeX, set
     `extract.command` (any PDF → markdown tool: marker, MinerU, docling, a local OCR
     script) or `extract.formula_command` (LaTeX for each equation crop) in
     `pawtrace.yaml` — and respect `rules.ai_tools` when that tool is a model.
   - `pawtrace extract seed <paper>` (try `--dry-run`, `--kind equation|panel|parameter`)
     appends candidate items to `spec.yaml` with `status: candidate`. **A candidate is
     not a finding:** read each one against the paper (equation crops against the PDF),
     fix the text and section, and set the real status — or delete it.
   Never commit anything from `source/` unless `rules.redistribute` is true; lint errors
   if git tracks it or would pick it up.
5. Read the paper section by section (from `text.md` if you extracted). For each detail,
   add an item (or review the candidate):
   - `kind` (equation, parameter, protocol, architecture, data, panel, claim, foundation)
   - `text` in the paper's own words (short quotes; paraphrase if `quotes_in_spec` is
     false), `section` (§, Eq., Alg. line, Table, Fig caption)
   - `status`: `stated` only if the paper says it; `missing` if the replication needs it
     and the paper doesn't say; `ambiguous` if two readings fit; `contradicted` if two
     places disagree (list both sections).
   - `value` when stated.
   Architecture counts too: layer counts and widths are exactly the kind of detail that
   goes unchecked for months. Extraction only finds what has a pattern (numbered
   equations, captions, "x = 0.5" sentences); protocol and architecture details are
   still yours to find.
6. One `kind: panel` item per figure panel you'll replicate, with what it claims.
7. Numeric claims in the text (e.g. "reduces β power by 40%") → `kind: claim` items;
   they become gates later.
8. `pawtrace lint <paper>` until there are no errors and no unreviewed candidates. Open
   gaps (warnings) are expected now — stage 3 fills them.
9. `pawtrace spec render <paper> --write` for the readable view (SPEC.md is generated;
   never edit it by hand).

## Done when

Every detail the build will need has an item; no item is still a candidate; lint has no
errors; the rights and the policy are set.

## Don't

- Don't commit the PDF, `text.md`, equation or figure crops, or long passages unless the
  rights allow it. Short quotes with section citations in `spec.yaml` are fine.
- Don't give the paper or its `source/` folder to an AI tool when `rules.ai_tools` is
  `never` (or to a hosted one when it's `local-only`).
- Don't fill gaps here — record them as missing.
- Don't open the authors' code to "check" a detail unless the policy allows it, and log
  what you read once the source-log module is on.
