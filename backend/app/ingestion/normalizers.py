"""
Reusable normalization utilities.

Every loader (app/ingestion/loaders/) and every provider that reads a local
file (app/providers/) should normalize raw field values through these
functions before they become a normalized model or DTO. This is what makes
"every future component consume one normalized model regardless of source"
(the Phase 2 objective) actually true: a name, country, date, or percentage
looks the same whether it came from a Phase 0 CSV, a curated JSON fixture, or
(in a future phase) a live API response.

These are pure functions -- no I/O, no database access -- so they're cheap to
unit test in isolation (see tests/test_normalizers.py) and safe to call from
both a loader and a provider without any shared state.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

# A conservative allow-list of 2-letter codes actually observed in the Phase 0
# datasets (docs/data-dictionary.md) plus common others. Anything not
# recognized is passed through uppercased rather than dropped -- normalization
# must never silently discard a real value it doesn't recognize.
_KNOWN_ISO2 = {
    "AE",
    "AF",
    "AU",
    "CA",
    "CH",
    "CN",
    "CY",
    "DE",
    "DK",
    "FR",
    "HK",
    "IN",
    "IR",
    "JP",
    "KP",
    "KY",
    "LB",
    "NL",
    "QA",
    "RU",
    "SD",
    "SG",
    "SY",
    "UK",
    "US",
    "VE",
    "VG",
    "VN",
    "GB",
}


def normalize_country_code(raw: str | None) -> str | None:
    """Uppercase, strip, and map a few common non-ISO2 spellings ('UK') to
    their standard form where unambiguous. Returns None for empty input --
    never fabricates a country."""
    if raw is None:
        return None
    value = raw.strip().upper()
    if not value or value in ("-0-", "N/A", "NONE", "NULL"):
        return None
    if value == "UK":
        return "GB"
    return value


def normalize_currency_code(raw: str | None) -> str | None:
    """Best-effort mapping from a free-text currency description (SAML-D
    style, e.g. 'UK pounds') to a 3-letter ISO 4217-ish code. Falls back to
    the cleaned original string if no mapping is known -- never invents a
    currency that wasn't stated."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    known = {
        "uk pounds": "GBP",
        "us dollar": "USD",
        "dollar": "USD",
        "euro": "EUR",
        "indian rupee": "INR",
        "yen": "JPY",
        "yuan": "CNY",
        "swiss franc": "CHF",
        "canadian dollar": "CAD",
        "australian dollar": "AUD",
        "mexican peso": "MXN",
        "naira": "NGN",
        "rand": "ZAR",
        "brazil real": "BRL",
        "albanian lek": "ALL",
    }
    lowered = value.lower()
    if lowered in known:
        return known[lowered]
    if re.fullmatch(r"[A-Za-z]{3}", value):
        return value.upper()
    return value


def normalize_name(raw: str | None) -> str | None:
    """Collapse internal whitespace and strip leading/trailing whitespace.
    Deliberately does NOT change case or punctuation -- entity names carry
    meaningful casing (e.g. 'AL-RASHID, Mohammad' vs a normalized-for-display
    form) that fuzzy matching in app/providers/ already handles on its own
    terms. This function only fixes formatting noise, never rewrites
    identity."""
    if raw is None:
        return None
    value = re.sub(r"\s+", " ", raw).strip()
    if not value or value in ("-0-",):
        return None
    return value


def normalize_entity_type(raw: str | None) -> str | None:
    """Lowercases and strips a free-text entity type. Deliberately does NOT
    map across vocabularies (OFAC's individual/vessel/aircraft vs.
    OpenSanctions' Person/Company/LegalEntity/...) -- see
    docs/data-dictionary.md for why forcing a shared enum here would lose
    information. This only removes formatting noise."""
    if raw is None:
        return None
    value = raw.strip()
    if not value or value in ("-0-",):
        return None
    return value


def normalize_transaction_direction(
    sender: str | None, receiver: str | None, subject_account: str | None
) -> str:
    """Given a transaction's sender/receiver account and the account the
    Customer 360 view is being built for, returns 'OUTBOUND', 'INBOUND', or
    'UNKNOWN' (never guesses when the subject account doesn't match either
    side)."""
    if subject_account is None:
        return "UNKNOWN"
    if sender is not None and str(sender) == str(subject_account):
        return "OUTBOUND"
    if receiver is not None and str(receiver) == str(subject_account):
        return "INBOUND"
    return "UNKNOWN"


def normalize_percentage(raw: float | int | str | None) -> float | None:
    """Clamps to [0, 100] and returns None for missing/unparseable input --
    never defaults to 0 or 100, since either would fabricate a fact."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, value))


def normalize_datetime(raw: str | datetime | date | None) -> datetime | None:
    """Parses a variety of the date/datetime string formats observed across
    Phase 0 sources into a timezone-aware UTC datetime. Returns None rather
    than raising on an unparseable value -- callers decide whether that's a
    validation error."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)

    value = str(raw).strip()
    if not value or value in ("-0-", "nan", "NaT"):
        return None

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%d %b %Y",
        "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_bool_flag(raw) -> bool:
    """Every Phase 0 *_flag column is 0/1 (int). This normalizes that plus
    common alternate truthy/falsy spellings defensively, defaulting to False
    only for genuinely empty/unrecognized input."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    value = str(raw).strip().lower()
    return value in ("1", "true", "yes", "y")


_DOB_PATTERN = re.compile(r"DOB\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")


def extract_dob_from_remarks(remarks: str | None) -> date | None:
    """OFAC's free-text Remarks field sometimes embeds a DOB, e.g.
    'DOB 15 Mar 1975; nationality UAE; a.k.a. ...'. This is a generic regex
    extraction, not a lookup keyed to any specific entity -- it runs
    identically over any Remarks string. Returns None if no DOB pattern is
    present (most Tier-1 OFAC rows have empty Remarks -- see
    docs/data-dictionary.md)."""
    if not remarks:
        return None
    match = _DOB_PATTERN.search(remarks)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %b %Y").date()
    except ValueError:
        return None


def build_provenance(*, source_dataset: str, source_tier, source_type) -> dict:
    """Small helper so every loader constructs its ProvenanceMixin fields the
    same way, with ingested_at stamped at call time."""
    return {
        "source_dataset": source_dataset,
        "source_tier": source_tier,
        "source_type": source_type,
        "ingested_at": datetime.now(timezone.utc),
    }
