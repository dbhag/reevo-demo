from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .config import PipelineConfig
from .models import Coercion, Finding, NormalizedOpportunity, Severity
from .stages import FUZZY_MAP_THRESHOLD, StageResolution, StageVerdict, plain_similarity, resolve_stages

# Legacy-stage corruption classes that, by construction, have no correct
# target stage. Any auto-map of a stage string in one of these classes is
# wrong by definition — the manifest doesn't need to name "the right
# answer" for us to know there isn't one. See generate_dataset.py A1/A2/A4.
NO_CORRECT_TARGET_CLASSES = {"a1_unresolved", "a2_near_miss", "a4_deprecated_renamed"}

SENSITIVITY_THRESHOLDS = (0.90, 0.85, 0.80)


@dataclass
class PipelineBreakdown:
    open_total: Decimal          # face value, all open opps with an amount
    weighted_total: Decimal      # amount x probability, MAPPED opps only
    weighted_count: int
    mispriced_amount: Decimal    # amount x probability, but resolved via the fuzzy tier (uncertain match)
    mispriced_count: int
    withheld_amount: Decimal     # face value, flagged/blocked — not weighted, not summed into any forecast
    withheld_count: int


def _pipeline_breakdown(
    opps: list[NormalizedOpportunity], resolutions: dict[str, StageResolution], config: PipelineConfig
) -> PipelineBreakdown:
    """Two separate dollar buckets, not one blended gap:

    - mispriced: dollars weighted using a stage resolved via the fuzzy tier
      (method == 'fuzzy' and verdict == MAPPED). These carry real risk — a
      fuzzy match, even above the auto-map threshold, is a probability
      applied with less than full confidence. This is the only channel
      through which a resolved-to-the-wrong-stage dollar figure can exist in
      this build. It is not a synonym for the whole gap.
    - withheld: face-value dollars for flagged/blocked opps. These are
      excluded from the forecast, not mispriced — no stage was confident
      enough to weight them at all, so no probability was applied, correctly.

    A naive unweighted face-value baseline is deliberately NOT compared
    against the weighted total here: that comparison mixes "probability
    weighting exists" (true even with zero data defects) with actual data
    problems, and doesn't decompose into an actionable number. See README.
    """
    prob_by_stage = {s.name: s.probability for s in config.stages}
    open_total = Decimal(0)
    weighted_total = Decimal(0)
    weighted_count = 0
    mispriced_amount = Decimal(0)
    mispriced_count = 0
    withheld_amount = Decimal(0)
    withheld_count = 0

    for o in opps:
        if o.is_closed or o.amount is None:
            continue
        open_total += o.amount
        res = resolutions.get(o.stage_raw)
        if res and res.verdict == StageVerdict.MAPPED and res.target_stage in prob_by_stage:
            weighted = o.amount * Decimal(str(prob_by_stage[res.target_stage]))
            weighted_total += weighted
            weighted_count += 1
            if res.method == "fuzzy":
                mispriced_amount += weighted
                mispriced_count += 1
        else:
            withheld_amount += o.amount
            withheld_count += 1

    return PipelineBreakdown(
        open_total, weighted_total, weighted_count,
        mispriced_amount, mispriced_count, withheld_amount, withheld_count,
    )


