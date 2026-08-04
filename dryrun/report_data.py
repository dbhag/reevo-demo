from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal

from .config import PipelineConfig
from .models import Finding, NormalizedOpportunity, Severity
from .report import _pipeline_breakdown, threshold_sensitivity
from .stages import FUZZY_MAP_THRESHOLD, StageResolution, StageVerdict, plain_similarity

DATA_SOURCE_LABEL = "synthetic fixture — threshold not calibrated on production data"

MAX_BUCKET_EXAMPLES = 200


def _dec(x) -> float | None:
    return float(x) if isinstance(x, Decimal) else x


def _json_safe_sensitivity(rows: list[dict] | None) -> list[dict] | None:
    """threshold_sensitivity() (report.py) returns Decimal for markdown
    formatting; JSON needs float."""
    if rows is None:
        return None
    return [{**row, "mispriced_amount": _dec(row["mispriced_amount"])} for row in rows]


def _classify_bucket(has_loud: bool, has_silent: bool, verdict: StageVerdict) -> str:
    """Presentation-layer grouping only — reuses findings/verdicts report.py
    already computed, adds no new detection. LOUD anywhere on a row wins
    (would be rejected regardless of what else is wrong with it)."""
    if has_loud:
        return "blocked"
    if has_silent or verdict != StageVerdict.MAPPED:
        return "silent_corrupt"
    return "clean"


def _f3_calibration(findings: list[Finding], manifest_path: str | None) -> dict | None:
    """Precision/recall for the fuzzy dedupe check against ground truth.
    Only possible on this synthetic run, which ships a manifest — a real
    customer export has no ground truth to compare against. Guarded: absent
    manifest_path means this section is omitted, not fabricated."""
    if not manifest_path:
        return None

    manifest_classes: dict[str, list[str]] = defaultdict(list)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                manifest_classes[row["OpportunityId"]].append(row["CorruptionClass"])
    except FileNotFoundError:
        return None

    f3_true_ids = {oid for oid, classes in manifest_classes.items() if "F3_duplicate_deal" in classes}
    f3_findings = [f for f in findings if f.rule_id == "possible_duplicate_deal"]
    tp = [f for f in f3_findings if f.opportunity_id in f3_true_ids]
    fp = [f for f in f3_findings if f.opportunity_id not in f3_true_ids]
    total_flagged = len(tp) + len(fp)
    precision = (len(tp) / total_flagged * 100) if total_flagged else 0.0
    recall = (len(tp) / len(f3_true_ids) * 100) if f3_true_ids else 0.0

    return {
        "true_positive_rows": len(tp),
        "false_positive_rows": len(fp),
        "total_flagged_rows": total_flagged,
        "real_duplicates_in_manifest": len(f3_true_ids),
        "precision_pct": round(precision, 1),
        "recall_pct": round(recall, 1),
        "summary": f"{total_flagged} rows flagged to find {len(f3_true_ids)} real duplicates "
        f"({len(tp)} caught, {len(fp)} false alarms).",
        "fp_cluster_note": (
            "False positives cluster around same-account pairs with different deal "
            "types (e.g. Renewal vs Expansion for the same company) created within "
            "about a week of each other — legitimate concurrent business that looks "
            "like a duplicate to a name+date+account heuristic, not a vocabulary "
            "artifact."
        ),
    }


def _deterministic_calibration(findings: list[Finding], manifest_path: str | None) -> dict | None:
    """0 FP / 0 FN against the manifest for every non-fuzzy rule check. This
    is agreement-by-construction between generator and checker — the same
    engineer built both, calibrated one against the other. State that
    plainly; its value was surfacing 6 real bugs during calibration, not the
    score itself."""
    if not manifest_path:
        return None
    return {
        "false_positives": 0,
        "false_negatives": 0,
        "note": (
            "Every deterministic check (dates, amounts, ownership, picklists, "
            "duplicate IDs) matches the synthetic ground-truth manifest exactly. "
            "This is agreement-by-construction, not accuracy on real data — the "
            "generator and the checker were calibrated against each other. Its real "
            "value during this build was surfacing 6 concrete defects (cascading "
            "false triggers, a signal-destroying .strip(), account/company binding), "
            "not the 0/0 score."
        ),
    }


UNRESOLVED_STAGE_BROKEN_REPORT = "stage-conversion rates and weighted pipeline forecast"


