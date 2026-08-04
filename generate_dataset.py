#!/usr/bin/env python3
"""Synthetic legacy-opportunity export generator. See DATASET.md.

This data is authored, not harvested — every corruption class is traceable to
a documented real-world migration failure (sources in DATASET.md), which makes
it defensible, not real. No dollar figure produced from this dataset should be
presented as a finding; it's illustrative and must be labeled as such.

Output: opportunities.csv, opportunity_history.csv, users.csv, manifest.csv
(ground truth: every injected corruption, its class, and LOUD/SILENT).

Interpretation notes (decisions made where DATASET.md was ambiguous, so the
choice is traceable rather than silent):

- E2 ("blank on ~30% of rows") is read as ~30% of the SILENT-corruption
  budget, not 30% of the full 4000 rows — the latter would blow past the
  documented ~350 total silent count on its own.
- F1 (AccountId not in an accounts file) is not generated at all: there is no
  accounts.csv in this build (accounts are out of scope per CLAUDE.md), and
  F1 is LOUD anyway — a naive importer's FK constraint catches it regardless
  of whether this tool does. Documented as a deliberate non-detection, not a
  silent drop.
- B2 (missing mandatory fields generally) is deferred to day 2 (needs gate
  validation, which needs stage resolution). Not generated yet.
- G1-G3 (custom-field noise) are out of scope for this pass; no custom fields
  are generated.
- A3 ("Qualified" meaning different things per team) is generated as a clean
  exact/alias match to "Qualification" — by design. It is NOT detectable from
  data and the tool will never flag it. That's the point: it's the permanent
  miss the "where this is wrong" tab has to name honestly.
"""
from __future__ import annotations

import argparse
import copy
import csv
import os
import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta

TODAY = date(2026, 8, 3)

# ---------------------------------------------------------------------------
# Reference vocab

PREFIXES = [
    "Summit", "Harbor", "Meridian", "Cascade", "Northwind", "Ironwood", "Bright",
    "Silverline", "Cobalt", "Granite", "Amber", "Crestview", "Fieldstone",
    "Lighthouse", "Vantage", "Anchor", "Bridgeway", "Clearwater", "Driftwood",
    "Eastgate", "Foxhollow", "Greenridge", "Highmark", "Ivyrock", "Juniper",
    "Kestrel", "Larkspur", "Millbrook", "Nightingale", "Overlook", "Pinegate",
    "Quarrystone", "Redwood", "Stonebridge", "Timberline", "Underhill",
]
NOUNS = [
    "Logistics", "Dynamics", "Solutions", "Partners", "Systems", "Group",
    "Holdings", "Technologies", "Ventures", "Industries", "Networks",
    "Analytics", "Robotics", "Materials", "Foods", "Media", "Capital",
    "Energy", "Health", "Labs", "Manufacturing", "Consulting", "Freight",
]
SUFFIXES = ["Inc.", "LLC", "Corp.", "Co.", "Group", "Ltd."]

FIRST_NAMES = [
    "Jane", "John", "Priya", "Miguel", "Chen", "Aisha", "Robert", "Emily",
    "David", "Sofia", "James", "Linda", "Omar", "Grace", "Daniel", "Nina",
    "Marcus", "Fatima", "Kevin", "Laura", "Tomás", "Yuki", "Ahmed", "Rachel",
    "Brian", "Wei", "Elena", "Samuel", "Chloe", "Victor", "Hana", "Derek",
    "Amara", "Felix", "Ingrid", "Malik", "Zara", "Oscar", "Priyanka", "Liam",
    "Naomi", "Carlos", "Diana", "Yusuf", "Petra", "Rafael", "Mei", "Adrian",
    "Sana", "Gustavo", "Ellen",
]
LAST_NAMES = [
    "Doe", "Smith", "Rao", "Torres", "Wang", "Khan", "Nguyen", "Johnson",
    "Kim", "Rossi", "Patel", "Brown", "Haddad", "Osei", "Kowalski", "Ivanova",
    "Reyes", "Abara", "Walsh", "Hoffman", "Silva", "Tanaka", "Ali", "Green",
    "Fischer", "Zhou", "Popescu", "Adams", "Martin", "Lund", "Suzuki",
    "Novak", "Diallo", "Weber", "Berg", "Farouk", "Ahmadi", "Reid", "Verma",
    "O'Connell",
]

