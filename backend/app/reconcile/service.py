"""Reconciliation pipeline: normalised events -> duplicate groups -> matches -> unified meetings."""

from datetime import datetime

from app.models.normalized import NormalizedEvent, Source
from app.models.unified import UnifiedMeeting
from app.reconcile.duplicates import group_duplicates
from app.reconcile.matcher import match_groups
from app.reconcile.merger import merge_match, single_source


def _sort_key(m: UnifiedMeeting) -> tuple:
    return (m.date or datetime.max.date(), m.start.time() if m.start else datetime.max.time(), m.title or "")


def reconcile(events: list[NormalizedEvent]) -> list[UnifiedMeeting]:
    crm_groups = group_duplicates([e for e in events if e.source is Source.CRM])
    cal_groups = group_duplicates([e for e in events if e.source is Source.CALENDAR])
    matches, crm_only, cal_only = match_groups(crm_groups, cal_groups)

    meetings = [merge_match(m) for m in matches]
    meetings += [single_source(g) for g in crm_only + cal_only]
    return sorted(meetings, key=_sort_key)
