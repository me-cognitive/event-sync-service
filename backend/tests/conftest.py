from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.ingest.pipeline import ingest

NY = ZoneInfo("America/New_York")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(data_source="file")


@pytest.fixture(scope="session")
def events(settings):
    return {e.source_id: e for e in ingest(settings)}