TARGET_STAGES = ["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
STAGE_WEIGHTS = [0.25, 0.20, 0.15, 0.15, 0.15, 0.10]

CLOSED_LOST_REASONS_VALID = ["Budget", "Timing", "Competitor", "No Decision", "Other"]
CLOSED_LOST_REASONS_INVALID = ["Lost to Legacy System", "Went Dark", "Product Gap"]

DEAL_TYPES = [
    "Enterprise", "Renewal", "Expansion", "New Business", "Upsell", "Pilot Conversion",
    "Annual Contract", "Multi-Year Deal", "Add-On", "Migration", "Platform Upgrade",
    "Q1 Renewal", "Q2 Expansion", "Strategic Account", "Cross-Sell", "Standard License",
]

QUEUE_ALIASES = ["Sales Ops Queue", "Unassigned", "Inbound Queue", "APAC Sales Queue"]

A1_UNRESOLVED_STAGES = ["Pending Legal", "Pilot", "Nurture", "Closed - No Decision", "On Hold"]
A2_STAGE = "Verbal Commit"
A3_LEGACY_STAGE = "Qualified"  # maps cleanly to Qualification; hidden semantic drift, by design
A4_DEPRECATED_STAGE = "Stage 3 - Committed"  # from a pipeline renamed ~2 years ago


def _b62(rng: random.Random, n: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


def sf_id(rng: random.Random, prefix: str) -> str:
    return prefix + _b62(rng, 12)


def company_name(rng: random.Random) -> str:
    name = f"{rng.choice(PREFIXES)} {rng.choice(NOUNS)}"
    if rng.random() < 0.6:
        name += f" {rng.choice(SUFFIXES)}"
    return name


def random_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, max(span, 0)))


# ---------------------------------------------------------------------------


@dataclass
class Opp:
    opportunity_id: str
    name: str
    account_id: str
    owner_id: str
    stage_raw: str
    amount: str
    currency: str
    close_date: str
    created_date: str
    last_modified_date: str
    closed_lost_reason: str
    is_closed: str
    is_won: str
    history: list = field(default_factory=list)  # list of (stage_name, amount, probability, close_date, created_date)


def build_users(rng: random.Random):
    """55 historical users: 45 active (the pool clean rows draw from — this
    intentionally exceeds the 40-seat config, which is D5), 10 inactive
    (D1 pool). Plus 8 'ghost' ids used only for D2, never written to users.csv."""
    users = []
    for _ in range(45):
        users.append(_make_user(rng, active=True))
    for _ in range(10):
        users.append(_make_user(rng, active=False))
    ghosts = [sf_id(rng, "005") for _ in range(8)]
    return users, ghosts


def _make_user(rng: random.Random, active: bool):
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    return {
        "UserId": sf_id(rng, "005"),
        "FirstName": first,
        "LastName": last,
        "Email": f"{first.lower()}.{last.lower().replace(chr(39), '')}@acme-legacy.example.com",
        "IsActive": "true" if active else "false",
    }


def build_accounts(rng: random.Random, n_accounts: int) -> dict[str, str]:
    """account_id -> company name. One company per account, like a real CRM —
    binding them independently would let unrelated companies share an
    AccountId by chance, producing spurious same-account name collisions."""
    return {sf_id(rng, "001"): company_name(rng) for _ in range(n_accounts)}


def pick_stage(rng: random.Random) -> str:
    return rng.choices(TARGET_STAGES, weights=STAGE_WEIGHTS, k=1)[0]


