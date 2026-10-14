"""The reconciled view served by the API: one UnifiedMeeting per real-world meeting."""

from datetime import date as Date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from app.models.normalized import DataIssue, Severity, Source


class FieldStatus(StrEnum):
    AGREE = "agree"  # both sources give the same value
    EQUIVALENT = "equivalent"  # different text, same meaning ("Virtual" vs "Zoom")
    REFINEMENT = "refinement"  # one value is a more specific form of the other
    COMPLEMENTARY = "complementary"  # different but not contradictory (titles, notes)
    FILLED = "filled"  # one source was empty; filled from the other
    CONFLICT = "conflict"  # sources contradict each other
    SINGLE_SOURCE = "single_source"  # only one source knows this field


class FieldValue(BaseModel):
    """A unified value plus where it came from and what each source said."""

    value: Any = None
    source: Source | None = None  # source the unified value was taken from
    crm: Any = None
    calendar: Any = None
    status: FieldStatus
    rule: str  # human-readable resolution rule


class Conflict(BaseModel):
    field: str
    severity: Severity
    message: str
    crm: Any = None
    calendar: Any = None


class MatchInfo(BaseModel):
    confidence: float
    reasons: list[str]
    breakdown: dict[str, float]
    # Records the score was computed on; for a duplicate group this is its best-matching member.
    scored_pair: dict[Source, str]


class DuplicateInfo(BaseModel):
    source: Source
    canonical_id: str
    superseded_ids: list[str]
    reason: str


class UnifiedMeeting(BaseModel):
    id: str
    source_ids: dict[Source, list[str]]
    match: MatchInfo | None = None
    fields: dict[str, FieldValue]
    conflicts: list[Conflict] = Field(default_factory=list)
    duplicates: list[DuplicateInfo] = Field(default_factory=list)
    issues: list[DataIssue] = Field(default_factory=list)

    @computed_field
    @property
    def sources(self) -> list[Source]:
        return [s for s in (Source.CRM, Source.CALENDAR) if self.source_ids.get(s)]

    @computed_field
    @property
    def title(self) -> str | None:
        return self.fields["title"].value

    @computed_field
    @property
    def start(self) -> datetime | None:
        return self.fields["start"].value

    @computed_field
    @property
    def date(self) -> Date | None:
        return self.fields["date"].value

    @computed_field
    @property
    def status(self) -> str | None:
        return self.fields["status"].value


class SyncSummary(BaseModel):
    synced_at: datetime
    data_source: str
    records: dict[Source, int]
    meetings: int
    matched: int
    crm_only: int
    calendar_only: int
    with_conflicts: int
    conflicts_by_field: dict[str, int]
    duplicate_groups: int
    issues_by_severity: dict[Severity, int]
