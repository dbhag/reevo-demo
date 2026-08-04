from __future__ import annotations

import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .models import Coercion, HistoryRow, NormalizedOpportunity, UserRecord

CLEAN_AMOUNT_RE = re.compile(r"^-?\d+(\.\d+)?$")
AMOUNT_STRIP = re.compile(r"[^0-9.\-]")

TRUE_STRINGS = {"true", "1", "yes", "y"}
FALSE_STRINGS = {"false", "0", "no", "n"}


def _find_col(headers: list[str], keys: list[str]) -> str | None:
    lower = {h.lower(): h for h in headers}
    for k in keys:
        if k in lower:
            return lower[k]
    return None


def _parse_date(raw: str, field_name: str, opp_id: str, coercions: list[Coercion]):
    if not raw:
        return None

    if "-" in raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass

    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 3:
            try:
                first, second = int(parts[0]), int(parts[1])
            except ValueError:
                first = second = None

            if first is not None:
                if first > 12:
                    try:
                        d = datetime.strptime(raw, "%d/%m/%Y").date()
                        coercions.append(Coercion(opp_id, field_name, raw, d, "DD/MM inferred (day field > 12)"))
                        return d
                    except ValueError:
                        pass
                elif second > 12:
                    try:
                        d = datetime.strptime(raw, "%m/%d/%Y").date()
                        coercions.append(Coercion(opp_id, field_name, raw, d, "MM/DD inferred (second field > 12)"))
                        return d
                    except ValueError:
                        pass
                else:
                    # Both <= 12: genuinely ambiguous. Assume MM/DD (US export
                    # convention) but flag it — never silently correct.
                    try:
                        d = datetime.strptime(raw, "%m/%d/%Y").date()
                        coercions.append(
                            Coercion(opp_id, field_name, raw, d, "AMBIGUOUS MM/DD vs DD/MM, assumed MM/DD")
                        )
                        return d
                    except ValueError:
                        pass

    coercions.append(Coercion(opp_id, field_name, raw, None, "unparseable date"))
    return None


def _parse_amount(raw: str, opp_id: str, coercions: list[Coercion]) -> tuple[Decimal | None, bool]:
    """Returns (value, amount_is_text). amount_is_text=True means the raw
    string wasn't a clean decimal literal — a real numeric-typed import
    column would reject it. Still best-effort parsed for internal math."""
    if not raw:
        return None, False

    if CLEAN_AMOUNT_RE.match(raw):
        return Decimal(raw), False

    cleaned = AMOUNT_STRIP.sub("", raw)
    coercions.append(Coercion(opp_id, "amount", raw, cleaned, "amount not a clean numeric literal"))
    try:
        return Decimal(cleaned), True
    except InvalidOperation:
        coercions.append(Coercion(opp_id, "amount", raw, None, "unparseable amount"))
        return None, True


def _parse_bool(raw: str) -> bool | None:
    if not raw:
        return None
    low = raw.strip().lower()
    if low in TRUE_STRINGS:
        return True
    if low in FALSE_STRINGS:
        return False
    return None


OPP_FIELD_ALIASES = {
    "opportunity_id": ["id", "opportunityid", "opportunity_id"],
    "name": ["name", "opportunityname"],
    "account_id": ["accountid", "account_id"],
    "owner_id": ["ownerid", "owner_id", "owner"],
    "stage_raw": ["stagename", "stage"],
    "amount": ["amount"],
    "currency": ["currencyisocode", "currency"],
    "close_date": ["closedate", "close_date"],
    "created_date": ["createddate", "created_date"],
    "last_modified_date": ["lastmodifieddate", "last_modified_date"],
    "closed_lost_reason": [
        "closedlostreason",
        "closed_lost_reason",
        "primaryclosedlostreason",
        "primary_closed_lost_reason",
    ],
    "is_closed": ["isclosed", "is_closed"],
    "is_won": ["iswon", "is_won"],
}


