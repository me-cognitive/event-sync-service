"""Source-agnostic representation every upstream record is mapped into before reconciliation."""

from datetime import date as Date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Source(StrEnum):
    CRM = "crm"
    CALENDAR = "calendar"


class Severity(StrEnum):
    INFO = "info"  # normalised silently, worth knowing
    WARNING = "warning"  # value was repaired or is missing; review advised
    ERROR = "error"  # value unusable


class IssueCode(StrEnum):
    MALFORMED_DATE_REPAIRED = "malformed_date_repaired"
    INVALID_DATE = "invalid_date"
    MISSING_TIME = "missing_time"
    INVALID_TIME = "invalid_time"
    INVALID_TIMESTAMP = "invalid_timestamp"
    NONSTANDARD_TIMESTAMP = "nonstandard_timestamp"
    TIMEZONE_CONVERTED = "timezone_converted"
    END_BEFORE_START = "end_before_start"
    EMAIL_REPAIRED = "email_repaired"
    INVALID_ATTENDEE = "invalid_attendee"
    MISSING_ATTENDEES = "missing_attendees"
    MISSING_LOCATION = "missing_location"
    MISSING_FIELD = "missing_field"
    PLACEHOLDER_VALUE = "placeholder_value"
    ENCODING_REPAIRED = "encoding_repaired"
    UNPARSEABLE_RECORD = "unparseable_record"


class DataIssue(BaseModel):
    source: Source
    source_id: str
    field: str
    code: IssueCode
    severity: Severity
    message: str
    raw_value: Any = None


class LocationKind(StrEnum):
    PHYSICAL = "physical"
    VIRTUAL = "virtual"
    UNKNOWN = "unknown"


class NormalizedEvent(BaseModel):
    source: Source
    source_id: str

    title: str | None = None
    # Timestamps are timezone-aware, expressed in the firm's local zone.
    start: datetime | None = None
    end: datetime | None = None
    date: Date | None = None
    time_known: bool = False

    client_name: str | None = None
    client_company: str | None = None
    owner_name: str | None = None
    organizer_email: str | None = None
    attendees: list[str] = Field(default_factory=list)
    unparsed_attendees: list[str] = Field(default_factory=list)
    external_domains: list[str] = Field(default_factory=list)
    is_internal: bool = False

    location: str | None = None
    location_kind: LocationKind = LocationKind.UNKNOWN
    meeting_type: str | None = None
    status: str | None = None
    notes: str | None = None
    is_recurring: bool = False
    created_at: datetime | None = None

    raw: dict[str, Any]
    issues: list[DataIssue] = Field(default_factory=list)
