"""Map raw CRM and Calendar records into NormalizedEvent, recording every repair as a DataIssue."""

from datetime import datetime, tzinfo
from typing import Any

from app.ingest.parsing import (
    classify_location,
    clean_str,
    email_domain,
    fix_mojibake,
    is_placeholder,
    normalize_email,
    parse_date,
    parse_time,
    parse_timestamp,
)
from app.models.normalized import (
    DataIssue,
    IssueCode,
    LocationKind,
    NormalizedEvent,
    Severity,
    Source,
)


class _Issues:
    """Small helper so normalisers can append issues tersely."""

    def __init__(self, source: Source, source_id: str):
        self.source, self.source_id = source, source_id
        self.items: list[DataIssue] = []

    def add(self, field: str, code: IssueCode, severity: Severity, message: str, raw: Any = None) -> None:
        self.items.append(
            DataIssue(
                source=self.source,
                source_id=self.source_id,
                field=field,
                code=code,
                severity=severity,
                message=message,
                raw_value=raw,
            )
        )


def _text(raw: dict[str, Any], field: str, issues: _Issues) -> str | None:
    value, repaired = fix_mojibake(clean_str(raw.get(field)))
    if repaired:
        issues.add(field, IssueCode.ENCODING_REPAIRED, Severity.INFO,
                   "Repaired mis-encoded characters", raw.get(field))
    return value


def _created_at(raw: dict[str, Any], tz: tzinfo, issues: _Issues) -> datetime | None:
    value, _, _ = parse_timestamp(raw.get("created_at"), tz)
    if value is None and raw.get("created_at") is not None:
        issues.add("created_at", IssueCode.INVALID_TIMESTAMP, Severity.WARNING,
                   "Unparseable created_at", raw.get("created_at"))
    return value


def normalize_crm(raw: dict[str, Any], tz: tzinfo) -> NormalizedEvent:
    source_id = clean_str(raw.get("crm_id")) or "CRM-UNKNOWN"
    issues = _Issues(Source.CRM, source_id)

    meeting_date, date_repaired = parse_date(raw.get("meeting_date"))
    if date_repaired:
        issues.add("meeting_date", IssueCode.MALFORMED_DATE_REPAIRED, Severity.WARNING,
                   f"Non-ISO date interpreted as {meeting_date.isoformat()}", raw.get("meeting_date"))
    elif meeting_date is None:
        issues.add("meeting_date", IssueCode.INVALID_DATE, Severity.ERROR,
                   "Meeting date missing or unparseable", raw.get("meeting_date"))

    meeting_time = parse_time(raw.get("meeting_time"))
    if meeting_time is None:
        if raw.get("meeting_time") is None:
            issues.add("meeting_time", IssueCode.MISSING_TIME, Severity.WARNING, "Meeting time is missing")
        else:
            issues.add("meeting_time", IssueCode.INVALID_TIME, Severity.WARNING,
                       "Unparseable meeting time", raw.get("meeting_time"))

    start = (
        datetime.combine(meeting_date, meeting_time, tzinfo=tz)
        if meeting_date and meeting_time
        else None
    )

    meeting_type = clean_str(raw.get("meeting_type"))
    is_internal = (meeting_type or "").lower() == "internal"

    client_name = _text(raw, "client_name", issues)
    if is_placeholder(client_name):
        issues.add("client_name", IssueCode.PLACEHOLDER_VALUE, Severity.INFO,
                   f"Placeholder client name '{client_name}' treated as unknown", client_name)
        client_name = None
    client_company = _text(raw, "client_company", issues)
    if not is_internal and client_name is None and client_company is None:
        issues.add("client_name", IssueCode.MISSING_FIELD, Severity.WARNING,
                   "External meeting has no client")

    location = _text(raw, "location", issues)

    return NormalizedEvent(
        source=Source.CRM,
        source_id=source_id,
        title=_text(raw, "subject", issues),
        start=start,
        date=meeting_date,
        time_known=start is not None,
        client_name=client_name,
        client_company=client_company,
        owner_name=_text(raw, "relationship_owner", issues),
        is_internal=is_internal,
        location=location,
        location_kind=classify_location(location, meeting_type),
        meeting_type=meeting_type,
        status=(clean_str(raw.get("status")) or "").lower() or None,
        notes=_text(raw, "notes", issues),
        created_at=_created_at(raw, tz, issues),
        raw=raw,
        issues=issues.items,
    )


