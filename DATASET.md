# DATASET.md — Synthetic legacy opportunity export

## Status: synthetic, and labeled as such

This dataset is authored, not harvested. Every corruption class below is
traceable to a documented real-world migration failure — sources listed inline.
That makes it *defensible*, not *real*.

Consequences, non-negotiable:

- The demo displays a data-source label: **synthetic fixture — threshold not
  calibrated on production data**
- No dollar figure is presented as a finding. Any $ number in the demo is
  illustrative and labeled.
- If a real scrubbed export lands later, rerun and replace.

A reviewer who has seen a real messy export will notice if this one is too tidy.
Being first to say it's synthetic costs nothing. Being caught costs everything.

---

## Shape: two files, matching a real Salesforce export

A real Salesforce export splits opportunity records from their stage history.
Reproduce that split — a single flat CSV is a tell.

### File 1 — `opportunities.csv`

Standard Salesforce Opportunity fields (Account Name is a lookup, Amount is
Currency(16,2), Close Date is a Date, Stage is a Picklist):

```
Id, Name, AccountId, StageName, Amount, CurrencyIsoCode, CloseDate,
Probability, OwnerId, CreatedDate, LastModifiedDate, Type, LeadSource,
IsClosed, IsWon, ForecastCategory, <custom fields>
```

Salesforce IDs are 15/18-char alphanumeric (e.g. `0067d000000XXXXX`). Use the
real format. Sequential integers are a tell.

### File 2 — `opportunity_history.csv`

Stage transitions live separately:

```
OpportunityId, StageName, Amount, Probability, CloseDate, CreatedDate
```

This file is what makes the "stage change recorded after close date" check
possible. Without it that check can't exist.

### File 3 — `users.csv`

```
UserId, FirstName, LastName, Email, IsActive
```

Needed because owner validity is checked against it. Some OwnerIds in file 1
will not appear here at all — that's intentional (see D2).

---

## Volume and distribution

- **4,000 opportunity rows.** Big enough that manual review is obviously
  infeasible, small enough to run instantly.
- **~2,800 clean.** If most rows are broken it stops resembling a real CRM.
- **~850 loud failures** — would throw on import anyway.
- **~350 silent failures** — the product. This is the number the demo is about.

Skew the mess toward older records. Data debt accumulates; a CRM's last quarter
is usually fine and its 2021 records are not. Uniform corruption across the date
range is a tell.

---

## Corruption classes

Each has: what it is, whether it's LOUD or SILENT, and the source.

### A. Stage mapping — the core class

**A1. Legacy stage with no target equivalent** — SILENT
Stage names that don't exist in the target pipeline: `Verbal Commit`,
`Pending Legal`, `Pilot`, `Nurture`, `Closed - No Decision`, `On Hold`.
Source: stages mapped incorrectly between source and target are documented as
inflating or deflating revenue projections
(clonepartner.com/blog/the-ultimate-crm-data-migration-checklist-a-10-point-plan-for-a-zero-loss-transition)

**A2. The near-miss** — SILENT, and this one is the demo
One stage that fuzzy-matches at ~0.91 to the wrong target stage.
`Verbal Commit` → `Negotiation` is the canonical example: high string similarity,
different meaning, different closing probability. This single row is gate 4
(adversarial input handled visibly). Build the dataset around it.

**A3. Same name, different meaning across teams** — SILENT
Two sales orgs both using `Qualified` with different entry criteria.
Source: lifecycle stages applied inconsistently across teams
(engagingpartners.co/blog/why-bad-data-gets-worse-after-a-migration-not-better)

**A4. Deprecated stage from a renamed pipeline** — SILENT
Values from a pipeline renamed two years ago and never backfilled. Cluster these
in older records only.

**A5. Free-text pollution** — LOUD
Trailing whitespace, case variants (`closed won` / `Closed Won` / `CLOSED WON`),
a stray `Closed Won ` with a trailing space.

### B. Required-field gaps

**B1. Closed-lost with no loss reason** — SILENT
Reevo makes Primary Closed Lost Reason single-select and required at the close
stage. A bulk import bypasses that. Best single example in the report — their own
system would reject the record on creation.

**B2. Missing mandatory fields generally** — LOUD
Source: each module has required fields that fail the row if blank; Deals need
Deal Name, Stage, and Closing Date
(codestringers.com/articles/zoho-crm-data-migration-checklist)

**B3. Picklist value absent from target** — SILENT if the field silently blanks
Source: same checklist warns to verify picklist fields didn't blank out.

### C. Dates

