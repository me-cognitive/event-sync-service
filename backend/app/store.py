"""In-memory store holding the latest reconciliation result.

The whole dataset is re-derived on every sync, so there is nothing to persist
between runs. A failed sync keeps the previous result and records the error.
"""

import logging
import threading
from collections import Counter
from datetime import UTC, datetime

from app.config import Settings
from app.ingest.loaders import SourceLoader
from app.ingest.pipeline import ingest
from app.models.normalized import NormalizedEvent, Severity, Source
from app.models.unified import SyncSummary, UnifiedMeeting
from app.reconcile.service import reconcile

log = logging.getLogger(__name__)


class Store:
    def __init__(self, settings: Settings, loader: SourceLoader | None = None):
        self.settings = settings
        self.loader = loader
        self._lock = threading.Lock()
        self.events: dict[str, NormalizedEvent] = {}
        self.meetings: list[UnifiedMeeting] = []
        self.by_id: dict[str, UnifiedMeeting] = {}
        self.summary: SyncSummary | None = None
        self.last_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.summary is not None

    def sync(self) -> SyncSummary:
        try:
            events = ingest(self.settings, self.loader)
            meetings = reconcile(events)
        except Exception as exc:
            log.exception("Sync failed")
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        summary = self._summarize(events, meetings)
        with self._lock:
            self.events = {e.source_id: e for e in events}
            self.meetings = meetings
            self.by_id = {m.id: m for m in meetings}
            self.summary = summary
            self.last_error = None
        log.info("Synced %d records into %d meetings", len(events), len(meetings))
        return summary

    def meeting_for_record(self) -> dict[str, str]:
        return {sid: m.id for m in self.meetings for ids in m.source_ids.values() for sid in ids}

    def _summarize(self, events: list[NormalizedEvent], meetings: list[UnifiedMeeting]) -> SyncSummary:
        issues = [i for e in events for i in e.issues]
        return SyncSummary(
            synced_at=datetime.now(UTC),
            data_source=self.settings.data_source,
            records=Counter(e.source for e in events),
            meetings=len(meetings),
            matched=sum(1 for m in meetings if len(m.sources) == 2),
            crm_only=sum(1 for m in meetings if m.sources == [Source.CRM]),
            calendar_only=sum(1 for m in meetings if m.sources == [Source.CALENDAR]),
            with_conflicts=sum(1 for m in meetings if m.conflicts),
            conflicts_by_field=Counter(c.field for m in meetings for c in m.conflicts),
            duplicate_groups=sum(len(m.duplicates) for m in meetings),
            issues_by_severity={s: sum(1 for i in issues if i.severity is s) for s in Severity},
        )
