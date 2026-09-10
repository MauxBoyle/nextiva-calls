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
HUNT_GROUPS = ("Reception", "Membership", "Certification", "Bookstore")
OUTCOME_BUCKETS = ("Yes", "No", "Voicemail", "Unknown / Ambiguous")


@dataclass(frozen=True)
class Week:
    """A seven-day historical reporting period in Central time."""

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
    weekday_hour_volume: dict[tuple[str, int], int]
    voicemail_unanswered_by_hour: dict[tuple[str, int], int]
    weekday_outcomes: dict[tuple[str, str], int]
    routing_attempt_distribution: dict[int, int]
    anomalies: dict[str, int]
    data_through: datetime | None


def week_for(day: date | None = None) -> Week:
    """Return the seven complete Central-time days ending before *day*."""
    if day is None:
        day = datetime.now(CENTRAL_TIME).date()
    return Week(day - timedelta(days=7))


def parse_week_start(value: str) -> Week:
    """Parse an ISO date as the beginning of a seven-day period."""
    try:
        chosen = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("--week-start must be a date in YYYY-MM-DD format") from error
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


def _outcome_bucket(outcome: str) -> str:
    """Map detailed outcomes into the manager chart's displayed buckets."""
    if outcome in {"Human answered", "Forwarded answered"}:
        return "Yes"
    if outcome == "Unanswered":
        return "No"
    if outcome == "Voicemail":
        return "Voicemail"
    return "Unknown / Ambiguous"


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


def _data_through(path: Path, week: Week) -> datetime | None:
    """Return the continuous metadata boundary for this week, in Central time.

    Report periods label whole days inclusively, so a midnight end is converted
    to the following midnight before the coverage intervals are joined.
    """
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(path)
        rows = connection.execute(
            "SELECT period_start, period_end FROM reports "
            "WHERE period_start IS NOT NULL AND period_end IS NOT NULL"
        ).fetchall()
        connection.close()
    except sqlite3.DatabaseError:
        return None
    intervals: list[tuple[datetime, datetime]] = []
    for start, end in rows:
        left, right = _timestamp(start), _timestamp(end)
        if left is None or right is None or right < left:
            continue
        if right.timetz().replace(tzinfo=None) == time.min:
            right += timedelta(days=1)
        left, right = max(left, week.start_at), min(right, week.end_at)
        if left <= right:
            intervals.append((left, right))
    cursor = week.start_at
    for left, right in sorted(intervals):
        if left > cursor:
            break
        cursor = max(cursor, right)
        if cursor >= week.end_at:
            return week.end_at
    return cursor if cursor > week.start_at else None


def summarize_week(
    candidates: list[dict[str, str]],
    week: Week,
    lookup: AgentLookup,
    metadata_path: Path,
    membership_hunt_group: str = "Membership",
) -> WeeklySummary:
    """Summarize Membership candidates in one Central-time seven-day period."""
    in_period = [
        (row, when)
        for row in candidates
        if (when := _timestamp(row.get("call_timestamp_ct", "")))
        and week.start_at <= when < week.end_at
    ]
    selected = [
        (row, when)
        for row, when in in_period
        if row.get("hunt_group") == membership_hunt_group
        or any(
            _agent_for(destination, lookup) is not None
            for destination in row.get("offered_destinations", "").split(";")
            if destination
        )
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
    weekday_hours = Counter((when.strftime("%a"), when.hour) for _, when in selected)
    weekday_outcomes = Counter(
        (when.strftime("%a"), _outcome_bucket(row.get("outcome") or "Unknown"))
        for row, when in selected
    )
    voicemail_unanswered = Counter(
        (when.strftime("%a"), when.hour)
        for row, when in selected
        if row.get("outcome") in {"Voicemail", "Unanswered"}
    )
    callers = Counter(
        row.get("from_number_normalized", "")
        for row, _ in selected
        if row.get("from_number_normalized", "")
    )
    durations = [_duration(row) for row, _ in selected]
    groups: dict[str, Counter[str]] = {group: Counter() for group in HUNT_GROUPS}
    agents: defaultdict[str, Counter[str]] = defaultdict(Counter)
    attempts = 0
    answered_talk = 0
    attempt_distribution: Counter[int] = Counter()
    anomalies: Counter[str] = Counter()
    # The hunt-group table is a small cross-department comparison, so it uses
    # all in-period candidates while the rest of the dashboard stays scoped to
    # Membership candidates.
    for row, _ in in_period:
        group = row.get("hunt_group") or "Unknown"
        if group in groups:
            outcome = row.get("outcome") or "Unknown"
            groups[group]["calls"] += 1
            groups[group][outcome] += 1
            groups[group][row.get("routing_mode") or "Unknown"] += 1
    for row, _ in selected:
        outcome = row.get("outcome") or "Unknown"
        offers = [
            item for item in row.get("offered_destinations", "").split(";") if item
        ]
        duration = _duration(row)
        attempts += len(offers)
        attempt_distribution[len(offers)] += 1
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
            agents[credited].setdefault("durations", []).append(duration)
            answered_talk += duration
            if credited == OTHER:
                anomalies["Unattributed answers"] += 1
        if outcome == "Ambiguous":
            anomalies["Ambiguous outcomes"] += 1
        if outcome == "Unknown":
            anomalies["Unknown outcomes"] += 1
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
        hunt_groups={key: dict(value) for key, value in groups.items()},
        agents={
            key: {
                **{field: value for field, value in values.items() if field != "durations"},
                "median_seconds": int(median(values["durations"])) if values.get("durations") else 0,
            }
            for key, values in sorted(agents.items())
        },
        weekday_hour_volume={
            (day, hour): weekday_hours[(day, hour)]
            for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            for hour in range(24)
        },
        voicemail_unanswered_by_hour={
            (day, hour): voicemail_unanswered[(day, hour)]
            for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            for hour in range(24)
        },
        weekday_outcomes={
            (day, bucket): weekday_outcomes[(day, bucket)]
            for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            for bucket in OUTCOME_BUCKETS
        },
        routing_attempt_distribution=dict(sorted(attempt_distribution.items())),
        anomalies={
            key: anomalies[key]
            for key in ("Ambiguous outcomes", "Unknown outcomes", "Unattributed answers")
        },
        data_through=_data_through(metadata_path, week),
    )
