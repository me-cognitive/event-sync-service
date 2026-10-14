from app.reconcile.scoring import MATCH_THRESHOLD, score_pair
from app.reconcile.similarity import company_tokens, is_refinement, name_localparts


def test_name_localparts_handles_apostrophes():
    assert "kevin.obrien" in name_localparts("Kevin O'Brien")


def test_company_tokens_drop_generic_words():
    assert company_tokens("Meridian Capital") == {"meridian"}
    assert company_tokens("Fund VII Investors") == {"fund", "vii"}


def test_refinement():
    assert is_refinement("Boston Office", "Boston Office - Room 301")
    assert not is_refinement("Boston Office", "Boston Office")


def test_different_dates_never_compared(events):
    assert score_pair(events["CRM-1001"], events["CAL-A2"]) is None


def test_true_match_scores_high_with_reasons(events):
    s = score_pair(events["CRM-1001"], events["CAL-A1"])
    assert s.total >= 0.95
    assert any("david.park@meridiancap.com" in r for r in s.reasons)


def test_same_day_unrelated_meetings_stay_below_threshold(events):
    # CRM-1003 (Lakeshore intro) shares a day with the internal pipeline review and the client reception.
    for cal_id in ("CAL-A7", "CAL-A11"):
        assert score_pair(events["CRM-1003"], events[cal_id]).total < MATCH_THRESHOLD


def test_time_conflict_still_matches_on_other_signals(events):
    # CRM-1016 is 2h off the calendar but client, company and owner all agree.
    assert score_pair(events["CRM-1016"], events["CAL-A17"]).total >= MATCH_THRESHOLD


def test_closest_wrong_pair_stays_below_threshold(events):
    # Same day, shared "Fund VII" and attendee, 5 h apart: the tightest margin in the dataset (0.46).
    assert score_pair(events["CRM-1017"], events["CAL-A19"]).total < MATCH_THRESHOLD
