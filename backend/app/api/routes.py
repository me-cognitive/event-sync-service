from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.models.api import Health, IssueItem, MeetingDetail, MeetingListItem, SourceFilter
from app.models.normalized import Severity, Source
from app.models.unified import SyncSummary, UnifiedMeeting
from app.store import Store

router = APIRouter(prefix="/api")


def get_store(request: Request) -> Store:
    return request.app.state.store


def ready_store(store: Store = Depends(get_store)) -> Store:
    if not store.ready:
        raise HTTPException(503, detail=f"Data not loaded yet: {store.last_error or 'sync pending'}")
    return store


def _matches_filters(
    m: UnifiedMeeting,
    source: SourceFilter | None,
    has_conflicts: bool | None,
    status: str | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
) -> bool:
    if source == SourceFilter.BOTH and len(m.sources) != 2:
        return False
    if source in (SourceFilter.CRM, SourceFilter.CALENDAR) and m.sources != [Source(source.value)]:
        return False
    if has_conflicts is not None and bool(m.conflicts) != has_conflicts:
        return False
    if status and (m.status or "").lower() != status.lower():
        return False
    if date_from and (m.date is None or m.date < date_from):
        return False
    if date_to and (m.date is None or m.date > date_to):
        return False
    if q:
        haystack = " ".join(
            str(m.fields[f].value or "") for f in ("title", "client_name", "client_company", "owner", "location")
        ) + " " + " ".join(i for ids in m.source_ids.values() for i in ids)
        if q.lower() not in haystack.lower():
            return False
    return True


@router.get("/health", response_model=Health, tags=["meta"])
def health(store: Store = Depends(get_store)) -> Health:
    return Health(status="ok", data_source=store.settings.data_source, ready=store.ready,
                  last_error=store.last_error)


@router.get("/summary", response_model=SyncSummary, tags=["meetings"])
def summary(store: Store = Depends(ready_store)) -> SyncSummary:
    return store.summary


@router.get("/meetings", response_model=list[MeetingListItem], tags=["meetings"])
def list_meetings(
    source: SourceFilter | None = Query(None, description="crm = CRM only, calendar = Calendar only, both = matched"),
    has_conflicts: bool | None = None,
    status: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    q: str | None = Query(None, description="Search title, client, company, owner, location, source IDs"),
    store: Store = Depends(ready_store),
) -> list[MeetingListItem]:
    return [
        MeetingListItem.from_meeting(m)
        for m in store.meetings
        if _matches_filters(m, source, has_conflicts, status, date_from, date_to, q)
    ]


@router.get("/meetings/{meeting_id}", response_model=MeetingDetail, tags=["meetings"])
def get_meeting(meeting_id: str, store: Store = Depends(ready_store)) -> MeetingDetail:
    m = store.by_id.get(meeting_id)
    if m is None:
        raise HTTPException(404, detail=f"Meeting {meeting_id} not found")
    raw = {sid: store.events[sid].raw for ids in m.source_ids.values() for sid in ids}
    return MeetingDetail(**m.model_dump(exclude={"sources", "title", "start", "date", "status"}), raw_records=raw)


@router.get("/issues", response_model=list[IssueItem], tags=["data quality"])
def list_issues(
    severity: Severity | None = None,
    source: Source | None = None,
    store: Store = Depends(ready_store),
) -> list[IssueItem]:
    owner = store.meeting_for_record()
    return [
        IssueItem(**i.model_dump(), meeting_id=owner.get(i.source_id))
        for e in store.events.values()
        for i in e.issues
        if (severity is None or i.severity is severity) and (source is None or i.source is source)
    ]


@router.post("/sync", response_model=SyncSummary, tags=["meetings"])
def sync(store: Store = Depends(get_store)) -> SyncSummary:
    try:
        return store.sync()
    except Exception as exc:
        raise HTTPException(502, detail=f"Sync failed: {exc}") from exc
