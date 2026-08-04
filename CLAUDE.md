# CLAUDE.md — Import Dry Run

## What this is

A pre-go-live audit for CRM opportunity migrations. Takes a legacy opportunity
export plus a target pipeline config, and reports which records will import
clean, which will import clean *and silently corrupt reporting*, and which are
blocked.

Built as an outreach artifact for Reevo (AI-native GTM platform, rip-and-replace
CRM) and a demo for their Forward Deployed Generalist role. It is not a generic
CRM linter. It is scoped to Reevo's object model and named after their own
primitive.

## Why it exists

Reevo replaces the customer's CRM entirely, so every deployment opens with a
bulk load of a legacy system's full history into an empty instance. Their own
docs put pre-alignment on the customer: confirm objects exist, confirm field
names and types match, create missing custom fields, picklist values, and
opportunity stages before importing.

They shipped **Workflow Dry Run** (Jul 3, 2026): simulate a workflow against a
real record, see projected field changes and affected record counts, with every
production write blocked at the database level. This tool is that same primitive
applied to migration — the higher-stakes surface with no dry run today.

Use their vocabulary. This is Import Dry Run, not "migration QA harness."

## The core distinction — do not lose this

**Loud failures** throw an error on import. The customer finds out immediately.
Nobody's trust is damaged. These are not the point.

**Silent failures** import clean and corrupt a report. The customer finds out a
quarter later. These are the entire product.

Every number that goes in the report counts silent failures only. If a naive
importer would have caught it, it doesn't count.

## Scope

**In scope: opportunities only.**

Accounts and contacts are deliberately excluded. Reevo already claims inbound
records are automatically validated, deduped, and mapped against existing
records, and shipped rep-facing Merge Records (Jun 24, 2026). Building there
argues against their own product. Opportunities are uncovered and are what every
pipeline report is built on.

**Out of scope:** writing to any system, two-way sync, account/contact dedupe,
anything that isn't reading a file and emitting a report.

## Architecture

CLI core, thin demo UI on top. The CLI is the real thing; the UI exists to make
it watchable.

```
audit.py --export legacy_opps.csv --config reevo_pipeline.yaml --out report.md
```

### Inputs

**1. Legacy export** — opportunity rows as they come out of Salesforce or
HubSpot. See DATA below.

**2. Target config (YAML)** — this is what makes it Reevo-shaped:

- pipeline stages, in order
- closing probability per stage (drives weighted amount)
- required fields per stage (stage gating)
- valid owner list
- valid picklist values per field

Primary Closed Lost Reason is single-select and required at the close stage in
Reevo. Encode that. It's the cleanest example of a record their own system would
reject on creation but a bulk import accepts.

### Pipeline — four passes

**1. Parse and normalize.** Types, dates, currency, IDs. Log every coercion.
Never silently fix.

**2. Rule checks.** Deterministic, no judgment:
- close date before create date
- closed-won/lost with null close date
- placeholder far-future dates (12/31/2099)
- last-modified before created
- stage-change activity after close date
- mixed date formats where MM/DD vs DD/MM is ambiguous (anything under the 13th)
- null or zero amount on closed-won
- multi-currency with no currency field
- amount stored as text with symbols/separators
- opportunity pointing at an account ID absent from the export
- duplicate opportunity IDs
- owner is deactivated, a queue/alias, blank, or absent from the valid list
- picklist values not present in target

**3. Stage resolution.** The only part with real logic. Legacy stage string →
target stage: exact match, then normalized alias table, then fuzzy, then
unresolved. Each carries a confidence score.

- above threshold → mapped
- below threshold → flagged, with top candidates and the reason
- no match → blocked

**4. Gate validation.** For each resolved stage, check the record against that
stage's required fields.

### Verdicts

Every record gets exactly one: `mapped`, `flagged`, `blocked`. Flagged records
carry the specific ambiguity and the candidate resolutions. Never guess.

## The report is the artifact

The code is supporting evidence. The report is what gets read. Sections:

1. **Counts and verdicts** — totals per bucket
2. **Silent corruption** — the money section. Each finding names the specific
   report it breaks: Q3 stage-conversion, weighted pipeline forecast, rep
   leaderboard. Not "312 issues found." "312 opportunities that would have made
   Q3 stage-conversion reporting wrong, invisibly."
3. **Loud failures** — separated, and explicitly marked as would-have-been-caught
4. **Human decision list** — grouped by decision, not by row. "14 legacy stages
   need a mapping decision," not 400 line items. This is what an FDE hands a
   customer.
5. **Where this is wrong** — see below

## Demo (90 seconds)

1. Naive importer view: 4,000 records, 0 errors, ready to import. The strawman,
   and what actually happens today.
2. Dry run, same file: three buckets. The middle one is the demo.
3. Drill into a row: "Verbal Commit" fuzzy-matched 0.91 to "Negotiation,"
   below threshold, flagged with both candidates and the reason.
4. The number: weighted pipeline mispriced by $X, since closing probability is
   set per stage.
5. Decision list, grouped.

Ship as a screen recording plus a live link. Recording gets watched; link proves
it's real.

## Non-negotiable gates

All four required before this ships anywhere.

1. **A number from real logged inputs.** Negative findings count and are
   stronger.
2. **A written failure section.** What it gets wrong, when it refuses, what it
   gates on.
3. **One sentence of business meaning.** Currently: importing as-is misprices
   weighted pipeline by $X.
4. **Adversarial input handled visibly.** The 0.91 fuzzy match that is wrong.
   Show it flagged, not confidently mapped. This belongs on the first screen of
   the README.

## "Where this is wrong" — ship this in the UI, not the README

A visible tab in the demo. Contents:

- over-flag rate, and the review burden it creates (the honest trade-off)
- the class it cannot catch at all: a stage that maps cleanly but *means*
  something different at that customer. Not detectable from data. Say so.

Most demos don't have this. It's the differentiator.

## DATA — the open gate

**Nothing else in this build is hard. This is the thing that decides whether the
number means anything.**

If the mess is authored here, the confidence threshold is tuned against its own
generator and $X is fiction. That is the exact defect two prior rejections named.

Preference order:

1. One scrubbed real opportunity export from anyone in a RevOps or sales seat.
2. Published sample CRM data as base, with every injected corruption documented
   and justified as representative. Weaker. Say so in the README rather than
   hoping nobody notices.

Do not start day one until this is resolved.

## Timeline

Two days, no exceptions.

- **Day 1** — parse, rule checks, config schema, report skeleton
- **Day 2** — stage resolution, confidence calibration, gate validation, and
  writing the failure section honestly

## Working notes

- Terse and direct. Honest pushback over agreement. If the plan is wrong, say so
  before executing.
- Known failure mode: shipping the happy path and the demo rather than the
  production edge and the business meaning. Push back when it shows up.
- Known failure mode: pivoting before an experiment produces evidence. Push back
  on that too.
- Reevo moves fast on preview-before-commit tooling. Assume they could ship the
  import version themselves within a quarter.