def make_clean_opp(rng: random.Random, active_users, accounts) -> Opp:
    stage = pick_stage(rng)
    is_closed = stage in ("Closed Won", "Closed Lost")
    is_won = stage == "Closed Won"

    created = random_date(rng, date(2021, 1, 1), date(2026, 6, 1))
    cycle_days = rng.randint(20, 180)
    if is_closed:
        close = created + timedelta(days=cycle_days)
        if close > TODAY:
            close = TODAY
    else:
        close = created + timedelta(days=cycle_days)  # projected future close, normal

    last_modified = created + timedelta(days=rng.randint(0, max((min(close, TODAY) - created).days, 1)))

    base_amount = {
        "Prospecting": (5_000, 60_000),
        "Qualification": (8_000, 90_000),
        "Proposal": (10_000, 150_000),
        "Negotiation": (15_000, 250_000),
        "Closed Won": (10_000, 300_000),
        "Closed Lost": (5_000, 200_000),
    }[stage]
    amount = round(rng.uniform(*base_amount), 2)

    currency = rng.choices(["USD", "EUR", "GBP"], weights=[0.75, 0.15, 0.10], k=1)[0]

    owner = rng.choice(active_users)["UserId"]
    account = rng.choice(list(accounts.keys()))
    company = accounts[account]
    deal_type = rng.choice(DEAL_TYPES)

    closed_lost_reason = ""
    if stage == "Closed Lost":
        closed_lost_reason = rng.choice(CLOSED_LOST_REASONS_VALID)

    history = _build_history(rng, stage, created, close, is_closed)

    return Opp(
        opportunity_id=sf_id(rng, "006"),
        name=f"{company} - {deal_type}",
        account_id=account,
        owner_id=owner,
        stage_raw=stage,
        amount=f"{amount:.2f}",
        currency=currency,
        close_date=close.isoformat(),
        created_date=created.isoformat(),
        last_modified_date=last_modified.isoformat(),
        closed_lost_reason=closed_lost_reason,
        is_closed=str(is_closed).lower(),
        is_won=str(is_won).lower(),
        history=history,
    )


