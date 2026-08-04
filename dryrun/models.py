from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Severity(str, Enum):
    LOUD = "loud"
    SILENT = "silent"


@dataclass
class Coercion:
    opportunity_id: str
    field: str
    raw_value: str
    parsed_value: object
    note: str


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    opportunity_id: str
    message: str
    broken_report: str | None = None


@dataclass
class UserRecord:
    user_id: str
    first_name: str
    last_name: str
    email: str
    is_active: bool


@dataclass
class HistoryRow:
    opportunity_id: str
    stage_name: str
    amount: Decimal | None
    probability: float | None
    close_date: date | None
    created_date: date | None  # when this transition was recorded


@dataclass
class NormalizedOpportunity:
    opportunity_id: str
    name: str
    account_id: str | None
    owner_id: str | None
    stage_raw: str
    amount: Decimal | None
    amount_is_text: bool
    currency: str | None
    close_date: date | None
    created_date: date | None
    last_modified_date: date | None
    closed_lost_reason: str | None
    is_closed: bool | None
    is_won: bool | None
    raw_row: dict = field(default_factory=dict)
