from __future__ import annotations

import difflib
import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from .config import PipelineConfig
from .models import Coercion, Finding, HistoryRow, NormalizedOpportunity, Severity, UserRecord

PLACEHOLDER_DATES = {date(2099, 12, 31), date(2050, 1, 1)}

# Salesforce IDs are 15/18-char alphanumeric. Anything else in an owner field
# is a queue/alias, not a user reference (D3).
ID_SHAPE_RE = re.compile(r"^[a-zA-Z0-9]{15,18}$")

DUPLICATE_NAME_SIMILARITY_MIN = 0.55
DUPLICATE_NAME_SIMILARITY_MAX = 0.97
DUPLICATE_DATE_WINDOW_DAYS = 14


def _stage_freetext_pollution(raw: str, canonical_names: set[str]) -> bool:
    stripped = raw.strip()
    for canon in canonical_names:
        if stripped.lower() == canon.lower() and raw != canon:
            return True
    return False


def check_rules(
    opps: list[NormalizedOpportunity],
    history: dict[str, list[HistoryRow]],
    users: dict[str, UserRecord],
    config: PipelineConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    id_counts = Counter(o.opportunity_id for o in opps if o.opportunity_id)
    canonical_stage_names = set(config.stage_names()) | set(config.stage_aliases.keys())

    for o in opps:
        oid = o.opportunity_id

        # F2 — duplicate opportunity Id. LOUD: unique-ID constraint rejects it.
        if oid and id_counts[oid] > 1:
            findings.append(
                Finding(
                    "duplicate_id",
                    Severity.LOUD,
                    oid,
                    f"Opportunity ID {oid!r} appears {id_counts[oid]} times in export.",
                )
            )

        # C1 — CloseDate before CreatedDate. LOUD.
        if o.close_date and o.created_date and o.close_date < o.created_date:
            findings.append(
                Finding(
                    "close_before_create",
                    Severity.LOUD,
                    oid,
                    "Close date precedes created date.",
                )
            )

        # C2 — IsClosed true, null CloseDate. SILENT.
        if o.is_closed and o.close_date is None:
            findings.append(
                Finding(
                    "closed_null_close_date",
                    Severity.SILENT,
                    oid,
                    "Closed opportunity with no close date.",
                    broken_report="cycle-time and pipeline velocity reporting",
                )
            )

        # C3 — placeholder far-future date. SILENT.
        if o.close_date in PLACEHOLDER_DATES:
            findings.append(
                Finding(
                    "placeholder_close_date",
                    Severity.SILENT,
                    oid,
                    f"Close date is placeholder {o.close_date}.",
                    broken_report="weighted pipeline forecast (date-bucketed)",
                )
            )

        # C4 — LastModifiedDate before CreatedDate. LOUD.
        if o.last_modified_date and o.created_date and o.last_modified_date < o.created_date:
            findings.append(
                Finding(
                    "modified_before_created",
                    Severity.LOUD,
                    oid,
                    "Last-modified date precedes created date.",
                )
            )

        # C5 — history transition timestamped after close date. SILENT.
        # Skipped when close predates create (C1 already caught): a close
        # date that's nonsensical on its face isn't a meaningful bound to
        # check activity against, and flagging both just double-counts one
        # broken field as two findings.
        close_is_sane = not (o.close_date and o.created_date and o.close_date < o.created_date)
        for h in history.get(oid, []):
            if close_is_sane and o.close_date and h.created_date and h.created_date > o.close_date:
                findings.append(
                    Finding(
                        "stage_change_after_close",
                        Severity.SILENT,
                        oid,
                        "Stage-change activity recorded after close date.",
                        broken_report="Q3 stage-conversion reporting",
                    )
                )
                break

        # E1 — IsWon true, null/zero amount. SILENT.
        if o.is_won and (o.amount is None or o.amount == Decimal(0)):
            findings.append(
                Finding(
                    "zero_amount_closed_won",
                    Severity.SILENT,
                    oid,
                    "Closed-won opportunity with null or zero amount.",
                    broken_report="weighted pipeline forecast",
                )
            )

        # E2 — multi-currency org, blank currency field. SILENT.
        if o.amount is not None and o.currency is None and config.multi_currency:
            findings.append(
                Finding(
                    "missing_currency",
                    Severity.SILENT,
                    oid,
                    "Amount present, no currency field, target org is multi-currency.",
                    broken_report="weighted pipeline forecast (cross-currency)",
                )
            )

        # E3 — amount stored as text. LOUD: numeric column type mismatch on import.
        if o.amount_is_text:
            findings.append(
                Finding(
                    "amount_as_text",
                    Severity.LOUD,
                    oid,
                    "Amount field is not a clean numeric literal (symbols/separators/units).",
                )
            )

        # E4 — negative amount on a won deal. LOUD.
        if o.is_won and o.amount is not None and o.amount < 0:
            findings.append(
                Finding(
                    "negative_amount_won",
                    Severity.LOUD,
                    oid,
                    "Closed-won opportunity has a negative amount.",
                )
            )

        # A5 — free-text pollution: case/whitespace variant of a canonical stage
        # string. LOUD: exact-match picklist validation rejects it.
        if o.stage_raw and _stage_freetext_pollution(o.stage_raw, canonical_stage_names):
            findings.append(
                Finding(
                    "stage_freetext_pollution",
                    Severity.LOUD,
                    oid,
                    f"Stage {o.stage_raw!r} is a case/whitespace variant of a known stage name.",
                )
            )

        # B1 — closed-lost with no loss reason. SILENT: bulk import bypasses
        # the required-at-close-stage business rule.
        if o.is_closed and o.is_won is False and not o.closed_lost_reason:
            findings.append(
                Finding(
                    "closed_lost_no_reason",
                    Severity.SILENT,
                    oid,
                    "Closed-lost with no Primary Closed Lost Reason.",
                    broken_report="loss-reason breakdown / win-rate analysis",
                )
            )

        # B3 — picklist value not present in target. SILENT.
        valid_reasons = config.picklists.get("closed_lost_reason")
        if o.closed_lost_reason and valid_reasons is not None and o.closed_lost_reason not in valid_reasons:
            findings.append(
                Finding(
                    "invalid_picklist_closed_lost_reason",
                    Severity.SILENT,
                    oid,
                    f"Closed Lost Reason {o.closed_lost_reason!r} not in target picklist.",
                    broken_report="loss-reason breakdown",
                )
            )

        # D1-D4 — ownership. All SILENT: bulk import reassigns/accepts rather
        # than rejecting, so these never show up as import errors.
        owner = o.owner_id
        if not owner:
            findings.append(
                Finding(
                    "owner_blank",
                    Severity.SILENT,
                    oid,
                    "Owner is blank; import will reassign to whoever runs it.",
                    broken_report="rep leaderboard and quota attainment",
                )
            )
        elif not ID_SHAPE_RE.match(owner):
            findings.append(
                Finding(
                    "owner_is_queue",
                    Severity.SILENT,
                    oid,
                    f"Owner {owner!r} is a queue/alias, not a rep.",
                    broken_report="rep leaderboard and quota attainment",
                )
            )
        elif owner not in users:
            findings.append(
                Finding(
                    "owner_departed",
                    Severity.SILENT,
                    oid,
                    f"Owner {owner!r} not found in target user list (rep left before export).",
                    broken_report="rep leaderboard and quota attainment",
                )
            )
        elif not users[owner].is_active:
            findings.append(
                Finding(
                    "owner_inactive",
                    Severity.SILENT,
                    oid,
                    f"Owner {owner!r} is inactive; import will reassign to whoever runs it.",
                    broken_report="rep leaderboard and quota attainment",
                )
            )

    # D5 — more distinct active owners referenced than seats licensed. SILENT,
    # portfolio-level rather than per-row.
    if config.seat_count is not None:
        active_owner_ids = {
            o.owner_id for o in opps if o.owner_id and o.owner_id in users and users[o.owner_id].is_active
        }
        if len(active_owner_ids) > config.seat_count:
            findings.append(
                Finding(
                    "owner_seat_overage",
                    Severity.SILENT,
                    "—",
                    f"{len(active_owner_ids)} distinct active owners referenced across the export; "
                    f"target is licensed for {config.seat_count} seats.",
                    broken_report="rep leaderboard / license reconciliation",
                )
            )

    # F3 — same deal entered twice under different names. SILENT, fuzzy.
    findings.extend(_duplicate_deal_findings(opps))

    return findings


def _duplicate_deal_findings(opps: list[NormalizedOpportunity]) -> list[Finding]:
    findings: list[Finding] = []
    by_account: dict[str, list[NormalizedOpportunity]] = {}
    for o in opps:
        if o.account_id:
            by_account.setdefault(o.account_id, []).append(o)

    flagged_pairs: set[tuple[str, str]] = set()
    for account_opps in by_account.values():
        if len(account_opps) < 2:
            continue
        for i in range(len(account_opps)):
            for j in range(i + 1, len(account_opps)):
                a, b = account_opps[i], account_opps[j]
                if not a.name or not b.name:
                    continue

                ratio = difflib.SequenceMatcher(None, a.name.lower(), b.name.lower()).ratio()
                if not (DUPLICATE_NAME_SIMILARITY_MIN <= ratio <= DUPLICATE_NAME_SIMILARITY_MAX):
                    continue

                a_date = a.created_date or a.close_date
                b_date = b.created_date or b.close_date
                if not a_date or not b_date:
                    continue
                if abs((a_date - b_date).days) > DUPLICATE_DATE_WINDOW_DAYS:
                    continue

                if a.amount == b.amount:
                    continue

                pair_key = tuple(sorted([a.opportunity_id, b.opportunity_id]))
                if pair_key in flagged_pairs:
                    continue
                flagged_pairs.add(pair_key)

                for this, other in ((a, b), (b, a)):
                    findings.append(
                        Finding(
                            "possible_duplicate_deal",
                            Severity.SILENT,
                            this.opportunity_id,
                            f"Possible duplicate of {other.opportunity_id!r} ({other.name!r}): same account, "
                            f"overlapping dates, different amount.",
                            broken_report="inflated pipeline totals (double-counted deal)",
                        )
                    )

    return findings


def ambiguous_date_findings(coercions: list[Coercion]) -> list[Finding]:
    findings = []
    for c in coercions:
        if c.note.startswith("AMBIGUOUS"):
            findings.append(
                Finding(
                    "ambiguous_date_format",
                    Severity.SILENT,
                    c.opportunity_id,
                    f"{c.field} = {c.raw_value!r}: {c.note}.",
                    broken_report="any date-bucketed report (cycle time, cohort, conversion)",
                )
            )
    return findings