def threshold_sensitivity(
    opps: list[NormalizedOpportunity], config: PipelineConfig, manifest_path: str | None
) -> list[dict] | None:
    """Re-runs stage resolution at 0.90 (shipped), 0.85, and 0.80 to measure
    what lowering the auto-map threshold would cost — without changing it.
    Only meaningful against ground truth, so gated on the manifest like the
    other calibration sections; a real customer run has no manifest and this
    returns None."""
    if not manifest_path:
        return None

    manifest_classes: dict[str, list[str]] = defaultdict(list)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                manifest_classes[row["OpportunityId"]].append(row["CorruptionClass"])
    except FileNotFoundError:
        return None

    # legacy stage string -> its corruption class. 1:1 by construction (see
    # generate_dataset.py — each problem stage string belongs to exactly one class).
    stage_class: dict[str, str] = {}
    for o in opps:
        classes = manifest_classes.get(o.opportunity_id)
        if classes:
            stage_class[o.stage_raw] = classes[0]

    stage_values = [o.stage_raw for o in opps]
    stage_counts = Counter(stage_values)
    prob_by_stage = {s.name: s.probability for s in config.stages}

    rows = []
    for threshold in SENSITIVITY_THRESHOLDS:
        res_at_t = resolve_stages(stage_values, config, fuzzy_map_threshold=threshold)
        auto_mapped = [
            (s, r) for s, r in res_at_t.items() if r.verdict == StageVerdict.MAPPED and r.method == "fuzzy"
        ]
        wrong = [(s, r) for s, r in auto_mapped if stage_class.get(s) in NO_CORRECT_TARGET_CLASSES]

        mispriced = Decimal(0)
        for stage, res in wrong:
            prob = prob_by_stage.get(res.target_stage)
            if prob is None:
                continue
            for o in opps:
                if o.stage_raw == stage and not o.is_closed and o.amount is not None:
                    mispriced += o.amount * Decimal(str(prob))

        rows.append({
            "threshold": threshold,
            "auto_mapped_stage_strings": [s for s, _ in auto_mapped],
            "auto_mapped_opportunity_count": sum(stage_counts[s] for s, _ in auto_mapped),
            "wrong_stage_strings": [s for s, _ in wrong],
            "wrong_opportunity_count": sum(stage_counts[s] for s, _ in wrong),
            "mispriced_amount": mispriced,
        })

    return rows


