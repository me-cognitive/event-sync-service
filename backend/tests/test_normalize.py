"""Normalisation of the real provided data files: every known data problem is repaired and reported."""

from app.models.normalized import IssueCode, LocationKind


def codes(event):
    return {i.code for i in event.issues}


def test_all_records_loaded(events):
    assert sum(1 for k in events if k.startswith("CRM-")) == 20
    assert sum(1 for k in events if k.startswith("CAL-")) == 22
    assert not any(IssueCode.UNPARSEABLE_RECORD in codes(e) for e in events.values())


def test_malformed_crm_date_repaired(events):
    e = events["CRM-1008"]
    assert e.date.isoformat() == "2025-03-15" and e.start.hour == 12
    assert IssueCode.MALFORMED_DATE_REPAIRED in codes(e)


def test_missing_crm_time_keeps_date(events):
    e = events["CRM-1007"]
    assert e.date.isoformat() == "2025-03-18"
    assert e.start is None and not e.time_known
    assert IssueCode.MISSING_TIME in codes(e)


def test_utf8_preserved_regardless_of_platform_encoding(events):
    # The file holds a real UTF-8 em dash; decoding with Windows' cp1252 default would give "\u00e2\u20ac\u201d".
    e = events["CRM-1009"]
    assert "\u2014" in e.notes and "\u00e2" not in e.notes
    assert not e.issues
    assert e.status == "cancelled"


def test_placeholder_client(events):
    e = events["CRM-1017"]
    assert e.client_name is None and e.client_company == "Fund VII Investors"
    assert IssueCode.PLACEHOLDER_VALUE in codes(e)


def test_utc_calendar_event_converted(events):
    e = events["CAL-A4"]
    assert e.start.hour == 15 and e.start.tzname() == "EDT"
    assert IssueCode.TIMEZONE_CONVERTED in codes(e)


def test_attendee_repairs(events):
    a16 = events["CAL-A16"]
    assert "raj.patel@atlasvc.com" in a16.attendees
    assert IssueCode.EMAIL_REPAIRED in codes(a16)

    a20 = events["CAL-A20"]
    assert a20.unparsed_attendees == ["external-guests"]
    assert IssueCode.INVALID_ATTENDEE in codes(a20)


def test_sparse_calendar_event(events):
    e = events["CAL-A11"]
    assert {IssueCode.MISSING_ATTENDEES, IssueCode.MISSING_LOCATION, IssueCode.NONSTANDARD_TIMESTAMP} <= codes(e)
    assert e.end.hour == 20


def test_internal_and_external_detection(events):
    assert events["CAL-A3"].is_internal and events["CAL-A3"].is_recurring
    assert events["CRM-1006"].is_internal
    assert events["CAL-A1"].external_domains == ["meridiancap.com"]
    assert not events["CAL-A1"].is_internal


def test_location_kind_exposes_type_conflict(events):
    # CRM-1002 says in-person, CAL-A2 says Zoom: both sides normalised so the matcher can flag it.
    assert events["CRM-1002"].location_kind == LocationKind.PHYSICAL
    assert events["CAL-A2"].location_kind == LocationKind.VIRTUAL
