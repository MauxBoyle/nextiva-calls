"""Pure calculations for the inbound weekly manager report."""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import median

from nextiva_calls.reconstruction import CANDIDATE_COLUMNS
from nextiva_calls.segments import CENTRAL_TIME, CLOSURE_DATES_2026, AgentLookup
from nextiva_calls.storage import StorageError

OUTCOMES = (
    "Human answered",
    "Forwarded answered",
    "Voicemail",
    "Unanswered",
    "Unknown",
    "Ambiguous",
)
OTHER = "Other / Unattributed"


@dataclass(frozen=True)
class Week:
    """A Monday-through-Sunday reporting period in Central time."""

    start: date

    @property
    def end(self) -> date:
        return self.start + timedelta(days=6)

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=CENTRAL_TIME)

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(days=7)

    @property
    def prior(self) -> Week:
        return Week(self.start - timedelta(days=7))


@dataclass(frozen=True)
class DurationStats:
    count: int
    total_seconds: int
    average_seconds: float
    median_seconds: float
    maximum_seconds: int


@dataclass(frozen=True)
class WeeklySummary:
    week: Week
    preliminary: bool
    calls: int
    outcomes: dict[str, int]
    routing_attempts: int
    durations: DurationStats
    answered_talk_seconds: int
    time_categories: dict[str, int]
    weekday_volume: dict[str, int]
    hour_volume: dict[int, int]
    repeat_callers: dict[str, int]
    hunt_groups: dict[str, dict[str, int]]
    agents: dict[str, dict[str, int]]


def week_for(day: date | None = None) -> Week:
    """Return the Central-time Monday containing *day* (or today)."""
    if day is None:
        day = datetime.now(CENTRAL_TIME).date()
    return Week(day - timedelta(days=day.weekday()))


def parse_week_start(value: str) -> Week:
    """Parse an ISO date and reject dates that are not Mondays."""
    try:
        chosen = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("--week-start must be a date in YYYY-MM-DD format") from error
    if chosen.weekday() != 0:
        raise ValueError("--week-start must be a Monday")
    return Week(chosen)


def load_candidates(path: Path) -> list[dict[str, str]]:
    """Load reconstructed candidate calls with their public CSV header checked."""
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != list(CANDIDATE_COLUMNS):
                raise StorageError("Candidate calls CSV has an unexpected header")
            return list(reader)
    except StorageError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise StorageError("Candidate calls CSV could not be read") from error


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CENTRAL_TIME)
    return parsed.astimezone(CENTRAL_TIME)


def _category(when: datetime) -> str:
    if when.date().isoformat() in CLOSURE_DATES_2026:
        return "Holiday"
    if when.weekday() >= 5:
        return "Weekend"
    if time(9) <= when.timetz().replace(tzinfo=None) < time(17):
        return "Business hours"
    return "After hours"


def _agent_for(destination: str, lookup: AgentLookup) -> str | None:
    if destination in lookup.by_number:
        return lookup.by_number[destination]
    if len(destination) >= 4:
        matches = lookup.by_extension.get(destination[-4:], frozenset())
        if len(matches) == 1:
            return next(iter(matches))
    return None


def _duration(row: dict[str, str]) -> int:
    """Return the available candidate-call duration proxy, or zero if invalid."""
    try:
        return max(0, int(row.get("maximum_duration_seconds", "0") or 0))
    except ValueError:
        return 0


