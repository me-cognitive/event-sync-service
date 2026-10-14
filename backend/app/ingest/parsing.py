"""Tolerant parsers for the messy values found in the upstream feeds.

Each parser returns the cleaned value plus enough information for the caller
to record a DataIssue; none of them raise on bad input.
"""

import re
from datetime import date, datetime, time, tzinfo

from app.models.normalized import LocationKind

PLACEHOLDER_VALUES = {"multiple", "various", "tbd", "n/a", "na", "unknown", "none", "-"}

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
_STRICT_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})?$")
_MOJIBAKE_MARKERS = ("â€", "Ã", "Â")
_VIRTUAL_KEYWORDS = ("zoom", "teams", "google meet", "meet.google", "webex", "virtual", "http", "dial-in")


def clean_str(value: object) -> str | None:
    """Strip strings and collapse empty values to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_placeholder(value: str | None) -> bool:
    return value is not None and value.strip().lower() in PLACEHOLDER_VALUES


def fix_mojibake(value: str | None) -> tuple[str | None, bool]:
    """Repair UTF-8 text that was mis-decoded as Windows-1252 (e.g. 'â€”' -> '—')."""
    if not value or not any(m in value for m in _MOJIBAKE_MARKERS):
        return value, False
    try:
        repaired = value.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value, False
    return repaired, repaired != value


def parse_date(value: str | None) -> tuple[date | None, bool]:
    """Parse a date. Returns (date, was_repaired).

    ISO `YYYY-MM-DD` is the expected format. Anything else is split into its
    numeric parts and interpreted as Y-M-D (4-digit first) or US M-D-Y
    (4-digit last), which repairs values such as '03-15/2025'.
    """
    text = clean_str(value)
    if text is None:
        return None, False
    try:
        return date.fromisoformat(text), False
    except ValueError:
        pass

    parts = re.findall(r"\d+", text)
    if len(parts) != 3:
        return None, False
    try:
        if len(parts[0]) == 4:
            y, m, d = (int(p) for p in parts)
        elif len(parts[2]) == 4:
            m, d, y = (int(p) for p in parts)
        else:
            return None, False
        return date(y, m, d), True
    except ValueError:
        return None, False


def parse_time(value: str | None) -> time | None:
    text = clean_str(value)
    if text is None:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def parse_timestamp(value: str | None, local_tz: tzinfo) -> tuple[datetime | None, bool, bool]:
    """Parse an ISO-8601 timestamp into the firm's local zone.

    Returns (datetime, had_explicit_offset, is_strict_iso). Naive values are
    assumed to already be firm-local time.
    """
    text = clean_str(value)
    if text is None:
        return None, False, False
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, False, False
    strict = bool(_STRICT_ISO_RE.match(text))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_tz), False, strict
    return parsed.astimezone(local_tz), True, strict


def normalize_email(value: str | None) -> tuple[str | None, bool]:
    """Return (email, was_repaired); email is None when it can't be salvaged."""
    text = clean_str(value)
    if text is None:
        return None, False
    email = text.lower()
    if _EMAIL_RE.match(email):
        return email, False
    repaired = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", email)
    repaired = re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", repaired)
    if repaired != email and _EMAIL_RE.match(repaired):
        return repaired, True
    return None, False


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1]


def classify_location(location: str | None, meeting_type: str | None = None) -> LocationKind:
    """Infer whether a meeting is virtual or physical from its location (and CRM type)."""
    if location:
        lowered = location.lower()
        if any(k in lowered for k in _VIRTUAL_KEYWORDS):
            return LocationKind.VIRTUAL
        return LocationKind.PHYSICAL
    if meeting_type:
        lowered = meeting_type.lower()
        if lowered == "virtual":
            return LocationKind.VIRTUAL
        if lowered == "in-person":
            return LocationKind.PHYSICAL
    return LocationKind.UNKNOWN