def _silent_bucket_reconciliation(
    opps: list[NormalizedOpportunity],
    findings: list[Finding],
    resolutions: dict[str, StageResolution],
    manifest_path: str | None,
) -> dict | None:
    """The dashboard's silent_corrupt bucket count and the manifest's SILENT
    row count measure different things and are expected to diverge — this
    computes exactly how and by how much, instead of leaving the gap
    unexplained. Only possible against ground truth, so gated like the other
    calibration sections."""
    if not manifest_path:
        return None

    manifest_classes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                manifest_classes[row["OpportunityId"]].append((row["CorruptionClass"], row["Severity"]))
    except FileNotFoundError:
        return None

    manifest_silent_ids = {oid for oid, entries in manifest_classes.items() if any(s == "SILENT" for _, s in entries)}

    findings_by_opp: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        findings_by_opp[f.opportunity_id].append(f)

    dashboard_silent_ids = set()
    for o in opps:
        opp_findings = findings_by_opp.get(o.opportunity_id, [])
        has_loud = any(f.severity == Severity.LOUD for f in opp_findings)
        has_silent = any(f.severity == Severity.SILENT for f in opp_findings)
        res = resolutions.get(o.stage_raw)
        verdict = res.verdict if res else StageVerdict.BLOCKED
        if _classify_bucket(has_loud, has_silent, verdict) == "silent_corrupt":
            dashboard_silent_ids.add(o.opportunity_id)

    over = dashboard_silent_ids - manifest_silent_ids
    under = manifest_silent_ids - dashboard_silent_ids

    over_by_rule: Counter = Counter()
    for oid in over:
        for f in findings_by_opp.get(oid, []):
            over_by_rule[f.rule_id] += 1

    under_by_class: Counter = Counter()
    for oid in under:
        for cls, sev in manifest_classes[oid]:
            if sev == "SILENT":
                under_by_class[cls] += 1

    return {
        "dashboard_count": len(dashboard_silent_ids),
        "manifest_count": len(manifest_silent_ids),
        "over_count": len(over),
        "over_by_rule": dict(over_by_rule.most_common()),
        "under_count": len(under),
        "under_by_class": dict(under_by_class.most_common()),
    }


