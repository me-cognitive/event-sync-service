"""Response shapes specific to the HTTP API."""

from datetime import date as Date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.models.normalized import DataIssue, Severity, Source
from app.models.unified import UnifiedMeeting


class SourceFilter(StrEnum):
    CRM = "crm"  # CRM only
    CALENDAR = "calendar"  # Calendar only
    BOTH = "both"  # matched across both sources


class MeetingListItem(BaseModel):
    """Compact row for the meetings table."""

    id: str
    title: str | None
    date: Date | None
    start: datetime | None
    end: datetime | None
    client_name: str | None
    client_company: str | None
    owner: str | None
    location: str | None
    status: str | None
    sources: list[Source]
    source_ids: dict[Source, list[str]]
    match_confidence: float | None
    conflict_count: int
    max_conflict_severity: Severity | None
    conflict_fields: list[str]
    issue_count: int
    has_duplicates: bool

    @classmethod
    def from_meeting(cls, m: UnifiedMeeting) -> "MeetingListItem":
        severities = [c.severity for c in m.conflicts]
        order = [Severity.INFO, Severity.WARNING, Severity.ERROR]
        return cls(
            id=m.id,
            title=m.title,
            date=m.date,
            start=m.start,
            end=m.fields["end"].value,
            client_name=m.fields["client_name"].value,
            client_company=m.fields["client_company"].value,
            owner=m.fields["owner"].value,
            location=m.fields["location"].value,
            status=m.status,
            sources=m.sources,
            source_ids=m.source_ids,
            match_confidence=m.match.confidence if m.match else None,
            conflict_count=len(m.conflicts),
            max_conflict_severity=max(severities, key=order.index) if severities else None,
            conflict_fields=[c.field for c in m.conflicts],
            issue_count=len(m.issues),
            has_duplicates=bool(m.duplicates),
        )


class MeetingDetail(UnifiedMeeting):
    """Full meeting plus the untouched upstream records it was built from."""

    raw_records: dict[str, dict[str, Any]]


class IssueItem(DataIssue):
    meeting_id: str | None


class Health(BaseModel):
    status: str
    data_source: str
    ready: bool
    last_error: str | None
