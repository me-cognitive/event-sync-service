from datetime import date, datetime, time

import pytest

from app.ingest.parsing import (
    classify_location,
    fix_mojibake,
    is_placeholder,
    normalize_email,
    parse_date,
    parse_time,
    parse_timestamp,
)
from app.models.normalized import LocationKind
from tests.conftest import NY


@pytest.mark.parametrize(
    "raw, expected, repaired",
    [
        ("2025-03-15", date(2025, 3, 15), False),
        ("03-15/2025", date(2025, 3, 15), True),  # CRM-1008
        ("2025/03/15", date(2025, 3, 15), True),
        ("15-15-2025", None, False),  # impossible month
        ("garbage", None, False),
        (None, None, False),
    ],
)
def test_parse_date(raw, expected, repaired):
    assert parse_date(raw) == (expected, repaired)


def test_parse_time():
    assert parse_time("14:00") == time(14, 0)
    assert parse_time(None) is None  # CRM-1007
    assert parse_time("2pm") is None


def test_naive_timestamp_is_firm_local():
    value, had_offset, strict = parse_timestamp("2025-03-10T14:00:00", NY)
    assert value == datetime(2025, 3, 10, 14, 0, tzinfo=NY)
    assert (had_offset, strict) == (False, True)


def test_utc_timestamp_respects_dst():
    # CAL-A4: 19:00Z on 2025-03-13 is 15:00 EDT (DST began 2025-03-09), not 14:00.
    value, had_offset, _ = parse_timestamp("2025-03-13T19:00:00Z", NY)
    assert had_offset
    assert (value.hour, value.tzname()) == (15, "EDT")


def test_timestamp_without_seconds_is_flagged_nonstandard():
    value, _, strict = parse_timestamp("2025-03-14T20:00", NY)  # CAL-A11
    assert value.hour == 20 and not strict


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Sarah.Chen@firma.com", ("sarah.chen@firma.com", False)),
        ("raj.patel[at]atlasvc.com", ("raj.patel@atlasvc.com", True)),  # CAL-A16
        ("external-guests", (None, False)),  # CAL-A20
        ("", (None, False)),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_fix_mojibake():
    raw = "CANCELLED \u00e2\u20ac\u201d rescheduled"  # CRM-1009 as stored in the file
    assert fix_mojibake(raw) == ("CANCELLED \u2014 rescheduled", True)
    assert fix_mojibake("plain text") == ("plain text", False)


def test_placeholder():
    assert is_placeholder("Multiple")  # CRM-1017
    assert not is_placeholder("David Park")


@pytest.mark.parametrize(
    "location, meeting_type, kind",
    [
        ("Zoom - https://zoom.us/j/1", None, LocationKind.VIRTUAL),
        ("Microsoft Teams", None, LocationKind.VIRTUAL),
        ("NYC Office - 30th Floor", "In-Person", LocationKind.PHYSICAL),
        (None, "Virtual", LocationKind.VIRTUAL),
        (None, "Internal", LocationKind.UNKNOWN),
        (None, None, LocationKind.UNKNOWN),
    ],
)
def test_classify_location(location, meeting_type, kind):
    assert classify_location(location, meeting_type) == kind
