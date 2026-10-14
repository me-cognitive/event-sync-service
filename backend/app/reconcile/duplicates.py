"""Detect records within a single source that describe the same meeting.

Two events are duplicates when they share a date and an owner/organizer,
their time windows overlap (or start within an hour of each other), and they
involve the same external party. Recurring instances on different dates never
qualify because the date must match.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.models.normalized import NormalizedEvent, Source
from app.models.unified import DuplicateInfo
from app.reconcile.similarity import company_tokens, title_similarity

MAX_START_GAP = timedelta(minutes=60)
INTERNAL_TITLE_THRESHOLD = 0.85


@dataclass
class EventGroup:
    """One or more same-source events treated as a single meeting; `canonical` supplies the values."""

    canonical: NormalizedEvent
    superseded: list[NormalizedEvent] = field(default_factory=list)
    reason: str | None = None

    @property
    def members(self) -> list[NormalizedEvent]:
        return [self.canonical, *self.superseded]

    def duplicate_info(self) -> DuplicateInfo | None:
        if not self.superseded:
            return None
        return DuplicateInfo(
            source=self.canonical.source,
            canonical_id=self.canonical.source_id,
            superseded_ids=[e.source_id for e in self.superseded],
            reason=self.reason or "",
        )


def _owner_key(e: NormalizedEvent) -> str | None:
    return e.organizer_email if e.source is Source.CALENDAR else (e.owner_name or "").lower() or None


def _parties(e: NormalizedEvent) -> set[str]:
    if e.source is Source.CALENDAR:
        return set(e.external_domains)
    return company_tokens(e.client_company)


def _times_close(a: NormalizedEvent, b: NormalizedEvent) -> bool:
    if not (a.start and b.start):
        return False
    if a.end and b.end and a.start < b.end and b.start < a.end:
        return True
    return abs(a.start - b.start) <= MAX_START_GAP


def _duplicate_reason(a: NormalizedEvent, b: NormalizedEvent) -> str | None:
    if a.date is None or a.date != b.date or not _times_close(a, b):
        return None
    if _owner_key(a) is None or _owner_key(a) != _owner_key(b):
        return None
    shared = _parties(a) & _parties(b)
    if shared:
        return f"Same day, same owner, overlapping time, same external party ({', '.join(sorted(shared))})"
    if a.is_internal and b.is_internal and title_similarity(a.title, b.title) >= INTERNAL_TITLE_THRESHOLD:
        return "Same day, same owner, overlapping time, near-identical internal title"
    return None


def _newest_first(e: NormalizedEvent) -> datetime:
    return e.created_at or datetime.min.replace(tzinfo=e.start.tzinfo if e.start else None)


def group_duplicates(events: list[NormalizedEvent]) -> list[EventGroup]:
    """Cluster same-source duplicates; the most recently created record becomes canonical."""
    parent = list(range(len(events)))
    reasons: dict[int, str] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            if a.source is b.source and (reason := _duplicate_reason(a, b)):
                root_i, root_j = find(i), find(j)
                parent[root_j] = root_i
                reasons[root_i] = reason

    clusters: dict[int, list[NormalizedEvent]] = {}
    for i, e in enumerate(events):
        clusters.setdefault(find(i), []).append(e)

    groups = []
    for root, members in clusters.items():
        ordered = sorted(members, key=_newest_first, reverse=True)
        groups.append(EventGroup(canonical=ordered[0], superseded=ordered[1:], reason=reasons.get(root)))
    return groups
