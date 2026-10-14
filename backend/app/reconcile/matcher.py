"""One-to-one assignment of CRM groups to Calendar groups.

Every CRM/Calendar pair above the threshold is a candidate. Pairs are then
accepted greedily from the highest score down, so each group is used at most
once. With so few same-day candidates, greedy and optimal (Hungarian)
assignment agree, and greedy is easier to explain.
"""

from dataclasses import dataclass

from app.models.normalized import NormalizedEvent
from app.reconcile.duplicates import EventGroup
from app.reconcile.scoring import MATCH_THRESHOLD, Score, score_pair


@dataclass
class Match:
    crm: EventGroup
    calendar: EventGroup
    score: Score
    # The member records that produced `score` (may differ from the canonical ones in a duplicate group).
    crm_record: NormalizedEvent
    calendar_record: NormalizedEvent


def _group_score(crm: EventGroup, cal: EventGroup) -> Match | None:
    """Best score across members, so a duplicate group matches if any of its records does."""
    pairs = [Match(crm, cal, s, a, b) for a in crm.members for b in cal.members if (s := score_pair(a, b))]
    return max(pairs, key=lambda m: m.score.total, default=None)


def match_groups(
    crm_groups: list[EventGroup], cal_groups: list[EventGroup]
) -> tuple[list[Match], list[EventGroup], list[EventGroup]]:
    """Return (matches, unmatched CRM groups, unmatched Calendar groups)."""
    candidates = [
        m
        for crm in crm_groups
        for cal in cal_groups
        if (m := _group_score(crm, cal)) and m.score.total >= MATCH_THRESHOLD
    ]
    candidates.sort(key=lambda m: m.score.total, reverse=True)

    matches: list[Match] = []
    used_crm: set[int] = set()
    used_cal: set[int] = set()
    for m in candidates:
        if id(m.crm) in used_crm or id(m.calendar) in used_cal:
            continue
        matches.append(m)
        used_crm.add(id(m.crm))
        used_cal.add(id(m.calendar))

    unmatched_crm = [g for g in crm_groups if id(g) not in used_crm]
    unmatched_cal = [g for g in cal_groups if id(g) not in used_cal]
    return matches, unmatched_crm, unmatched_cal
