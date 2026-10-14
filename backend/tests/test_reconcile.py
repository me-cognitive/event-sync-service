"""Golden tests: reconciling the provided data must produce exactly the outcome documented in docs/PLAN.md §2.1."""

import pytest

from app.models.normalized import Severity
from app.models.unified import FieldStatus
from app.reconcile.duplicates import group_duplicates
from app.reconcile.service import reconcile

EXPECTED_MATCHES = {
    ("CRM-1001",): ("CAL-A1",),
    ("CRM-1002",): ("CAL-A2",),
    ("CRM-1004",): ("CAL-A4",),
    ("CRM-1005",): ("CAL-A6", "CAL-A5"),  # calendar duplicate group, newest first
    ("CRM-1006",): ("CAL-A7",),
    ("CRM-1007",): ("CAL-A8",),
    ("CRM-1008",): ("CAL-A9",),
    ("CRM-1009",): ("CAL-A10",),
    ("CRM-1011",): ("CAL-A12",),
    ("CRM-1012",): ("CAL-A13",),
    ("CRM-1013",): ("CAL-A14",),
    ("CRM-1014",): ("CAL-A15",),
    ("CRM-1015",): ("CAL-A16",),
    ("CRM-1016",): ("CAL-A17",),
    ("CRM-1017",): ("CAL-A20",),
    ("CRM-1018",): ("CAL-A21",),
    ("CRM-1019",): ("CAL-A22",),
}
EXPECTED_CRM_ONLY = {"CRM-1003", "CRM-1010", "CRM-1020"}
EXPECTED_CAL_ONLY = {"CAL-A3", "CAL-A11", "CAL-A18", "CAL-A19"}


@pytest.fixture(scope="module")
def meetings(events):
    return reconcile(list(events.values()))


@pytest.fixture(scope="module")
def by_crm(meetings):
    return {m.source_ids["crm"][0]: m for m in meetings if m.source_ids.get("crm")}


def test_pairings(meetings):
    matched = {
        tuple(m.source_ids["crm"]): tuple(m.source_ids["calendar"])
        for m in meetings if len(m.sources) == 2
    }
    assert matched == EXPECTED_MATCHES


def test_unmatched(meetings):
    crm_only = {i for m in meetings if m.sources == ["crm"] for i in m.source_ids["crm"]}
    cal_only = {i for m in meetings if m.sources == ["calendar"] for i in m.source_ids["calendar"]}
    assert crm_only == EXPECTED_CRM_ONLY
    assert cal_only == EXPECTED_CAL_ONLY
    assert len(meetings) == len(EXPECTED_MATCHES) + len(EXPECTED_CRM_ONLY) + len(EXPECTED_CAL_ONLY)


def test_every_source_record_accounted_for_once(meetings, events):
    seen = [i for m in meetings for ids in m.source_ids.values() for i in ids]
    assert sorted(seen) == sorted(events)


def test_recurring_instances_not_treated_as_duplicates(events):
    groups = group_duplicates([e for e in events.values() if e.source == "calendar"])
    assert all(not g.superseded for g in groups if g.canonical.source_id in ("CAL-A3", "CAL-A18"))


def test_duplicate_group(by_crm):
    m = by_crm["CRM-1005"]
    assert m.duplicates[0].canonical_id == "CAL-A6"
    assert m.duplicates[0].superseded_ids == ["CAL-A5"]
    start_conflict = next(c for c in m.conflicts if c.field == "start")
    assert "CAL-A5" in start_conflict.message


def conflict_fields(meeting):
    return {c.field for c in meeting.conflicts}


@pytest.mark.parametrize(
    "crm_id, fields",
    [
        ("CRM-1002", {"location"}),  # In-Person vs Zoom (meeting_type folded into the location conflict)
        ("CRM-1004", {"start"}),  # DST: 14:00 vs 15:00 EDT
        ("CRM-1009", {"status"}),  # Cancelled vs confirmed
        ("CRM-1016", {"start"}),  # 13:00 vs 15:00
        ("CRM-1001", set()),  # cosmetic location difference + completed/confirmed are not conflicts
        ("CRM-1011", set()),  # location refinement
        ("CRM-1017", set()),  # "The Palm - DC" ~ "The Palm Restaurant"
    ],
)
def test_conflicts(by_crm, crm_id, fields):
    assert conflict_fields(by_crm[crm_id]) == fields


def test_dst_hint(by_crm):
    message = by_crm["CRM-1004"].conflicts[0].message
    assert "daylight saving" in message


def test_cancellation_wins_and_is_critical(by_crm):
    m = by_crm["CRM-1009"]
    assert m.status == "cancelled"
    assert m.conflicts[0].severity is Severity.ERROR


def test_field_provenance(by_crm):
    m = by_crm["CRM-1007"]
    assert m.fields["start"].status is FieldStatus.FILLED  # CRM had no time
    assert m.fields["start"].source == "calendar" and m.start.hour == 15
    assert m.fields["location"].status is FieldStatus.FILLED
    assert m.fields["client_name"].source == "crm"

    loc = by_crm["CRM-1011"].fields["location"]
    assert loc.status is FieldStatus.REFINEMENT and loc.value == "NYC Office - 12th Floor"
    assert (loc.crm, loc.calendar) == ("NYC Office", "NYC Office - 12th Floor")


def test_issues_carried_onto_meeting(by_crm):
    codes = {i.code for i in by_crm["CRM-1008"].issues}
    assert "malformed_date_repaired" in codes


def test_meeting_ids_stable(events):
    first = [m.id for m in reconcile(list(events.values()))]
    second = [m.id for m in reconcile(list(reversed(list(events.values()))))]
    assert sorted(first) == sorted(second)


def test_match_reports_which_records_were_scored(by_crm):
    # The duplicate group matched via its best member (A5, same 11:00 start) while A6 supplies the values.
    m = by_crm["CRM-1005"]
    assert m.match.scored_pair == {"crm": "CRM-1005", "calendar": "CAL-A5"}
    assert by_crm["CRM-1001"].match.scored_pair == {"crm": "CRM-1001", "calendar": "CAL-A1"}