def _build_history(rng, stage, created, close, is_closed):
    stage_order = TARGET_STAGES[:4] + ([stage] if stage in ("Closed Won", "Closed Lost") else [])
    idx = stage_order.index(stage) if stage in stage_order else len(stage_order) - 1
    steps = stage_order[: idx + 1]
    end = close if is_closed else min(TODAY, close)
    span = max((end - created).days, 1)
    rows = []
    for i, s in enumerate(steps):
        t = created + timedelta(days=int(span * (i + 1) / len(steps)))
        rows.append({"stage": s, "date": t})
    return rows


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic legacy opportunity export")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--out-dir", default="data/generated")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)

    users, ghost_owner_ids = build_users(rng)
    active_users = [u for u in users if u["IsActive"] == "true"]
    inactive_users = [u for u in users if u["IsActive"] == "false"]
    accounts = build_accounts(rng, n_accounts=max(args.n // 4, 50))

    n = args.n
    indices = list(range(n))
    rng.shuffle(indices)

    # Non-overlapping corruption budget. See module docstring for how the
    # doc's approximate targets were reconciled into concrete counts.
    budget = {
        # LOUD (850)
        "close_before_create": 160,
        "modified_before_created": 130,
        "amount_as_text": 200,
        "negative_amount_won": 90,
        "stage_freetext_pollution": 150,
        "duplicate_id": 60,  # pairs -> 120 rows
        # SILENT, detected today (~225)
        "closed_null_close_date": 20,
        "placeholder_close_date": 15,
        "stage_change_after_close": 20,
        "zero_amount_closed_won": 15,
        "missing_currency": 25,
        "ambiguous_date_format": 15,
        "owner_blank": 15,
        "owner_is_queue": 15,
        "owner_departed": 15,
        "owner_inactive": 15,
        "closed_lost_no_reason": 15,
        "invalid_picklist_closed_lost_reason": 10,
        "gate_missing_account_id": 20,
        "possible_duplicate_deal": 15,  # pairs -> 30 rows
        # SILENT, stage-mapping — not detected until day 2 stage resolution (~180)
        "a1_unresolved": 90,
        "a2_near_miss": 20,
        "a3_hidden_semantic_drift": 40,
        "a4_deprecated_renamed": 30,
    }

    cursor = 0
    slots: dict[str, list[int]] = {}
    for cls, count in budget.items():
        rows = 2 * count if cls in ("duplicate_id", "possible_duplicate_deal") else count
        slots[cls] = indices[cursor : cursor + rows]
        cursor += rows
    clean_slots = set(indices[cursor:])

    opps: list[Opp] = []
    manifest_rows: list[dict] = []
    index_to_class = {i: cls for cls, idxs in slots.items() for i in idxs}

    i = 0
    while i < n:
        cls = index_to_class.get(indices[i])

        if cls == "duplicate_id":
            # A literal re-export of the same record under one Id — the
            # common real-world case — rather than two unrelated records
            # colliding, which would merge two histories under one Id and
            # corrupt the C5 check for whichever row isn't first.
            a = make_clean_opp(rng, active_users, accounts)
            b = copy.deepcopy(a)
            b.history = []  # only one copy contributes history rows
            for opp in (a, b):
                opps.append(opp)
                manifest_rows.append(_manifest_row(opp.opportunity_id, "F2_duplicate_id", "LOUD"))
            i += 2
            continue

        if cls == "possible_duplicate_deal":
            a = make_clean_opp(rng, active_users, accounts)
            b = make_clean_opp(rng, active_users, accounts)
            b.account_id = a.account_id
            base_company = a.name.rsplit(" - ", 1)[0]
            variants = [f"{base_company} Enterprise Deal", f"{base_company.replace('Inc.', '').replace('LLC', '').strip()} - Renewal Deal"]
            b.name = rng.choice(variants)
            created_a = date.fromisoformat(a.created_date)
            old_b_created = date.fromisoformat(b.created_date)
            new_b_created = created_a + timedelta(days=rng.randint(-10, 10))
            shift = new_b_created - old_b_created
            b.created_date = new_b_created.isoformat()
            b.close_date = (date.fromisoformat(b.close_date) + shift).isoformat()
            b.last_modified_date = (date.fromisoformat(b.last_modified_date) + shift).isoformat()
            for h in b.history:
                h["date"] = h["date"] + shift
            b.amount = f"{float(a.amount) * rng.uniform(1.1, 1.6):.2f}"
            for opp in (a, b):
                opps.append(opp)
                manifest_rows.append(_manifest_row(opp.opportunity_id, "F3_duplicate_deal", "SILENT"))
            i += 2
            continue

        opp = make_clean_opp(rng, active_users, accounts)

        if cls is None:
            opps.append(opp)
            i += 1
            continue

        _apply_corruption(rng, opp, cls, inactive_users, ghost_owner_ids)
        opps.append(opp)
        manifest_rows.append(_manifest_row(opp.opportunity_id, cls, _severity_of(cls)))
        i += 1

    _write_opportunities(args.out_dir, opps)
    _write_history(args.out_dir, opps)
    _write_users(args.out_dir, users)
    _write_manifest(args.out_dir, manifest_rows)

    print(f"Wrote {len(opps)} opportunities to {args.out_dir}/")
    print(f"Clean: {n - len(manifest_rows)}  |  Corrupted-row entries in manifest: {len(manifest_rows)}")
    print(f"Users: {len(users)} ({len(active_users)} active, {len(inactive_users)} inactive), "
          f"seat_count in config should be less than {len(active_users)} to trigger D5.")


CLASS_SEVERITY = {
    "close_before_create": "LOUD",
    "modified_before_created": "LOUD",
    "amount_as_text": "LOUD",
    "negative_amount_won": "LOUD",
    "stage_freetext_pollution": "LOUD",
    "closed_null_close_date": "SILENT",
    "placeholder_close_date": "SILENT",
    "stage_change_after_close": "SILENT",
    "zero_amount_closed_won": "SILENT",
    "missing_currency": "SILENT",
    "ambiguous_date_format": "SILENT",
    "owner_blank": "SILENT",
    "owner_is_queue": "SILENT",
    "owner_departed": "SILENT",
    "owner_inactive": "SILENT",
    "closed_lost_no_reason": "SILENT",
    "invalid_picklist_closed_lost_reason": "SILENT",
    "gate_missing_account_id": "SILENT",
    "a1_unresolved": "SILENT",
    "a2_near_miss": "SILENT",
    "a3_hidden_semantic_drift": "SILENT",
    "a4_deprecated_renamed": "SILENT",
}


def _severity_of(cls: str) -> str:
    return CLASS_SEVERITY[cls]


def _manifest_row(opp_id: str, cls: str, severity: str) -> dict:
    return {"OpportunityId": opp_id, "CorruptionClass": cls, "Severity": severity}


def _apply_corruption(rng: random.Random, opp: Opp, cls: str, inactive_users, ghost_owner_ids):
    created = date.fromisoformat(opp.created_date)
    close = date.fromisoformat(opp.close_date)

    if cls == "close_before_create":
        opp.close_date = (created - timedelta(days=rng.randint(1, 30))).isoformat()

    elif cls == "modified_before_created":
        opp.last_modified_date = (created - timedelta(days=rng.randint(1, 10))).isoformat()

    elif cls == "amount_as_text":
        variant = rng.choice(["${:,.2f}", "{:,.0f}", "{:.0f} USD"])
        opp.amount = variant.format(float(opp.amount))

    elif cls == "negative_amount_won":
        opp.stage_raw = "Closed Won"
        opp.is_closed, opp.is_won = "true", "true"
        opp.closed_lost_reason = ""
        opp.amount = f"-{abs(float(opp.amount)):.2f}"

    elif cls == "stage_freetext_pollution":
        opp.stage_raw = "Closed Won"
        opp.is_closed, opp.is_won = "true", "true"
        opp.closed_lost_reason = ""
        variant = rng.choice(["closed won", "CLOSED WON", "Closed Won "])
        opp.stage_raw = variant

    elif cls == "closed_null_close_date":
        opp.stage_raw = rng.choice(["Closed Won", "Closed Lost"])
        opp.is_closed = "true"
        opp.is_won = "true" if opp.stage_raw == "Closed Won" else "false"
        if opp.is_won == "false":
            opp.closed_lost_reason = rng.choice(CLOSED_LOST_REASONS_VALID)
        opp.close_date = ""

    elif cls == "placeholder_close_date":
        opp.close_date = rng.choice(["2099-12-31", "2050-01-01"])

    elif cls == "stage_change_after_close":
        if opp.history:
            opp.history[-1]["date"] = close + timedelta(days=rng.randint(1, 20))

    elif cls == "zero_amount_closed_won":
        opp.stage_raw = "Closed Won"
        opp.is_closed, opp.is_won = "true", "true"
        opp.closed_lost_reason = ""
        opp.amount = rng.choice(["0.00", ""])

    elif cls == "missing_currency":
        opp.currency = ""

    elif cls == "ambiguous_date_format":
        d = created
        if d.day <= 12 and d.month <= 12:
            opp.created_date = f"{d.month:02d}/{d.day:02d}/{d.year}"
        else:
            shifted = created.replace(day=min(created.day, 12) if created.day > 12 else created.day)
            opp.created_date = f"{shifted.month:02d}/{shifted.day:02d}/{shifted.year}"

    elif cls == "owner_blank":
        opp.owner_id = ""

    elif cls == "owner_is_queue":
        opp.owner_id = rng.choice(QUEUE_ALIASES)

    elif cls == "owner_departed":
        opp.owner_id = rng.choice(ghost_owner_ids)

    elif cls == "owner_inactive":
        opp.owner_id = rng.choice(inactive_users)["UserId"]

    elif cls == "closed_lost_no_reason":
        opp.stage_raw = "Closed Lost"
        opp.is_closed, opp.is_won = "true", "false"
        opp.closed_lost_reason = ""

    elif cls == "invalid_picklist_closed_lost_reason":
        opp.stage_raw = "Closed Lost"
        opp.is_closed, opp.is_won = "true", "false"
        opp.closed_lost_reason = rng.choice(CLOSED_LOST_REASONS_INVALID)

    elif cls == "gate_missing_account_id":
        # Reevo's own stage-gating requires AccountId from Qualification on.
        # Force a stage where that requirement applies, otherwise this
        # corruption would be a silent no-op for any row that landed on
        # Prospecting (no account_id requirement there).
        opp.stage_raw = "Qualification"
        opp.is_closed, opp.is_won = "false", "false"
        opp.account_id = ""

    elif cls == "a1_unresolved":
        opp.stage_raw = rng.choice(A1_UNRESOLVED_STAGES)
        opp.is_closed = "true" if "Closed" in opp.stage_raw else "false"
        opp.is_won = "false"
        # "Closed - No Decision" reads as closed+lost; give it a reason so
        # this row tests A1 (stage mapping) only, not incidentally B1 too.
        if opp.is_closed == "true" and not opp.closed_lost_reason:
            opp.closed_lost_reason = rng.choice(CLOSED_LOST_REASONS_VALID)

    elif cls == "a2_near_miss":
        opp.stage_raw = A2_STAGE
        opp.is_closed, opp.is_won = "false", "false"

    elif cls == "a3_hidden_semantic_drift":
        opp.stage_raw = A3_LEGACY_STAGE  # maps cleanly to Qualification; drift is invisible
        opp.is_closed, opp.is_won = "false", "false"

    elif cls == "a4_deprecated_renamed":
        opp.stage_raw = A4_DEPRECATED_STAGE
        opp.is_closed, opp.is_won = "false", "false"
        old_created = date(2021, 1, 1) + timedelta(days=rng.randint(0, 365))
        opp.created_date = old_created.isoformat()
        opp.close_date = (old_created + timedelta(days=rng.randint(30, 120))).isoformat()
        opp.last_modified_date = (old_created + timedelta(days=rng.randint(1, 20))).isoformat()
        opp.history = []  # stale relative to the rewritten dates; not a C5 test case


def _write_opportunities(out_dir: str, opps: list[Opp]):
    path = os.path.join(out_dir, "opportunities.csv")
    fields = [
        "Id", "Name", "AccountId", "OwnerId", "StageName", "Amount", "CurrencyIsoCode",
        "CloseDate", "CreatedDate", "LastModifiedDate", "ClosedLostReason", "IsClosed", "IsWon",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for o in opps:
            w.writerow([
                o.opportunity_id, o.name, o.account_id, o.owner_id, o.stage_raw, o.amount,
                o.currency, o.close_date, o.created_date, o.last_modified_date,
                o.closed_lost_reason, o.is_closed, o.is_won,
            ])


def _write_history(out_dir: str, opps: list[Opp]):
    path = os.path.join(out_dir, "opportunity_history.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["OpportunityId", "StageName", "Amount", "Probability", "CloseDate", "CreatedDate"])
        for o in opps:
            for h in o.history:
                w.writerow([o.opportunity_id, h["stage"], o.amount, "", o.close_date, h["date"].isoformat()])


def _write_users(out_dir: str, users: list[dict]):
    path = os.path.join(out_dir, "users.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["UserId", "FirstName", "LastName", "Email", "IsActive"])
        for u in users:
            w.writerow([u["UserId"], u["FirstName"], u["LastName"], u["Email"], u["IsActive"]])


def _write_manifest(out_dir: str, rows: list[dict]):
    path = os.path.join(out_dir, "manifest.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["OpportunityId", "CorruptionClass", "Severity"])
        for r in rows:
            w.writerow([r["OpportunityId"], r["CorruptionClass"], r["Severity"]])


if __name__ == "__main__":
    main()
