Import Dry Run — pre-go-live audit for CRM opportunity migrations into Reevo; flags what imports clean but silently corrupts reporting.
Run: `git clone <this repo> && cd reevo-demo && make demo`
Python 3.12 (3.10+ enforced by the Makefile).
Dashboard lands at `dashboard.html` — open directly in a browser, no server.
Data is synthetic and seeded — see "Data" below before reading any number as real.

# Import Dry Run

A pre-go-live audit for CRM opportunity migrations into Reevo. Takes a legacy
opportunity export plus a target pipeline config, and reports which records
will import clean, which will import clean *and silently corrupt reporting*,
and which are blocked. See [CLAUDE.md](CLAUDE.md) for the full spec.

**The adversarial case this tool is built around:** a legacy stage called
`Verbal Commit` fuzzy-matches "Negotiation" at 0.87 confidence — below the
0.90 auto-map threshold. The tool flags it for a human decision instead of
confidently mapping it. Plain character similarity alone scores that pair at
0.25 — a small hand-curated synonym lexicon is the only reason it surfaces as
a candidate at all. See `dashboard.html`'s Decision List tab, or run:

```
python3 audit.py --export-dir data/generated --config fixtures/reevo_pipeline.yaml --out report.md
```

## Running it

```bash
make demo    # generate dataset, run audit, render dashboard
make clean   # remove generated artifacts
```

Or step by step:

```bash
# 1. Generate the synthetic dataset (seeded, ground-truth manifest included)
python3 generate_dataset.py --seed 42 --n 4000 --out-dir data/generated

# 2. Run the audit -> markdown report
python3 audit.py --export-dir data/generated --config fixtures/reevo_pipeline.yaml \
  --out report.md --manifest data/generated/manifest.csv

# 3. Render the single-page dashboard
python3 generate_dashboard.py --export-dir data/generated --config fixtures/reevo_pipeline.yaml \
  --manifest data/generated/manifest.csv --out dashboard.html
```

`dashboard.html` is self-contained — open it directly in a browser, no server needed.

Dependencies: `pip install -r requirements.txt` (one pinned package, PyYAML —
everything else used is Python 3.12 standard library).

## Data

**Synthetic, seeded, documented — not a real export.** See
[DATASET.md](DATASET.md) for every corruption class and its real-world
source. No dollar figure this tool produces should be read as a forecast;
`dashboard.html`'s pipeline-value numbers are labeled illustrative for
exactly this reason.

## Known gaps

- Gate validation (`dryrun/gates.py`) fired zero times against the dataset
  until it was deliberately extended with a `gate_missing_account_id`
  corruption class. Of its 5 required-field checks, only `account_id` is
  currently exercised; `owner` can never fire (its dedup guard duplicates the
  condition that triggers it — dead code, not a coverage gap); `amount`,
  `close_date`, and `closed_lost_reason` are reachable but untested by this
  dataset. Don't read "0 findings" from an unexercised check as "passing."
- Stage resolution's fuzzy-mapped tier (auto-map without human review) has
  never fired in this dataset either — every mapped stage resolved via exact,
  normalized, or alias match. See `dashboard.html` → Where This Is Wrong for
  what else that implies.

## Recurring failure mode

Two defects in this build had the same shape: a check whose "is this already
covered?" guard tested the identical condition that triggered the check,
making it unreachable regardless of input — `gate_missing_owner`, and the
gate-validation pass as a whole, which fired zero times until the dataset was
deliberately extended to exercise it. Both were caught by cross-checking
per-rule fire counts against the ground-truth manifest, not by reading the
code and trusting it looked right — a check with 0 findings looks identical
whether it's passing or dead. The dashboard was verified the same way,
against behavior instead of source: a headless DOM run (jsdom) driving actual
clicks caught an unguarded `scrollIntoView` call that would have silently
broken the canonical-example jump button.
