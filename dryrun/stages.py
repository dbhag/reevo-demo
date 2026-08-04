from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum

from .config import PipelineConfig

# Above this, auto-map despite not being exact/alias. Below it but with a
# real candidate, flag for human review. Below FUZZY_FLAG_FLOOR, no
# candidate resembles the input closely enough to guess at all -> blocked.
FUZZY_MAP_THRESHOLD = 0.90
FUZZY_FLAG_FLOOR = 0.30
FUZZY_CANDIDATES = 3

# Small, documented domain lexicon: common alternate phrasings per target
# stage. Augments raw character similarity so semantically-adjacent CRM
# jargon (not just character-similar strings) gets a fair candidate score.
# This is what lets "Verbal Commit" surface Negotiation as a real candidate
# instead of nothing — plain character similarity alone scores that pair at
# 0.25 (see plain_similarity() below), indistinguishable from noise.
STAGE_SYNONYMS: dict[str, list[str]] = {
    "Negotiation": ["Verbal Commitment", "Committed Verbally", "Final Negotiation"],
    "Proposal": ["Quote Sent", "Proposal Sent"],
    "Qualification": ["Qualifying", "Discovery"],
    "Prospecting": ["New Lead", "Cold Lead"],
    "Closed Won": ["Won"],
    "Closed Lost": ["Lost", "No Decision"],
}


class StageVerdict(str, Enum):
    MAPPED = "mapped"
    FLAGGED = "flagged"
    BLOCKED = "blocked"


@dataclass
class StageResolution:
    legacy_stage: str
    verdict: StageVerdict
    target_stage: str | None
    confidence: float
    method: str  # "exact" | "normalized" | "alias" | "fuzzy"
    candidates: list[tuple[str, float]] = field(default_factory=list)
    reason: str | None = None


def _normalize(s: str) -> str:
    return s.strip().lower()


def _fuzzy_score(raw_norm: str, target: str) -> float:
    candidates = [target] + STAGE_SYNONYMS.get(target, [])
    return max(difflib.SequenceMatcher(None, raw_norm, _normalize(c)).ratio() for c in candidates)


def plain_similarity(a: str, b: str) -> float:
    """Character similarity with no synonym lexicon involved — the baseline
    the lexicon is measured against in 'where this is wrong'. Single source
    of truth so this number can't drift between the report and the
    dashboard (it did once: a hardcoded 0.31 disagreed with this)."""
    return round(difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio(), 3)


def resolve_stage_name(
    legacy_stage: str, config: PipelineConfig, fuzzy_map_threshold: float = FUZZY_MAP_THRESHOLD
) -> StageResolution:
    """fuzzy_map_threshold defaults to the shipped FUZZY_MAP_THRESHOLD (0.90).
    The parameter exists only for the threshold-sensitivity analysis in
    report_data.py — it does not change the threshold audit.py/gates.py/the
    dashboard actually use."""
    targets = config.stage_names()

    if legacy_stage in targets:
        return StageResolution(legacy_stage, StageVerdict.MAPPED, legacy_stage, 1.0, "exact")

    norm = _normalize(legacy_stage)
    norm_targets = {_normalize(t): t for t in targets}
    if norm in norm_targets:
        return StageResolution(legacy_stage, StageVerdict.MAPPED, norm_targets[norm], 1.0, "normalized")

    norm_aliases = {_normalize(k): v for k, v in config.stage_aliases.items()}
    if norm in norm_aliases:
        return StageResolution(legacy_stage, StageVerdict.MAPPED, norm_aliases[norm], 1.0, "alias")

    scored = sorted(((t, _fuzzy_score(norm, t)) for t in targets), key=lambda x: -x[1])
    best_stage, best_score = scored[0]
    candidates = scored[:FUZZY_CANDIDATES]

    if best_score >= fuzzy_map_threshold:
        return StageResolution(legacy_stage, StageVerdict.MAPPED, best_stage, best_score, "fuzzy", candidates)

    if best_score >= FUZZY_FLAG_FLOOR:
        reason = (
            f"Best fuzzy match {best_stage!r} at {best_score:.2f} confidence is below "
            f"the {fuzzy_map_threshold:.2f} auto-map threshold."
        )
        return StageResolution(legacy_stage, StageVerdict.FLAGGED, None, best_score, "fuzzy", candidates, reason)

    reason = "No target stage resembles this legacy value closely enough to guess."
    return StageResolution(legacy_stage, StageVerdict.BLOCKED, None, best_score, "fuzzy", candidates, reason)


def resolve_stages(
    legacy_stage_values: list[str], config: PipelineConfig, fuzzy_map_threshold: float = FUZZY_MAP_THRESHOLD
) -> dict[str, StageResolution]:
    """Resolve each distinct legacy stage string once — resolution is a
    property of the string, not the row, and the report groups by decision
    (distinct legacy stage) rather than by opportunity."""
    cache: dict[str, StageResolution] = {}
    for s in legacy_stage_values:
        if s not in cache:
            cache[s] = resolve_stage_name(s, config, fuzzy_map_threshold)
    return cache
