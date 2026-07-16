"""
Entity-name normalization for matching.

Distinct from `app/ingestion/normalizers.py` (which normalizes values on the
way *into* storage and deliberately preserves identity -- see its
`normalize_name` docstring). This module normalizes *for comparison only*:
it aggressively strips things that are noise to a matcher (legal suffixes,
punctuation, case, accents) but which must never be destroyed in the stored
record. Originals are always preserved -- these functions are pure and take
a string, returning a new one; nothing here mutates or writes.

Both are needed. Storing "AL-RASHID, Mohammad" as "al rashid mohammad" would
lose real information; comparing them without normalizing would miss the match.

Company suffixes are a curated, generic list of legal-form tokens (ltd, gmbh,
llc, ...). This is deliberately a *linguistic* list, not an entity list --
there is no company name in it, so it cannot encode a bias toward any
specific record in the dataset.
"""

from __future__ import annotations

import re
import unicodedata

# Legal-form tokens stripped before company-name comparison. Generic across
# jurisdictions; contains no entity names.
COMPANY_SUFFIXES: frozenset[str] = frozenset(
    {
        "ltd",
        "limited",
        "llc",
        "llp",
        "lp",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "gmbh",
        "ag",
        "sa",
        "sas",
        "sarl",
        "bv",
        "nv",
        "ab",
        "as",
        "oy",
        "aps",
        "spa",
        "srl",
        "pte",
        "pty",
        "pvt",
        "private",
        "holdings",
        "holding",
        "group",
        "international",
        "intl",
        "trading",
        "trust",
        "foundation",
        "fzco",
        "fze",
        "jsc",
        "ojsc",
        "zao",
        "ooo",
        "kg",
        "kft",
        "sp",
        "zoo",
        "doo",
        "ad",
        "dd",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_accents(value: str) -> str:
    """NFKD-decompose and drop combining marks, so 'Müller' == 'Muller' and
    'Ali Rezá' == 'Ali Reza' for matching purposes."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_matching(value: str | None) -> str:
    """Case-fold, strip accents/punctuation, collapse whitespace.
    The baseline every comparison starts from. Returns '' for empty input --
    callers check for emptiness rather than receiving None."""
    if not value:
        return ""
    text = strip_accents(str(value))
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip().lower()


def strip_company_suffixes(value: str | None) -> str:
    """Remove trailing/embedded legal-form tokens from an already
    match-normalized string. 'greenfield technologies pte ltd' ->
    'greenfield technologies'.

    If stripping would leave nothing (e.g. a company literally named
    'Holdings Ltd'), returns the un-stripped input instead -- an empty
    comparison key would match everything, which is far worse than a noisy one.
    """
    normalized = normalize_for_matching(value)
    if not normalized:
        return ""
    tokens = [t for t in normalized.split() if t not in COMPANY_SUFFIXES]
    if not tokens:
        return normalized
    return " ".join(tokens)


def normalize_person_name(value: str | None) -> str:
    """Person names: normalize and sort tokens so 'AL-RASHID, Mohammad' and
    'Mohammad Al-Rashid' produce the same key. Word order carries no reliable
    signal across the `LAST, First` (OFAC) vs `First Last` (OpenSanctions)
    conventions this project actually has to bridge -- see
    docs/data-dictionary.md."""
    normalized = normalize_for_matching(value)
    if not normalized:
        return ""
    return " ".join(sorted(normalized.split()))


def normalize_country(value: str | None) -> str:
    """Country comparison key. Reuses the ingestion-layer ISO-2 normalizer
    (single source of truth for the 'UK'->'GB' style fixes), then lowercases
    for comparison. Falls back to a plain match-normalized string for
    spelled-out names (OFAC addresses use 'United Kingdom', not 'GB')."""
    from app.ingestion.normalizers import normalize_country_code

    if not value:
        return ""
    raw = str(value).strip()
    if len(raw) <= 3:
        code = normalize_country_code(raw)
        return code.lower() if code else ""
    return normalize_for_matching(raw)


def normalize_identifier(value: str | None) -> str:
    """Registration/passport identifiers: strip everything non-alphanumeric
    and casefold, so 'HRB 145782', 'HRB-145782' and 'hrb145782' compare
    equal."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def normalize_entity_type(value: str | None) -> str | None:
    """Map the project's two incompatible entity-type vocabularies -- OFAC's
    (individual / vessel / aircraft / blank-means-entity) and OpenSanctions'
    (Person / Company / LegalEntity / Organization / Vessel / ...) -- onto a
    small shared vocabulary usable for *compatibility* checks only.

    Returns None for unknown/absent values, which the entity-type scorer
    treats as 'not applicable' rather than 'incompatible'. Deliberately does
    NOT rewrite the stored value (docs/data-dictionary.md explains why the
    raw vocabularies are preserved).
    """
    if not value:
        return None
    key = normalize_for_matching(value)
    if not key:
        return None

    person = {"individual", "person", "natural person"}
    organization = {
        "entity",
        "company",
        "legalentity",
        "legal entity",
        "organization",
        "organisation",
        "publicbody",
        "public body",
        "corporate",
        "ngo",
        "financial institution",
        "trust",
        "foundation",
    }
    vessel = {"vessel", "ship"}
    aircraft = {"aircraft", "airplane", "plane"}

    if key in person:
        return "person"
    if key in organization:
        return "organization"
    if key in vessel:
        return "vessel"
    if key in aircraft:
        return "aircraft"
    return None


def looks_like_person(entity_type: str | None) -> bool:
    return normalize_entity_type(entity_type) == "person"


def normalize_dob(value: str | None) -> str | None:
    """Return an ISO date string, or a bare 4-digit year when that's all the
    source gives (the UBO fixtures store `dob: "1975"`; OFAC Remarks give
    '15 Mar 1975'). Returning the year rather than None preserves the
    partial signal the DOB scorer needs -- see its year-only comparison path.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    if re.fullmatch(r"\d{4}", raw):
        return raw

    from app.ingestion.normalizers import normalize_datetime

    parsed = normalize_datetime(raw)
    if parsed is not None:
        return parsed.date().isoformat()

    year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", raw)
    if year_match:
        return year_match.group(1)
    return None


def dob_year(value: str | None) -> str | None:
    normalized = normalize_dob(value)
    if not normalized:
        return None
    return normalized[:4]
