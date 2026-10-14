"""Build UnifiedMeetings with per-field provenance and conflict classification.

Source-of-truth policy:
  * Calendar owns scheduling: title, start/end, attendees, location, recurrence.
  * CRM owns the relationship: client, company, owner, meeting type, notes.
  * Status: the most restrictive value wins (cancelled > tentative > confirmed),
    because showing a cancelled meeting as live is the costlier mistake.
Every disagreement is preserved: both raw values stay on the FieldValue.
"""

import hashlib
from typing import Any

from app.models.normalized import IssueCode, LocationKind, NormalizedEvent, Severity, Source
from app.models.unified import Conflict, FieldStatus, FieldValue, MatchInfo, UnifiedMeeting
from app.reconcile.duplicates import EventGroup
from app.reconcile.matcher import Match
from app.reconcile.similarity import (
    is_refinement,
    localpart,
    location_similarity,
    name_localparts,
    normalize_location,
)

LOCATION_EQUIVALENCE = 0.8

# Rank used for "most restrictive wins". Scheduled/confirmed/completed are all "live".
STATUS_RANK = {"cancelled": 3, "tentative": 2, "confirmed": 1, "scheduled": 1, "completed": 1}

CRM, CAL = Source.CRM, Source.CALENDAR


def meeting_id(source_ids: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(source_ids)).encode()).hexdigest()[:12]


def _single(event: NormalizedEvent, value: Any, rule: str = "Only one source has this meeting") -> FieldValue:
    side = {"crm": value} if event.source is CRM else {"calendar": value}
    status = FieldStatus.SINGLE_SOURCE
    return FieldValue(value=value, source=event.source if value is not None else None, status=status, rule=rule, **side)


def _owned(owner: Source, crm_val: Any, cal_val: Any, rule: str) -> FieldValue:
    """Field only one system tracks in a matched meeting."""
    value = crm_val if owner is CRM else cal_val
    return FieldValue(value=value, source=owner if value is not None else None,
                      crm=crm_val, calendar=cal_val, status=FieldStatus.SINGLE_SOURCE, rule=rule)


# --- per-field merge rules for matched meetings -------------------------------------------------


def _merge_title(crm: NormalizedEvent, cal: NormalizedEvent) -> FieldValue:
    same = (crm.title or "").lower() == (cal.title or "").lower()
    return FieldValue(
        value=cal.title or crm.title, source=CAL if cal.title else CRM, crm=crm.title, calendar=cal.title,
        status=FieldStatus.AGREE if same else FieldStatus.COMPLEMENTARY,
        rule="Calendar title preferred (usually names the client); CRM subject kept for reference",
    )


def _merge_start(crm: NormalizedEvent, cal_group: EventGroup) -> tuple[FieldValue, Conflict | None]:
    cal = cal_group.canonical
    rule = "Calendar is the scheduling system of record"
    if crm.start is None:
        return FieldValue(value=cal.start, source=CAL, crm=None, calendar=cal.start,
                          status=FieldStatus.FILLED, rule=f"CRM has no time; {rule.lower()}"), None
    if cal.start is None:
        return FieldValue(value=crm.start, source=CRM, crm=crm.start, calendar=None,
                          status=FieldStatus.FILLED, rule="Calendar has no valid time; CRM used"), None
    if crm.start == cal.start:
        return FieldValue(value=cal.start, source=CAL, crm=crm.start, calendar=cal.start,
                          status=FieldStatus.AGREE, rule=rule), None

    minutes = abs((crm.start - cal.start).total_seconds()) / 60
    message = f"Start time differs by {minutes:.0f} min: CRM {crm.start:%H:%M}, Calendar {cal.start:%H:%M}"
    converted = any(i.code is IssueCode.TIMEZONE_CONVERTED and i.field == "start_time" for i in cal.issues)
    if converted and minutes == 60:
        message += (". The calendar time was supplied in UTC; a 1-hour gap usually means one system "
                    "ignored daylight saving")
    for dup in cal_group.superseded:
        if dup.start == crm.start:
            message += (f". Superseded duplicate {dup.source_id} has {dup.start:%H:%M}, matching CRM; "
                        "the meeting was likely rescheduled")
    field = FieldValue(value=cal.start, source=CAL, crm=crm.start, calendar=cal.start,
                       status=FieldStatus.CONFLICT, rule=rule)
    return field, Conflict(field="start", severity=Severity.WARNING, message=message,
                           crm=crm.start, calendar=cal.start)


