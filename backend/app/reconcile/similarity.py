"""Small text-similarity helpers shared by duplicate detection, matching and merging."""

import re

from rapidfuzz import fuzz

# Corporate suffixes carry no identifying signal ("Meridian Capital" is identified by "meridian").
GENERIC_COMPANY_WORDS = {
    "capital", "group", "partners", "holdings", "investments", "investors", "advisors",
    "ventures", "wealth", "institutional", "management", "llc", "inc", "ltd", "the", "and", "of",
}

_LOCATION_PREFIXES = re.compile(r"^(hq|headquarters)\s*[-–:]\s*")


def tokens(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def company_tokens(company: str | None) -> set[str]:
    return {t for t in tokens(company) if t not in GENERIC_COMPANY_WORDS and len(t) >= 3}


def title_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a.lower(), b.lower()) / 100


def name_localparts(full_name: str | None) -> set[str]:
    """Likely email local-parts for a person: 'Kevin O'Brien' -> {'kevin.obrien', 'kevinobrien', 'kobrien'}."""
    parts = ["".join(re.findall(r"[a-z]", p)) for p in (full_name or "").lower().split()]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return set()
    first, last = parts[0], parts[-1]
    return {f"{first}.{last}", f"{first}{last}", f"{first[0]}{last}", f"{first[0]}.{last}"}


def last_name(full_name: str | None) -> str | None:
    parts = ["".join(re.findall(r"[a-z]", p)) for p in (full_name or "").lower().split()]
    return parts[-1] if len(parts) >= 2 and parts[-1] else None


def localpart(email: str) -> str:
    return email.split("@", 1)[0]


def normalize_location(location: str | None) -> str:
    return " ".join(tokens(_LOCATION_PREFIXES.sub("", (location or "").lower().strip())))


def location_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(normalize_location(a), normalize_location(b)) / 100


def is_refinement(general: str | None, specific: str | None) -> bool:
    """True when `specific` extends `general` ('Boston Office' -> 'Boston Office - Room 301')."""
    g, s = normalize_location(general), normalize_location(specific)
    return bool(g) and g != s and s.startswith(g)
