from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass
class Stage:
    name: str
    probability: float
    required_fields: list[str]


@dataclass
class PipelineConfig:
    stages: list[Stage]
    picklists: dict[str, list[str]]
    stage_aliases: dict[str, str]
    multi_currency: bool
    seat_count: int | None

    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]


REQUIRED_KEYS = ["stages", "picklists"]


def load_config(path: str) -> PipelineConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"Config {path!r} missing required keys: {missing}")

    stages = [
        Stage(
            name=s["name"],
            probability=float(s["probability"]),
            required_fields=list(s.get("required_fields", [])),
        )
        for s in raw["stages"]
    ]

    seat_count = raw.get("seat_count")

    return PipelineConfig(
        stages=stages,
        picklists={k: list(v) for k, v in raw.get("picklists", {}).items()},
        stage_aliases=dict(raw.get("stage_aliases", {})),
        multi_currency=bool(raw.get("multi_currency", False)),
        seat_count=int(seat_count) if seat_count is not None else None,
    )
