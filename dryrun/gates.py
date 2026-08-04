from __future__ import annotations

from decimal import Decimal

from .config import PipelineConfig
from .models import Finding, NormalizedOpportunity, Severity
from .stages import StageResolution, StageVerdict

FIELD_LABELS = {
    "amount": "Amount",
    "owner": "Owner",
    "account_id": "AccountId",
    "close_date": "CloseDate",
    "closed_lost_reason": "Primary Closed Lost Reason",
}

# Fields where an existing rules.py check already covers the same defect
# (using IsWon/IsClosed directly, independent of stage resolution). Skipping
# these here avoids reporting one broken field as two separate findings.
# account_id is deliberately absent: nothing else checks it.
_COVERED_BY_EXISTING_RULE = {
    "closed_lost_reason": lambda o: bool(o.is_closed) and o.is_won is False and not o.closed_lost_reason,
    "amount": lambda o: bool(o.is_won) and (o.amount is None or o.amount == Decimal(0)),
    "close_date": lambda o: bool(o.is_closed) and o.close_date is None,
    "owner": lambda o: not o.owner_id,
}


def _is_blank(o: NormalizedOpportunity, field: str) -> bool:
    if field == "owner":
        return not o.owner_id
    value = getattr(o, field)
    return value is None if field in ("amount", "close_date") else not value


def check_gates(
    opps: list[NormalizedOpportunity],
    resolutions: dict[str, StageResolution],
    config: PipelineConfig,
) -> list[Finding]:
    """Gate validation: for each opportunity whose stage resolved to a target
    stage, check it against that stage's required fields. This is Reevo's own
    app-level stage-gating business rule — enforced on save in the live app,
    bypassed entirely by a raw bulk import. SILENT by construction: that's
    the whole reason this pass exists (see CLAUDE.md)."""
    stage_by_name = {s.name: s for s in config.stages}
    findings: list[Finding] = []

    for o in opps:
        res = resolutions.get(o.stage_raw)
        if res is None or res.verdict != StageVerdict.MAPPED or res.target_stage is None:
            continue
        stage_cfg = stage_by_name.get(res.target_stage)
        if not stage_cfg:
            continue

        for field in stage_cfg.required_fields:
            if not _is_blank(o, field):
                continue
            covered = _COVERED_BY_EXISTING_RULE.get(field)
            if covered and covered(o):
                continue  # already reported by a dedicated rule
            findings.append(
                Finding(
                    f"gate_missing_{field}",
                    Severity.SILENT,
                    o.opportunity_id,
                    f"{FIELD_LABELS[field]} required at {res.target_stage!r} stage per Reevo config, "
                    f"but missing — record would be rejected on save in the live app.",
                    broken_report=f"{res.target_stage} stage-gate integrity",
                )
            )

    return findings