def _merge_location(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[FieldValue, Conflict | None]:
    a, b = crm.location, cal.location

    def fv(value: Any, source: Source | None, status: FieldStatus, rule: str) -> FieldValue:
        return FieldValue(value=value, source=source, crm=a, calendar=b, status=status, rule=rule)

    if a is None and b is None:
        return fv(None, None, FieldStatus.AGREE, "Neither source has a location"), None
    if a is None or b is None:
        source = CAL if b else CRM
        return fv(b or a, source, FieldStatus.FILLED, f"Only {source.value} has a location"), None

    kinds = {crm.location_kind, cal.location_kind}
    if kinds == {LocationKind.PHYSICAL, LocationKind.VIRTUAL}:
        field = fv(b, CAL, FieldStatus.CONFLICT, "Calendar location preferred (it is what attendees join)")
        return field, Conflict(
            field="location", severity=Severity.WARNING,
            message=f"Meeting format differs: CRM says {crm.location_kind.value} '{a}', "
                    f"Calendar says {cal.location_kind.value} '{b}'",
            crm=a, calendar=b,
        )
    if a.lower() == b.lower():
        return fv(b, CAL, FieldStatus.AGREE, "Both sources agree"), None
    if is_refinement(a, b):
        return fv(b, CAL, FieldStatus.REFINEMENT, "Calendar location is more specific"), None
    if is_refinement(b, a):
        return fv(a, CRM, FieldStatus.REFINEMENT, "CRM location is more specific"), None
    if (normalize_location(a) == normalize_location(b)
            or kinds == {LocationKind.VIRTUAL}
            or location_similarity(a, b) >= LOCATION_EQUIVALENCE):
        return fv(b, CAL, FieldStatus.EQUIVALENT, "Same place described differently; Calendar wording kept"), None

    field = fv(b, CAL, FieldStatus.CONFLICT, "Calendar location preferred (it is what attendees join)")
    return field, Conflict(field="location", severity=Severity.WARNING,
                           message=f"Different locations: CRM '{a}', Calendar '{b}'", crm=a, calendar=b)


def _merge_meeting_type(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[FieldValue, Conflict | None]:
    cal_kind = cal.location_kind.value
    rule = "CRM meeting type, validated against the calendar location"

    def fv(status: FieldStatus) -> FieldValue:
        return FieldValue(value=crm.meeting_type, source=CRM if crm.meeting_type else None,
                          crm=crm.meeting_type, calendar=cal_kind, status=status, rule=rule)

    crm_type = (crm.meeting_type or "").lower()
    if crm_type == "internal":
        return fv(FieldStatus.AGREE if cal.is_internal else FieldStatus.COMPLEMENTARY), None
    expected = {"in-person": LocationKind.PHYSICAL, "virtual": LocationKind.VIRTUAL}.get(crm_type)
    if expected is None or cal.location_kind is LocationKind.UNKNOWN:
        return fv(FieldStatus.SINGLE_SOURCE), None
    if expected is cal.location_kind:
        return fv(FieldStatus.AGREE), None
    return fv(FieldStatus.CONFLICT), Conflict(
        field="meeting_type", severity=Severity.WARNING,
        message=f"CRM type is {crm.meeting_type} but the calendar location is {cal_kind}",
        crm=crm.meeting_type, calendar=cal_kind,
    )


def _merge_status(crm: NormalizedEvent, cal: NormalizedEvent) -> tuple[FieldValue, Conflict | None]:
    a, b = crm.status, cal.status
    rule = "Most restrictive status wins (cancelled > tentative > confirmed)"
    if a == b or not (a and b):
        value, source = (a, CRM) if a else (b, CAL)
        return FieldValue(value=value, source=source if value else None, crm=a, calendar=b,
                          status=FieldStatus.AGREE if a == b else FieldStatus.FILLED, rule=rule), None

    ra, rb = STATUS_RANK.get(a, 1), STATUS_RANK.get(b, 1)
    if ra == rb:
        # e.g. CRM "completed" vs Calendar "confirmed": lifecycle wording, not a contradiction.
        return FieldValue(value=a, source=CRM, crm=a, calendar=b, status=FieldStatus.EQUIVALENT,
                          rule="Same lifecycle state; CRM wording kept (it tracks outcome)"), None

    value, source = (a, CRM) if ra > rb else (b, CAL)
    severity = Severity.ERROR if "cancelled" in (a, b) else Severity.WARNING
    field = FieldValue(value=value, source=source, crm=a, calendar=b, status=FieldStatus.CONFLICT, rule=rule)
    return field, Conflict(field="status", severity=severity,
                           message=f"Status differs: CRM '{a}', Calendar '{b}'. Showing '{value}'",
                           crm=a, calendar=b)


def _merge_owner(crm: NormalizedEvent, cal: NormalizedEvent) -> FieldValue:
    same = bool(cal.organizer_email) and localpart(cal.organizer_email) in name_localparts(crm.owner_name)
    return FieldValue(
        value=crm.owner_name, source=CRM if crm.owner_name else None,
        crm=crm.owner_name, calendar=cal.organizer_email,
        status=FieldStatus.AGREE if same else FieldStatus.COMPLEMENTARY,
        rule="CRM relationship owner preferred; calendar organizer shown for reference",
    )


def _merge_notes(crm: NormalizedEvent, cal: NormalizedEvent) -> FieldValue:
    a, b = crm.notes, cal.notes
    if a and b:
        status = FieldStatus.COMPLEMENTARY
    elif a or b:
        status = FieldStatus.FILLED
    else:
        status = FieldStatus.AGREE
    return FieldValue(value=a or b, source=CRM if a else (CAL if b else None), crm=a, calendar=b,
                      status=status, rule="Both kept; CRM notes shown first (richer client context)")


# --- assembly ---------------------------------------------------------------------------------


def merge_match(match: Match) -> UnifiedMeeting:
    crm, cal = match.crm.canonical, match.calendar.canonical
    start, start_conflict = _merge_start(crm, match.calendar)
    location, location_conflict = _merge_location(crm, cal)
    meeting_type, type_conflict = _merge_meeting_type(crm, cal)
    status, status_conflict = _merge_status(crm, cal)

    # A physical-vs-virtual location conflict already explains a meeting-type mismatch.
    if location_conflict and {crm.location_kind, cal.location_kind} == {LocationKind.PHYSICAL, LocationKind.VIRTUAL}:
        type_conflict = None

    fields = {
        "title": _merge_title(crm, cal),
        "date": FieldValue(value=start.value.date() if start.value else crm.date, source=start.source,
                           crm=crm.date, calendar=cal.date, status=FieldStatus.AGREE,
                           rule="Records are only matched on the same date"),
        "start": start,
        "end": _owned(CAL, None, cal.end, "Only the calendar records an end time"),
        "client_name": _owned(CRM, crm.client_name, None, "CRM is the relationship system of record"),
        "client_company": _owned(CRM, crm.client_company, None, "CRM is the relationship system of record"),
        "owner": _merge_owner(crm, cal),
        "attendees": _owned(CAL, None, cal.attendees, "Calendar holds the actual invitees"),
        "location": location,
        "meeting_type": meeting_type,
        "status": status,
        "notes": _merge_notes(crm, cal),
        "is_recurring": _owned(CAL, None, cal.is_recurring, "Only the calendar tracks recurrence"),
    }
    groups = [match.crm, match.calendar]
    ids = {CRM: [e.source_id for e in match.crm.members], CAL: [e.source_id for e in match.calendar.members]}
    return UnifiedMeeting(
        id=meeting_id(ids[CRM] + ids[CAL]),
        source_ids=ids,
        match=MatchInfo(
            confidence=match.score.total,
            reasons=match.score.reasons,
            breakdown=match.score.breakdown,
            scored_pair={CRM: match.crm_record.source_id, CAL: match.calendar_record.source_id},
        ),
        fields=fields,
        conflicts=[c for c in (start_conflict, location_conflict, type_conflict, status_conflict) if c],
        duplicates=[d for g in groups if (d := g.duplicate_info())],
        issues=[i for g in groups for e in g.members for i in e.issues],
    )


def single_source(group: EventGroup) -> UnifiedMeeting:
    e = group.canonical
    is_cal = e.source is CAL
    fields = {
        "title": _single(e, e.title),
        "date": _single(e, e.date),
        "start": _single(e, e.start),
        "end": _single(e, e.end),
        "client_name": _single(e, e.client_name),
        "client_company": _single(e, e.client_company),
        "owner": _single(e, e.organizer_email if is_cal else e.owner_name),
        "attendees": _single(e, e.attendees if is_cal else None),
        "location": _single(e, e.location),
        "meeting_type": _single(e, e.location_kind.value if is_cal else e.meeting_type),
        "status": _single(e, e.status),
        "notes": _single(e, e.notes),
        "is_recurring": _single(e, e.is_recurring if is_cal else None),
    }
    ids = [m.source_id for m in group.members]
    return UnifiedMeeting(
        id=meeting_id(ids),
        source_ids={e.source: ids},
        fields=fields,
        duplicates=[d] if (d := group.duplicate_info()) else [],
        issues=[i for m in group.members for i in m.issues],
    )