**C1.** CloseDate before CreatedDate — LOUD
**C2.** IsClosed = true with null CloseDate — SILENT
**C3.** Placeholder far-future dates: `12/31/2099`, `01/01/2050` — SILENT
**C4.** LastModifiedDate before CreatedDate — LOUD
**C5.** OpportunityHistory row timestamped after CloseDate — SILENT
**C6.** Mixed date formats where `03/04/2025` is ambiguous — SILENT, and nasty.
Include a cluster where MM/DD and DD/MM both parse. Only affects days under 13,
which is why it survives casual review.

### D. Ownership

**D1. Owner is an inactive user** — SILENT
Source: records owned by inactive users get reassigned to whoever runs the
import (codestringers.com/articles/zoho-crm-data-migration-checklist).
This is the cleanest documented silent failure available. Use it.

**D2. OwnerId absent from users.csv entirely** — SILENT
Reps who left before the export.

**D3. Owner is a queue or alias** — SILENT
`Sales Ops`, `Inbound Queue`, `Unassigned`.

**D4. Blank OwnerId** — SILENT
Same reassignment consequence as D1.

**D5. More distinct owners than seats** — SILENT
63 distinct OwnerIds when the customer bought 40 seats.

### E. Amount and currency

**E1.** IsWon = true with null or zero Amount — SILENT
**E2.** Multiple CurrencyIsoCode values with the field blank on ~30% of rows — SILENT
**E3.** Amount as text: `$45,000.00`, `45000 USD`, `45,000` — LOUD
**E4.** Negative amounts on won deals — LOUD

### F. Referential integrity

**F1.** AccountId not present in the accounts file — LOUD
Source: migrating in the wrong order leaves orphaned records and missing links
(fayedigital.com/blog/8-crm-data-migration-challenges-that-sabotage-your-project-and-how-to-fix-them/)

**F2.** Duplicate Opportunity Id — LOUD

**F3.** Same deal entered twice under slightly different names — SILENT
`Acme Corp - Enterprise` vs `Acme Corporation Enterprise Deal`, same account,
overlapping dates, different amounts. Not a string-match problem. This is the
real duplicate case and it inflates pipeline.
Source: duplicate records distort revenue figures when deal data is split across
multiple records (dedupe.ly/blog/why-every-crm-migration-needs-deduplication-first)

### G. Custom fields

**G1.** Fields with >90% nulls that someone still expects to see — SILENT
**G2.** Two legacy fields meaning the same thing, populated inconsistently — SILENT
**G3.** Properties created by people who left — SILENT
Source: properties created by people who left years ago
(engagingpartners.co/blog/why-bad-data-gets-worse-after-a-migration-not-better)

---

## Generator requirements

- **Seeded RNG.** The dataset must be reproducible. Commit the seed.
- **Emit a ground-truth manifest.** Every injected corruption logged with row Id,
  class, and LOUD/SILENT. This is how over-flag and miss rates get measured — and
  it's the only honest way to fill the "where this is wrong" tab.
- **Never inject a corruption the tool wasn't designed to catch, then quietly
  drop it from the manifest.** The misses are part of the finding.
- **Realistic company names.** Pull from a public company-name list rather than
  `Company_0001`. Free text should look like humans typed it.
- **Keep the generator in the repo.** Someone will ask how the data was made. The
  answer being a readable script is a strength.

---

## What this dataset cannot do

State this in the "where this is wrong" tab, not just here:

- The confidence threshold is tuned against corruptions authored here. On a real
  export it will over- and under-flag differently.
- Class A3 (same stage name, different meaning across teams) is injected here but
  is **not detectable from data alone** in reality. The tool catches it here only
  because the generator knows the answer. On real data this requires asking a
  human. Say so explicitly — this is the most credible thing in the artifact.
- Real exports contain failure modes not in this list. Absence of a class here is
  not evidence it doesn't occur.

---

## Sources

- Salesforce Opportunity standard fields and OpportunityHistory schema:
  dench.com/blog/salesforce-data-export
- Zoho migration checklist (inactive owner reassignment, mandatory fields,
  picklist blanking, dependency load order):
  codestringers.com/articles/zoho-crm-data-migration-checklist
- Stage mapping corrupting forecasting:
  clonepartner.com/blog/the-ultimate-crm-data-migration-checklist-a-10-point-plan-for-a-zero-loss-transition
- Data debt, inconsistent lifecycle stages, orphaned properties:
  engagingpartners.co/blog/why-bad-data-gets-worse-after-a-migration-not-better
- Duplicate deals distorting revenue reporting:
  dedupe.ly/blog/why-every-crm-migration-needs-deduplication-first
- Orphaned records from dependency-order failures:
  fayedigital.com/blog/8-crm-data-migration-challenges-that-sabotage-your-project-and-how-to-fix-them/