def render_report(
    opps: list[NormalizedOpportunity],
    coercions: list[Coercion],
    findings: list[Finding],
    resolutions: dict[str, StageResolution],
    config: PipelineConfig,
    source_path: str,
    manifest_path: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Import Dry Run — {source_path}\n")
    lines.append(f"Records parsed: {len(opps)}\n")

    silent = [f for f in findings if f.severity == Severity.SILENT]
    loud = [f for f in findings if f.severity == Severity.LOUD]

    verdict_counts = Counter(resolutions[o.stage_raw].verdict for o in opps)
    mapped = verdict_counts.get(StageVerdict.MAPPED, 0)
    flagged = verdict_counts.get(StageVerdict.FLAGGED, 0)
    blocked = verdict_counts.get(StageVerdict.BLOCKED, 0)

    lines.append("## 1. Counts and verdicts\n")
    lines.append(f"- Mapped: {mapped}\n")
    lines.append(f"- Flagged (needs a human stage-mapping decision): {flagged}\n")
    lines.append(f"- Blocked (no usable stage candidate): {blocked}\n")
    lines.append(f"- Coercions logged during parse: {len(coercions)}\n")
    lines.append(f"- Silent-corruption findings: {len(silent)}\n")
    lines.append(f"- Loud-failure findings: {len(loud)}\n")

    lines.append("## 2. Silent corruption\n")
    lines.append("Records that import clean and corrupt a report, invisibly.\n")
    silent_by_rule = Counter(f.rule_id for f in silent)
    if not silent:
        lines.append("None found.\n")
    for rule_id, count in silent_by_rule.most_common():
        example = next(f for f in silent if f.rule_id == rule_id)
        noun = "opportunity" if count == 1 else "opportunities"
        lines.append(
            f"- **{count} {noun}** — {example.message} "
            f"(breaks: {example.broken_report or 'unspecified report'})\n"
        )

    lines.append("## 3. Loud failures (would have been caught)\n")
    lines.append("A naive importer errors on these too. Not the point of this tool — shown for contrast.\n")
    loud_by_rule = Counter(f.rule_id for f in loud)
    if not loud:
        lines.append("None found.\n")
    for rule_id, count in loud_by_rule.most_common():
        example = next(f for f in loud if f.rule_id == rule_id)
        noun = "opportunity" if count == 1 else "opportunities"
        lines.append(f"- **{count} {noun}** — {example.message}\n")

    lines.append("## 4. Human decision list\n")
    lines.append("Grouped by decision — one legacy stage string, not one row per opportunity.\n")
    stage_counts = Counter(o.stage_raw for o in opps)
    needs_decision = [
        (stage, res)
        for stage, res in resolutions.items()
        if res.verdict in (StageVerdict.FLAGGED, StageVerdict.BLOCKED)
    ]
    needs_decision.sort(key=lambda sr: -stage_counts[sr[0]])
    if not needs_decision:
        lines.append("None — every legacy stage value resolved cleanly.\n")
    for stage, res in needs_decision:
        n = stage_counts[stage]
        noun = "opportunity" if n == 1 else "opportunities"
        if res.verdict == StageVerdict.FLAGGED:
            cand_str = ", ".join(f"{c!r} ({score:.2f})" for c, score in res.candidates)
            lines.append(
                f"- **{stage!r}** ({n} {noun}, FLAGGED) — {res.reason} Candidates: {cand_str}.\n"
            )
        else:
            cand_str = ", ".join(f"{c!r} ({score:.2f})" for c, score in res.candidates)
            lines.append(
                f"- **{stage!r}** ({n} {noun}, BLOCKED) — {res.reason} Closest (still too weak): {cand_str}.\n"
            )

    pb = _pipeline_breakdown(opps, resolutions, config)
    if pb.open_total > 0:
        lines.append(
            "\n**Pipeline value impact — illustrative, not a forecast.** These dollar "
            "figures come from this build's synthetic, seeded dataset (see DATASET.md); "
            "the corruption rate was chosen by the generator, not observed in real data, "
            "so the magnitude below is a demonstration of the mechanism, not a claim "
            "about what any real pipeline is worth. Do not read the numbers as a forecast.\n"
        )
        lines.append(f"- Open pipeline, face value: ${pb.open_total:,.0f} ({pb.weighted_count + pb.withheld_count} opps).\n")
        lines.append(
            f"- Withheld pending the {len(needs_decision)} decisions above: "
            f"${pb.withheld_amount:,.0f} ({pb.withheld_count} opps). Not weighted at any "
            f"probability — no stage was confident enough to apply one. This is exclusion, "
            f"not mispricing.\n"
        )
        if pb.mispriced_count:
            lines.append(
                f"- Weighted using an uncertain (fuzzy-tier) stage match: ${pb.mispriced_amount:,.0f} "
                f"({pb.mispriced_count} opps). This is the actual mispricing-risk dollar figure — "
                f"probability applied with less than full confidence in the stage it was applied to.\n"
            )
        else:
            lines.append(
                "- Mispriced (fuzzy-tier auto-map): **$0 (0 opps)** — at threshold 0.90, on this "
                "dataset, no record auto-mapped through the fuzzy tier, so there is no wrong-stage "
                "dollar exposure to report (see the threshold-sensitivity table below).\n"
            )
        lines.append(
            f"- Weighted at configured probability, confidently-resolved stages: "
            f"${pb.weighted_total:,.0f} ({pb.weighted_count} opps).\n"
        )

    vc_plain = plain_similarity("Verbal Commit", "Negotiation")
    vc_lexicon = resolutions["Verbal Commit"].confidence if "Verbal Commit" in resolutions else None
    pilot_plain = plain_similarity("Pilot", "Closed Lost")
    pilot_lexicon = resolutions["Pilot"].confidence if "Pilot" in resolutions else None

    lines.append("\n## 5. Where this is wrong\n")
    lines.append(
        "- **The hand-curated synonym lexicon (dryrun/stages.py) has a measured cost, "
        "not just a measured benefit — both from the same mechanism, same run:**\n"
    )
    lines.append(
        f"  - Verbal Commit → Negotiation: plain string similarity {vc_plain:.2f}, with lexicon "
        f"{vc_lexicon:.2f}. **Fixed** — the intended catch, this is why the lexicon exists.\n"
    )
    lines.append(
        f"  - Pilot → Closed Lost: plain string similarity {pilot_plain:.2f}, with lexicon "
        f"{pilot_lexicon:.2f}. **Still wrong, now more confident** — plain similarity already "
        f"ranked Closed Lost top for Pilot before any synonym was involved; the lexicon didn't "
        f"invent this error, it amplified it.\n"
    )
    lines.append(
        "  Neither threshold nor lexicon was retuned after finding this. Hand-curated "
        "means it only helps for phrasing the author anticipated — and can hurt for "
        "phrasing the author didn't.\n"
    )

    sensitivity = threshold_sensitivity(opps, config, manifest_path)
    if sensitivity:
        lines.append(
            "\n  **Threshold sensitivity** — re-running resolution at lower thresholds, "
            "shipped threshold unchanged at 0.90:\n\n"
        )
        lines.append("  | Threshold | Auto-mapped (fuzzy) | Wrong, per manifest | Mispriced |\n")
        lines.append("  |---|---|---|---|\n")
        for row in sensitivity:
            wrong_note = f"{row['wrong_stage_strings'][0]!r}" if row["wrong_stage_strings"] else "—"
            lines.append(
                f"  | {row['threshold']:.2f} | {row['auto_mapped_opportunity_count']} opps "
                f"({', '.join(row['auto_mapped_stage_strings']) or '—'}) | "
                f"{row['wrong_opportunity_count']} opps ({wrong_note}) | "
                f"${row['mispriced_amount']:,.0f} |\n"
            )
        lines.append(
            "\n  0.90 stays the shipped threshold: at 0.85, the only stage that crosses is "
            "'Verbal Commit' itself — the canonical near-miss this tool exists to catch — "
            "which would move it from a human decision to a silent wrong answer. Nothing "
            "else crosses until well below 0.80.\n"
        )
    lines.append(
        "- Two of the three configured stage aliases ('Closed - Won', 'Closed - Lost') "
        "never appear in this dataset — only 'Qualified' exercises the alias tier. The "
        "fuzzy-tier auto-map path (confidence at/above the 0.90 threshold) never fires "
        "either; every fuzzy-tier resolution in this run lands below threshold. Both are "
        "real code paths that are currently unverified by any test data, not confirmed "
        "correct.\n"
    )
    lines.append(
        "- Gate validation (dryrun/gates.py) has 5 checks, one required field each. Only "
        "'account_id' has ever fired (20/20 against the manifest). 'owner' cannot fire "
        "regardless of data — its dedup guard against the existing owner_blank rule checks "
        "the identical condition that triggers it, so the two conditions can never disagree. "
        "'amount', 'close_date', and 'closed_lost_reason' are reachable in principle but "
        "untested — this dataset never produces the specific gap (a blank required field on "
        "an open, not-closed stage) that would exercise them.\n"
    )
    lines.append(
        "- One rule check is also fuzzy: possible-duplicate-deal (F3) matches on name "
        "similarity, account, and date proximity. In this build's own synthetic "
        "calibration run (see DATASET.md, which ships a ground-truth manifest), it "
        "caught every injected duplicate at ~13% precision — about 7 flagged rows per "
        "real duplicate. That number is specific to this synthetic run; a real export "
        "has no ground truth to check against, so its true precision here is unknown "
        "until run. The review-burden trade-off is real regardless of the exact number.\n"
    )
    lines.append(
        "- Cannot catch: a legacy stage that maps cleanly to a target stage but "
        "*means* something different at this customer (e.g. two sales orgs both using "
        "'Qualified' with different entry criteria). Not detectable from data — the "
        "record looks identical to a correct mapping. Not solved, not solvable from "
        "this input alone. Say so, don't pretend a config alias table catches it.\n"
    )
    lines.append(
        "- The account-reference check is blank/absent only. No accounts file is in "
        "scope (see CLAUDE.md), so a present-but-wrong account ID cannot be verified "
        "against a real account roster.\n"
    )
    lines.append(
        "- **DATA gate**: this run was executed against a synthetic, seeded, "
        "documented dataset (see DATASET.md) — not a real or harvested export. "
        "Every corruption class is sourced to a documented real-world migration "
        "failure, which makes it defensible, not real. No dollar figure from this "
        "run should be presented as a finding; treat counts as illustrative until a "
        "real export is run through this tool.\n"
    )

    return "\n".join(lines)
