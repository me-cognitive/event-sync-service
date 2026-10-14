from zoneinfo import ZoneInfo

from app.config import Settings
from app.ingest.loaders import SourceLoader, build_loader
from app.ingest.normalize import safe_normalize
from app.models.normalized import NormalizedEvent, Source


def ingest(settings: Settings, loader: SourceLoader | None = None) -> list[NormalizedEvent]:
    """Load both sources and normalise every record (CRM first, then Calendar)."""
    loader = loader or build_loader(settings)
    tz = ZoneInfo(settings.firm_timezone)
    events: list[NormalizedEvent] = []
    for source, name in ((Source.CRM, settings.crm_file), (Source.CALENDAR, settings.calendar_file)):
        events.extend(safe_normalize(raw, source, tz, settings.firm_email_domain) for raw in loader.load(name))
    return events