def _period_is_continuous(path: Path, week: Week) -> bool:
    """Check that valid metadata report periods cover every instant of a week."""
    if not path.exists():
        return False
    try:
        connection = sqlite3.connect(path)
        rows = connection.execute(
            "SELECT period_start, period_end FROM reports "
            "WHERE period_start IS NOT NULL AND period_end IS NOT NULL"
        ).fetchall()
        connection.close()
    except sqlite3.DatabaseError:
        return False
    intervals: list[tuple[datetime, datetime]] = []
    for start, end in rows:
        left, right = _timestamp(start), _timestamp(end)
        if left is None or right is None or right < left:
            continue
        # Nextiva's labelled periods are dates and are stored at midnight.  The
        # end date is inclusive, so make it an exclusive next-day boundary.
        if right.timetz().replace(tzinfo=None) == time.min:
            right += timedelta(days=1)
        left, right = max(left, week.start_at), min(right, week.end_at)
        if left <= right:
            intervals.append((left, right))
    if not intervals:
        return False
    intervals.sort()
    cursor = week.start_at
    for left, right in intervals:
        if left > cursor:
            return False
        if right > cursor:
            cursor = right
        if cursor >= week.end_at:
            return True
    return cursor >= week.end_at


def summarize_week(
    candidates: list[dict[str, str]],
    week: Week,
    lookup: AgentLookup,
    metadata_path: Path,
) -> WeeklySummary:
    """Summarize candidates in one Central Monday--Sunday reporting week."""
    selected = [
        (row, when)
        for row in candidates
        if (when := _timestamp(row.get("call_timestamp_ct", "")))
        and week.start_at <= when < week.end_at
    ]
    outcomes = Counter(
        row.get("outcome", "Unknown")
        if row.get("outcome", "Unknown") in OUTCOMES
        else "Unknown"
        for row, _ in selected
    )
    categories = Counter(_category(when) for _, when in selected)
    weekdays = Counter(when.strftime("%a") for _, when in selected)
    hours = Counter(when.hour for _, when in selected)
    callers = Counter(
        row.get("from_number_normalized", "")
        for row, _ in selected
        if row.get("from_number_normalized", "")
    )
    durations = [_duration(row) for row, _ in selected]
    groups: defaultdict[str, Counter[str]] = defaultdict(Counter)
    agents: defaultdict[str, Counter[str]] = defaultdict(Counter)
    attempts = 0
    answered_talk = 0
    for row, _ in selected:
        group = row.get("hunt_group") or "Unknown"
        outcome = row.get("outcome") or "Unknown"
        groups[group]["calls"] += 1
        groups[group][outcome] += 1
        groups[group][row.get("routing_mode") or "Unknown"] += 1
        offers = [
            item for item in row.get("offered_destinations", "").split(";") if item
        ]
        duration = _duration(row)
        attempts += len(offers)
        for destination in offers:
            agent = _agent_for(destination, lookup) or OTHER
            agents[agent]["offers"] += 1
        positives = [
            item
            for item in row.get("possible_answering_destinations", "").split(";")
            if item
        ]
        answer_agents = {_agent_for(item, lookup) for item in positives}
        answer_agents.discard(None)
        if positives:
            credited = (
                next(iter(answer_agents))
                if len(answer_agents) == 1 and len(positives) == 1
                else OTHER
            )
            agents[credited]["answers"] += 1
            agents[credited]["talk_seconds"] += duration
            answered_talk += duration
    duration_stats = DurationStats(
        count=len(durations),
        total_seconds=sum(durations),
        average_seconds=(sum(durations) / len(durations)) if durations else 0,
        median_seconds=float(median(durations)) if durations else 0,
        maximum_seconds=max(durations, default=0),
    )
    return WeeklySummary(
        week=week,
        preliminary=not _period_is_continuous(metadata_path, week),
        calls=len(selected),
        outcomes={key: outcomes[key] for key in OUTCOMES},
        routing_attempts=attempts,
        durations=duration_stats,
        answered_talk_seconds=answered_talk,
        time_categories={
            key: categories[key]
            for key in ("Business hours", "After hours", "Weekend", "Holiday")
        },
        weekday_volume={
            key: weekdays[key]
            for key in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        },
        hour_volume={key: hours[key] for key in range(24)},
        repeat_callers={key: value for key, value in callers.items() if value > 1},
        hunt_groups={key: dict(value) for key, value in sorted(groups.items())},
        agents={key: dict(value) for key, value in sorted(agents.items())},
    )
