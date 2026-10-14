"""Explainable similarity score between a CRM record and a Calendar event.

Each signal yields a value in [0, 1] and a human-readable reason. The final
score is their weighted sum. Records on different dates are never compared.
"""

from dataclasses import dataclass, field

from app.models.normalized import NormalizedEvent
from app.reconcile.similarity import (
    company_tokens,
    last_name,
    localpart,
    location_similarity,
    name_localparts,
    title_similarity,
    tokens,
)

WEIGHTS: dict[str, float] = {
    "time": 0.30,
    "client": 0.25,
    "company": 0.20,
    "owner": 0.10,
    "title": 0.10,
    "location": 0.05,
}
MATCH_THRESHOLD = 0.55
TIME_DECAY_MINUTES = 180  # start times this far apart contribute nothing
NEUTRAL = 0.5  # used when one side lacks the data to compare


@dataclass
class Score:
    total: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def add(self, signal: str, value: float, reason: str | None = None) -> None:
        self.breakdown[signal] = round(value, 2)
        self.total += WEIGHTS[signal] * value
        if reason:
            self.reasons.append(reason)


def _time(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    if not (crm.start and cal.start):
        s.add("time", NEUTRAL, "CRM has no start time; time not compared")
        return
    minutes = abs((crm.start - cal.start).total_seconds()) / 60
    if minutes == 0:
        s.add("time", 1.0, f"Same start time ({cal.start:%H:%M})")
    else:
        s.add("time", max(0.0, 1 - minutes / TIME_DECAY_MINUTES),
              f"Start times differ by {minutes:.0f} min ({crm.start:%H:%M} vs {cal.start:%H:%M})")


def _client(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    if crm.is_internal:
        s.add("client", 1.0 if cal.is_internal else 0.0,
              "Both are internal meetings" if cal.is_internal else None)
        return
    if not crm.client_name:
        s.add("client", NEUTRAL, "CRM client name unknown; client not compared")
        return
    candidates = name_localparts(crm.client_name)
    for email in cal.attendees:
        if localpart(email) in candidates:
            s.add("client", 1.0, f"Client {crm.client_name} is an attendee ({email})")
            return
    surname = last_name(crm.client_name)
    for email in cal.attendees:
        if surname and surname in localpart(email):
            s.add("client", 0.7, f"Client surname '{surname}' found in attendee {email}")
            return
    s.add("client", 0.0)


def _company(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    if crm.is_internal:
        s.add("company", 1.0 if cal.is_internal else 0.0)
        return
    wanted = company_tokens(crm.client_company)
    if not wanted:
        s.add("company", 0.0)
        return
    title_words = set(tokens(cal.title))
    domain_bases = [d.split(".")[0] for d in cal.external_domains]
    found = {t for t in wanted if t in title_words or any(t in base for base in domain_bases)}
    ratio = len(found) / len(wanted)
    s.add("company", ratio,
          f"Company '{crm.client_company}' matches calendar title/attendee domain" if found else None)


def _owner(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    candidates = name_localparts(crm.owner_name)
    people = [cal.organizer_email, *cal.attendees]
    hit = next((p for p in people if p and localpart(p) in candidates), None)
    if hit:
        role = "organizer" if hit == cal.organizer_email else "attendee"
        s.add("owner", 1.0, f"Owner {crm.owner_name} is the calendar {role}")
    else:
        s.add("owner", 0.0)


def _title(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    sim = title_similarity(crm.title, cal.title)
    s.add("title", sim, f"Title similarity {sim:.0%}" if sim >= 0.5 else None)


def _location(crm: NormalizedEvent, cal: NormalizedEvent, s: Score) -> None:
    if not (crm.location and cal.location):
        s.add("location", NEUTRAL)
        return
    sim = location_similarity(crm.location, cal.location)
    s.add("location", sim, f"Location similarity {sim:.0%}" if sim >= 0.8 else None)


def score_pair(crm: NormalizedEvent, cal: NormalizedEvent) -> Score | None:
    """Score a CRM/Calendar pair, or None if they can't be the same meeting (different dates)."""
    if crm.date is None or crm.date != cal.date:
        return None
    s = Score(reasons=[f"Same date ({crm.date.isoformat()})"])
    for signal in (_time, _client, _company, _owner, _title, _location):
        signal(crm, cal, s)
    s.total = round(s.total, 3)
    return s