def normalize_calendar(raw: dict[str, Any], tz: tzinfo, firm_domain: str) -> NormalizedEvent:
    source_id = clean_str(raw.get("event_id")) or "CAL-UNKNOWN"
    issues = _Issues(Source.CALENDAR, source_id)

    def timestamp(field: str) -> datetime | None:
        value, had_offset, strict = parse_timestamp(raw.get(field), tz)
        if value is None:
            issues.add(field, IssueCode.INVALID_TIMESTAMP, Severity.ERROR,
                       f"{field} missing or unparseable", raw.get(field))
        elif had_offset:
            issues.add(field, IssueCode.TIMEZONE_CONVERTED, Severity.INFO,
                       f"Explicit UTC offset converted to firm-local time {value.strftime('%H:%M %Z')}",
                       raw.get(field))
        elif not strict:
            issues.add(field, IssueCode.NONSTANDARD_TIMESTAMP, Severity.INFO,
                       "Timestamp not in full ISO-8601 form", raw.get(field))
        return value

    start, end = timestamp("start_time"), timestamp("end_time")
    if start and end and end < start:
        issues.add("end_time", IssueCode.END_BEFORE_START, Severity.WARNING, "End time precedes start time")

    organizer, _ = normalize_email(raw.get("organizer"))

    attendees: list[str] = []
    unparsed: list[str] = []
    for value in raw.get("attendees") or []:
        email, repaired = normalize_email(value)
        if email is None:
            unparsed.append(str(value))
            issues.add("attendees", IssueCode.INVALID_ATTENDEE, Severity.WARNING,
                       f"Attendee '{value}' is not a valid email", value)
        else:
            if repaired:
                issues.add("attendees", IssueCode.EMAIL_REPAIRED, Severity.WARNING,
                           f"Attendee email repaired to {email}", value)
            if email not in attendees:
                attendees.append(email)
    if not attendees and not unparsed:
        issues.add("attendees", IssueCode.MISSING_ATTENDEES, Severity.WARNING, "No attendees listed")

    external_domains = sorted({email_domain(a) for a in attendees} - {firm_domain})

    location = _text(raw, "location", issues)
    if location is None:
        issues.add("location", IssueCode.MISSING_LOCATION, Severity.INFO, "No location given")

    return NormalizedEvent(
        source=Source.CALENDAR,
        source_id=source_id,
        title=_text(raw, "title", issues),
        start=start,
        end=end,
        date=start.date() if start else None,
        time_known=start is not None,
        organizer_email=organizer,
        attendees=attendees,
        unparsed_attendees=unparsed,
        external_domains=external_domains,
        is_internal=bool(attendees) and not external_domains and not unparsed,
        location=location,
        location_kind=classify_location(location) if location else LocationKind.UNKNOWN,
        status=(clean_str(raw.get("status")) or "").lower() or None,
        notes=_text(raw, "description", issues),
        is_recurring=bool(raw.get("is_recurring")),
        created_at=_created_at(raw, tz, issues),
        raw=raw,
        issues=issues.items,
    )


def safe_normalize(raw: Any, source: Source, tz: tzinfo, firm_domain: str) -> NormalizedEvent:
    """Normalise one record; a record that can't be processed is kept with an error issue, never dropped."""
    id_field = "crm_id" if source is Source.CRM else "event_id"
    try:
        if not isinstance(raw, dict):
            raise TypeError(f"expected an object, got {type(raw).__name__}")
        if source is Source.CRM:
            return normalize_crm(raw, tz)
        return normalize_calendar(raw, tz, firm_domain)
    except Exception as exc:  # defensive: one bad record must not break the sync
        source_id = str(raw.get(id_field)) if isinstance(raw, dict) else "UNKNOWN"
        return NormalizedEvent(
            source=source,
            source_id=source_id,
            raw=raw if isinstance(raw, dict) else {"value": raw},
            issues=[DataIssue(source=source, source_id=source_id, field="*",
                              code=IssueCode.UNPARSEABLE_RECORD, severity=Severity.ERROR,
                              message=f"Record could not be processed: {exc}")],
        )