def parse_opportunities(path: str) -> tuple[list[NormalizedOpportunity], list[Coercion]]:
    coercions: list[Coercion] = []
    opps: list[NormalizedOpportunity] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        cols = {name: _find_col(headers, aliases) for name, aliases in OPP_FIELD_ALIASES.items()}

        for row in reader:
            def get(name: str) -> str:
                col = cols[name]
                if not col:
                    return ""
                val = row.get(col) or ""
                # stage_raw is left un-stripped on purpose: leading/trailing
                # whitespace on a picklist value is itself a defect (A5) that
                # an exact-match importer rejects. Stripping it here would
                # silently fix the exact thing this tool exists to catch.
                return val if name == "stage_raw" else val.strip()

            opp_id = get("opportunity_id")
            amount, amount_is_text = _parse_amount(get("amount"), opp_id, coercions)

            opps.append(
                NormalizedOpportunity(
                    opportunity_id=opp_id,
                    name=get("name"),
                    account_id=get("account_id") or None,
                    owner_id=get("owner_id") or None,
                    stage_raw=get("stage_raw"),
                    amount=amount,
                    amount_is_text=amount_is_text,
                    currency=get("currency") or None,
                    close_date=_parse_date(get("close_date"), "close_date", opp_id, coercions),
                    created_date=_parse_date(get("created_date"), "created_date", opp_id, coercions),
                    last_modified_date=_parse_date(
                        get("last_modified_date"), "last_modified_date", opp_id, coercions
                    ),
                    closed_lost_reason=get("closed_lost_reason") or None,
                    is_closed=_parse_bool(get("is_closed")),
                    is_won=_parse_bool(get("is_won")),
                    raw_row=row,
                )
            )

    return opps, coercions


def parse_history(path: str) -> dict[str, list[HistoryRow]]:
    by_opp: dict[str, list[HistoryRow]] = {}
    coercions: list[Coercion] = []  # history-row date issues aren't reported, just parsed leniently

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        cols = {
            "opportunity_id": _find_col(headers, ["opportunityid", "opportunity_id"]),
            "stage_name": _find_col(headers, ["stagename", "stage"]),
            "amount": _find_col(headers, ["amount"]),
            "probability": _find_col(headers, ["probability"]),
            "close_date": _find_col(headers, ["closedate", "close_date"]),
            "created_date": _find_col(headers, ["createddate", "created_date"]),
        }

        for row in reader:
            def get(name: str) -> str:
                col = cols[name]
                return (row.get(col) or "").strip() if col else ""

            oid = get("opportunity_id")
            amount, _ = _parse_amount(get("amount"), oid, coercions)
            prob_raw = get("probability")
            history_row = HistoryRow(
                opportunity_id=oid,
                stage_name=get("stage_name"),
                amount=amount,
                probability=float(prob_raw) if prob_raw else None,
                close_date=_parse_date(get("close_date"), "history_close_date", oid, coercions),
                created_date=_parse_date(get("created_date"), "history_created_date", oid, coercions),
            )
            by_opp.setdefault(oid, []).append(history_row)

    return by_opp


def parse_users(path: str) -> dict[str, UserRecord]:
    users: dict[str, UserRecord] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        cols = {
            "user_id": _find_col(headers, ["userid", "user_id", "id"]),
            "first_name": _find_col(headers, ["firstname", "first_name"]),
            "last_name": _find_col(headers, ["lastname", "last_name"]),
            "email": _find_col(headers, ["email"]),
            "is_active": _find_col(headers, ["isactive", "is_active"]),
        }
        for row in reader:
            def get(name: str) -> str:
                col = cols[name]
                return (row.get(col) or "").strip() if col else ""

            uid = get("user_id")
            users[uid] = UserRecord(
                user_id=uid,
                first_name=get("first_name"),
                last_name=get("last_name"),
                email=get("email"),
                is_active=bool(_parse_bool(get("is_active"))),
            )
    return users