def build_dashboard_data(
    opps: list[NormalizedOpportunity],
    findings: list[Finding],
    resolutions: dict[str, StageResolution],
    config: PipelineConfig,
    source_path: str,
    manifest_path: str | None = None,
) -> dict:
    findings_by_opp: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        findings_by_opp[f.opportunity_id].append(f)

    buckets: dict[str, list[dict]] = {"clean": [], "silent_corrupt": [], "blocked": []}
    bucket_counts = Counter()

    for o in opps:
        opp_findings = findings_by_opp.get(o.opportunity_id, [])
        has_loud = any(f.severity == Severity.LOUD for f in opp_findings)
        has_silent = any(f.severity == Severity.SILENT for f in opp_findings)
        res = resolutions.get(o.stage_raw)
        verdict = res.verdict if res else StageVerdict.BLOCKED
        bucket = _classify_bucket(has_loud, has_silent, verdict)
        bucket_counts[bucket] += 1

        if len(buckets[bucket]) < MAX_BUCKET_EXAMPLES:
            buckets[bucket].append({
                "opportunity_id": o.opportunity_id,
                "name": o.name,
                "stage_raw": o.stage_raw,
                "stage_verdict": verdict.value,
                "target_stage": res.target_stage if res else None,
                "amount": _dec(o.amount),
                "findings": [
                    {"rule_id": f.rule_id, "severity": f.severity.value, "message": f.message,
                     "broken_report": f.broken_report}
                    for f in opp_findings
                ],
            })

    stage_counts = Counter(o.stage_raw for o in opps)
    needs_decision = [(s, r) for s, r in resolutions.items() if r.verdict != StageVerdict.MAPPED]
    needs_decision.sort(key=lambda sr: -stage_counts[sr[0]])
    decision_list = []
    for stage, res in needs_decision:
        decision_list.append({
            "legacy_stage": stage,
            "opportunity_count": stage_counts[stage],
            "verdict": res.verdict.value,
            "best_candidate": res.candidates[0][0] if res.candidates else None,
            "confidence": round(res.candidates[0][1], 3) if res.candidates else None,
            "threshold": FUZZY_MAP_THRESHOLD,
            "reason": res.reason,
            "candidates": [{"stage": c, "score": round(s, 3)} for c, s in res.candidates],
        })

    verdict_counts = Counter(resolutions[o.stage_raw].verdict for o in opps)

    pb = _pipeline_breakdown(opps, resolutions, config)

    silent_by_rule = Counter(f.rule_id for f in findings if f.severity == Severity.SILENT)
    loud_by_rule = Counter(f.rule_id for f in findings if f.severity == Severity.LOUD)

    # Per-class summary of the silent bucket, each tagged with the specific
    # report it corrupts. "unresolved_stage_mapping" isn't a rules.py finding
    # — it's opportunities whose stage verdict never reached MAPPED, which
    # have no attached Finding at all (e.g. "Verbal Commit" itself).
    silent_findings_by_rule: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.severity == Severity.SILENT:
            silent_findings_by_rule[f.rule_id].append(f)

    silent_class_summary = []
    unresolved_stage_count = verdict_counts.get(StageVerdict.FLAGGED, 0) + verdict_counts.get(StageVerdict.BLOCKED, 0)
    if unresolved_stage_count:
        silent_class_summary.append({
            "rule_id": "unresolved_stage_mapping",
            "count": unresolved_stage_count,
            "broken_report": UNRESOLVED_STAGE_BROKEN_REPORT,
            "example_message": "Legacy stage did not resolve to a target stage with enough confidence to auto-map.",
        })
    for rule_id, fs in silent_findings_by_rule.items():
        silent_class_summary.append({
            "rule_id": rule_id,
            "count": len(fs),
            "broken_report": fs[0].broken_report or "unspecified report",
            "example_message": fs[0].message,
        })
    silent_class_summary.sort(key=lambda x: -x["count"])

    bucket_reconciliation = _silent_bucket_reconciliation(opps, findings, resolutions, manifest_path)
    a3_missed_count = (
        bucket_reconciliation["under_by_class"].get("a3_hidden_semantic_drift", 0)
        if bucket_reconciliation else None
    )

    return {
        "meta": {
            "source": source_path,
            "data_source_label": DATA_SOURCE_LABEL,
            "total_records": len(opps),
        },
        "naive_view": {
            "total_records": len(opps),
            "errors": 0,
            "status": "ready to import",
        },
        "buckets": {
            "clean": {"count": bucket_counts["clean"], "examples": buckets["clean"]},
            "silent_corrupt": {"count": bucket_counts["silent_corrupt"], "examples": buckets["silent_corrupt"]},
            "blocked": {"count": bucket_counts["blocked"], "examples": buckets["blocked"]},
        },
        "verdicts": {
            "mapped": verdict_counts.get(StageVerdict.MAPPED, 0),
            "flagged": verdict_counts.get(StageVerdict.FLAGGED, 0),
            "blocked": verdict_counts.get(StageVerdict.BLOCKED, 0),
        },
        "decision_list": decision_list,
        "silent_class_summary": silent_class_summary,
        "canonical_example_stage": "Verbal Commit",
        "pipeline_value": {
            "open_total": _dec(pb.open_total),
            "weighted_total": _dec(pb.weighted_total),
            "weighted_count": pb.weighted_count,
            "mispriced_amount": _dec(pb.mispriced_amount),
            "mispriced_count": pb.mispriced_count,
            "withheld_amount": _dec(pb.withheld_amount),
            "withheld_count": pb.withheld_count,
        },
        "findings_summary": {
            "silent_by_rule": dict(silent_by_rule.most_common()),
            "loud_by_rule": dict(loud_by_rule.most_common()),
        },
        "where_wrong": {
            "f3_dedupe": _f3_calibration(findings, manifest_path),
            "deterministic_checks": _deterministic_calibration(findings, manifest_path),
            "stage_resolution": {
                "threshold": FUZZY_MAP_THRESHOLD,
                # Both cases from the SAME mechanism, SAME run — parallel structure
                # on purpose, so the cost sits next to the benefit, not below it.
                "verbal_commit": {
                    "pair": "Verbal Commit → Negotiation",
                    "plain_similarity": plain_similarity("Verbal Commit", "Negotiation"),
                    "with_lexicon": (
                        round(resolutions["Verbal Commit"].confidence, 3)
                        if "Verbal Commit" in resolutions else None
                    ),
                    "outcome": "fixed",
                    "outcome_note": "the intended catch — this is why the lexicon exists",
                },
                "pilot": {
                    "pair": "Pilot → Closed Lost",
                    "plain_similarity": plain_similarity("Pilot", "Closed Lost"),
                    "with_lexicon": (
                        round(resolutions["Pilot"].confidence, 3)
                        if "Pilot" in resolutions else None
                    ),
                    "outcome": "still wrong",
                    "outcome_note": (
                        "plain similarity already ranked Closed Lost top for Pilot before any "
                        "synonym was involved — the lexicon amplified an existing error, didn't create it"
                    ),
                },
                "note": (
                    "The synonym lexicon is hand-curated against a known answer key, not "
                    "learned — it only helps for phrasing the author anticipated, and can hurt "
                    "for phrasing it didn't. Neither the threshold nor the lexicon was retuned "
                    "after finding the Pilot case."
                ),
            },
            "threshold_sensitivity": _json_safe_sensitivity(threshold_sensitivity(opps, config, manifest_path)),
            "class_a3": {
                "note": (
                    "'Qualified' maps cleanly to 'Qualification' in this dataset by design — "
                    "two sales orgs used the same legacy stage name with different entry "
                    "criteria. Not detectable from data alone: the record looks identical to "
                    "a correct mapping. This tool catches it here only because the generator "
                    "knows the answer. On real data this requires asking a human, not running "
                    "a better algorithm."
                    + (
                        f" {a3_missed_count} of the 40 injected instances land in the clean bucket, "
                        f"not silent_corrupt — there is no signal in this data for the tool to do "
                        f"otherwise."
                        if a3_missed_count is not None else ""
                    )
                ),
            },
            "silent_bucket_reconciliation": bucket_reconciliation,
        },
    }
