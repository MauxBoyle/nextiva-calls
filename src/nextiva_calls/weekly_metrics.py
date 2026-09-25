"""Pure calculations for the inbound weekly manager report."""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Literal

from nextiva_calls.holiday_calendar import Holiday, HolidayCalendar
from nextiva_calls.reconstruction import CANDIDATE_COLUMNS
from nextiva_calls.segments import CENTRAL_TIME, AgentLookup
from nextiva_calls.storage import StorageError

OUTCOMES = (
    "Confirmed human answered",
    "Connected / unknown attribution",
    "Answered / unattributed",
    "Forwarded / routing only",
    "Voicemail",
    "Unanswered",
    "Unknown",
    "Ambiguous",
)
OTHER = "Other / Unattributed"
OUTCOME_BUCKETS = ("Yes", "No", "Voicemail", "Unknown / Ambiguous")
REPORTING_DEPARTMENTS = ("Membership", "Certification")
COMBINED_SCOPE = "combined"
MINIMUM_INSIGHT_CALLS = 20
MATERIAL_RATE_CHANGE_POINTS = 10

# Nextiva's export labels are not always the labels used in the manager
# report. Keep the original label in the candidate CSV for auditability, then
# translate only when populating the hunt-group comparison.
HUNT_GROUP_ALIASES = {
    "Reception1": "Reception",
    "Book Store": "Bookstore",
}


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
    eligible_inbound_calls: int
    confirmed_known_agent_answers: int
    connected_calls: int
    attribution_coverage_numerator: int
    attribution_coverage_denominator: int
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
    coverage_inferred: bool = False
    holidays: tuple[Holiday, ...] = ()
    calendar_coverage_warning: bool = False


@dataclass(frozen=True)
class WeeklyInsight:
    """A privacy-safe observation calculated before PDF layout begins."""

    kind: Literal["coverage", "significant_change", "low_sample", "no_observation"]
    metric: str | None
    current_count: int | None
    current_denominator: int | None
    prior_count: int | None
    prior_denominator: int | None
    percentage_point_change: float | None
    text: str


def _insight_period(week: Week) -> str:
    return f"{week.start:%b %-d, %Y}–{week.end:%b %-d, %Y}"


def _insight_data_through(summary: WeeklySummary) -> str:
    if summary.data_through is None:
        return "no continuous metadata-confirmed coverage"
    inclusive = summary.data_through - timedelta(minutes=1)
    return f"{inclusive:%b %-d, %Y %-I:%M %p} CT"


def _rate_insight(
    *,
    metric: str,
    current_count: int,
    prior_count: int,
    current: WeeklySummary,
    prior: WeeklySummary,
) -> WeeklyInsight | None:
    """Build one rate observation when its documented threshold is reached."""
    current_total, prior_total = current.eligible_inbound_calls, prior.eligible_inbound_calls
    if not current_total or not prior_total:
        return None
    current_rate, prior_rate = current_count / current_total, prior_count / prior_total
    change = (current_rate - prior_rate) * 100
    if abs(change) < MATERIAL_RATE_CHANGE_POINTS:
        return None
    evidence = (
        f"Current ({_insight_period(current.week)}): {current_count}/{current_total} "
        f"({current_rate:.0%}); prior ({_insight_period(prior.week)}): "
        f"{prior_count}/{prior_total} ({prior_rate:.0%}); change: {change:+.0f} percentage points."
    )
    if current_total >= MINIMUM_INSIGHT_CALLS and prior_total >= MINIMUM_INSIGHT_CALLS:
        return WeeklyInsight(
            kind="significant_change",
            metric=metric,
            current_count=current_count,
            current_denominator=current_total,
            prior_count=prior_count,
            prior_denominator=prior_total,
            percentage_point_change=change,
            text=f"{metric}: significant weekly rate change under the documented rule. {evidence}",
        )
    return WeeklyInsight(
        kind="low_sample",
        metric=metric,
        current_count=current_count,
        current_denominator=current_total,
        prior_count=prior_count,
        prior_denominator=prior_total,
        percentage_point_change=change,
        text=(
            f"{metric}: rate change met the 10-percentage-point threshold, but no significance claim is made "
            f"because at least one week has fewer than {MINIMUM_INSIGHT_CALLS} scoped calls. {evidence}"
        ),
    )


def build_weekly_insights(current: WeeklySummary, prior: WeeklySummary) -> tuple[WeeklyInsight, ...]:
    """Return only documented, privacy-safe observations for the combined scope.

    A preliminary week means metadata coverage is incomplete, so this function
    returns coverage information only and intentionally suppresses rate trends.
    """
    if current.preliminary or prior.preliminary:
        return (
            WeeklyInsight(
                kind="coverage",
                metric=None,
                current_count=None,
                current_denominator=None,
                prior_count=None,
                prior_denominator=None,
                percentage_point_change=None,
                text=(
                    "PRELIMINARY coverage: trend observations are suppressed because metadata coverage is incomplete. "
                    f"Current week data through: {_insight_data_through(current)}. "
                    f"Prior week data through: {_insight_data_through(prior)}."
                ),
            ),
        )
    observations = tuple(
        insight
        for insight in (
            _rate_insight(
                metric="Voicemail rate",
                current_count=current.outcomes["Voicemail"],
                prior_count=prior.outcomes["Voicemail"],
                current=current,
                prior=prior,
            ),
            _rate_insight(
                metric="Confirmed-human-answer rate",
                current_count=current.confirmed_known_agent_answers,
                prior_count=prior.confirmed_known_agent_answers,
                current=current,
                prior=prior,
            ),
        )
        if insight is not None
    )
    if observations:
        return observations
    return (
        WeeklyInsight(
            kind="no_observation",
            metric=None,
            current_count=None,
            current_denominator=None,
            prior_count=None,
            prior_denominator=None,
            percentage_point_change=None,
            text="No automated observations met the documented rules.",
        ),
    )


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


def _category(when: datetime, holiday_calendar: HolidayCalendar | None = None) -> str:
    if holiday_calendar is not None and holiday_calendar.holiday_on(when.date()) is not None:
        return "Holiday"
    if when.weekday() >= 5:
        return "Weekend"
    if time(9) <= when.timetz().replace(tzinfo=None) < time(17):
        return "Business hours"
    return "After hours"


def _agent_for(destination: str, lookup: AgentLookup) -> str | None:
    found = _agent_destination(destination, lookup)
    return found.display_name if found is not None else None


def _agent_destination(destination: str, lookup: AgentLookup):
    """Return an unambiguous agent lookup entry for a destination."""
    if destination in lookup.by_number:
        found = lookup.by_number[destination]
        return found if found.destination_type == "agent" else None
    if len(destination) >= 4:
        matches = lookup.by_extension.get(destination[-4:], frozenset())
        if len(matches) == 1:
            found = next(iter(matches))
            return found if found.destination_type == "agent" else None
    return None


def _scope_departments(scope: str) -> tuple[str, ...]:
    """Return the approved departments represented by a report scope."""
    normalized = scope.casefold()
    if normalized == COMBINED_SCOPE:
        return REPORTING_DEPARTMENTS
    for department in REPORTING_DEPARTMENTS:
        if normalized == department.casefold():
            return (department,)
    raise ValueError("weekly report scope must be combined, Membership, or Certification")


def _matches_scope(
    row: dict[str, str], lookup: AgentLookup, departments: tuple[str, ...], membership_hunt_group: str,
    certification_hunt_group: str,
) -> bool:
    """Whether a call is in a department scope through a group or offered agent."""
    source_groups = {
        "Membership": membership_hunt_group,
        "Certification": certification_hunt_group,
    }
    if row.get("hunt_group") in {source_groups[department] for department in departments}:
        return True
    return any(
        (agent := _agent_destination(destination, lookup)) is not None
        and agent.department in departments
        for destination in row.get("offered_agent_destinations", "").split(";")
        if destination
    )


def _duration(row: dict[str, str]) -> int:
    """Return the available candidate-call duration proxy, or zero if invalid."""
    try:
        return max(0, int(row.get("maximum_duration_seconds", "0") or 0))
    except ValueError:
        return 0


def _outcome_bucket(outcome: str) -> str:
    """Map detailed outcomes into the manager chart's displayed buckets."""
    if outcome == "Confirmed human answered":
        return "Yes"
    if outcome == "Unanswered":
        return "No"
    if outcome == "Voicemail":
        return "Voicemail"
    return "Unknown / Ambiguous"


def _report_hunt_group(hunt_group: str) -> str:
    """Return the manager-report label for a raw Nextiva hunt-group name."""
    return HUNT_GROUP_ALIASES.get(hunt_group, hunt_group)


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


def _inferred_candidate_coverage(
    candidates: list[dict[str, str]], week: Week
) -> datetime | None:
    """Infer coverage only when candidates include every date in ``week``."""
    dates = [
        when.date()
        for row in candidates
        if (when := _timestamp(row.get("call_timestamp_ct", ""))) is not None
    ]
    required_dates = {
        week.start + timedelta(days=offset)
        for offset in range((week.end - week.start).days + 1)
    }
    if not required_dates.issubset(dates):
        return None
    return week.end_at


def summarize_week(
    candidates: list[dict[str, str]],
    week: Week,
    lookup: AgentLookup,
    metadata_path: Path,
    membership_hunt_group: str = "Membership",
    scope: str = "Membership",
    holiday_calendar: HolidayCalendar | None = None,
    closure_dates: frozenset[str] | None = None,
    certification_hunt_group: str = "Certification Hunt Group",
) -> WeeklySummary:
    """Summarize one approved department scope in a Central-time week.

    ``combined`` is a union, so a call that touches both departments is counted
    once. Individual department summaries intentionally include that same call.
    """
    departments = _scope_departments(scope)
    in_period = [
        (row, when)
        for row in candidates
        if (when := _timestamp(row.get("call_timestamp_ct", "")))
        and week.start_at <= when < week.end_at
    ]
    selected = [
        (row, when)
        for row, when in in_period
        if _matches_scope(
            row, lookup, departments, membership_hunt_group, certification_hunt_group
        )
    ]
    outcomes = Counter(
        row.get("outcome", "Unknown")
        if row.get("outcome", "Unknown") in OUTCOMES
        else "Unknown"
        for row, _ in selected
    )
    confirmed_known_agent_answers = outcomes["Confirmed human answered"]
    connected_calls = sum(
        outcomes[outcome]
        for outcome in (
            "Confirmed human answered",
            "Connected / unknown attribution",
            "Answered / unattributed",
            "Ambiguous",
        )
    )
    # Compatibility for third-party callers of the old date-set API.  The CLI
    # and imports use ``holiday_calendar`` exclusively.
    if holiday_calendar is None and closure_dates is not None:
        legacy_entries = {
            date.fromisoformat(value): Holiday(date.fromisoformat(value), "Closed")
            for value in closure_dates
        }
        if legacy_entries:
            holiday_calendar = HolidayCalendar(
                legacy_entries, min(legacy_entries), max(legacy_entries), "legacy"
            )
    categories = Counter(_category(when, holiday_calendar) for _, when in selected)
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
    groups: dict[str, Counter[str]] = {
        group: Counter()
        for group in ("Reception", membership_hunt_group, certification_hunt_group, "Bookstore")
    }
    # Start with approved lookup-listed people. This both retains zero-offer
    # agents and prevents other departments from producing attribution data.
    agents: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for destination in lookup.by_number.values():
        if destination.destination_type == "agent" and destination.department in departments:
            agents[destination.display_name]
    attempts = 0
    answered_talk = 0
    attempt_distribution: Counter[int] = Counter()
    anomalies: Counter[str] = Counter()
    # The hunt-group table is a small cross-department comparison, so it uses
    # all in-period candidates while the rest of the dashboard stays scoped to
    # Membership candidates.
    for row, _ in in_period:
        group = _report_hunt_group(row.get("hunt_group") or "Unknown")
        if group in groups:
            outcome = row.get("outcome") or "Unknown"
            groups[group]["calls"] += 1
            groups[group][outcome] += 1
            groups[group][row.get("routing_mode") or "Unknown"] += 1
    for row, _ in selected:
        outcome = row.get("outcome") or "Unknown"
        offers = [item for item in row.get("offered_agent_destinations", "").split(";") if item]
        duration = _duration(row)
        attempts += len(offers)
        attempt_distribution[len(offers)] += 1
        for destination in offers:
            agent = _agent_destination(destination, lookup)
            if agent is not None and agent.department in departments:
                agents[agent.display_name]["offers"] += 1
        recorded_offers = [
            item
            for item in row.get("recorded_offer_agent_destinations", "").split(";")
            if item
        ]
        for destination in recorded_offers:
            agent = _agent_destination(destination, lookup)
            if agent is not None and agent.department in departments:
                agents[agent.display_name]["recorded_offers"] += 1
        confirmed = [
            item
            for item in row.get("confirmed_answered_agent_destinations", "").split(";")
            if item
        ]
        # Reconstruction only puts known-agent ordinary Yes segments here.  A
        # malformed candidate is still kept conservative: it receives no credit.
        # Both fields are derived from the same normalized segments, but use
        # their intersection defensively so malformed CSV data cannot produce
        # an answer without a recorded-offer denominator.
        answer_agents = {
            _agent_destination(item, lookup)
            for item in set(confirmed).intersection(recorded_offers)
        }
        answer_agents.discard(None)
        answer_agents = {agent for agent in answer_agents if agent.department in departments}
        # Each reconstructed call-agent pair has already been de-duplicated.
        # A simultaneous call may therefore answer for more than one agent.
        for credited in answer_agents:
            agents[credited.display_name]["answers"] += 1
        if outcome == "Confirmed human answered":
            answered_talk += duration
        if len(confirmed) == 1 and len(answer_agents) == 1:
            credited = next(iter(answer_agents))
            agents[credited.display_name]["talk_seconds"] += duration
            agents[credited.display_name].setdefault("durations", []).append(duration)
        forwarded_agents = {
            _agent_destination(item, lookup)
            for item in row.get("forwarded_destinations", "").split(";")
            if item
        }
        forwarded_agents.discard(None)
        for agent in forwarded_agents:
            if agent.department in departments:
                agents[agent.display_name]["forwarded_away"] += 1
        unknown_status_agents = {
            _agent_destination(item, lookup)
            for item in row.get("unknown_status_agent_destinations", "").split(";")
            if item
        }
        unknown_status_agents.discard(None)
        for agent in unknown_status_agents:
            if agent.department in departments:
                agents[agent.display_name]["unknown_status_exclusions"] += 1
        if outcome in {"Connected / unknown attribution", "Answered / unattributed"}:
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
    metadata_complete = _period_is_continuous(metadata_path, week)
    metadata_data_through = _data_through(metadata_path, week)
    inferred_data_through = (
        _inferred_candidate_coverage(candidates, week)
        if not metadata_complete
        else None
    )
    return WeeklySummary(
        week=week,
        preliminary=not metadata_complete and inferred_data_through is None,
        calls=len(selected),
        eligible_inbound_calls=len(selected),
        confirmed_known_agent_answers=confirmed_known_agent_answers,
        connected_calls=connected_calls,
        attribution_coverage_numerator=confirmed_known_agent_answers,
        attribution_coverage_denominator=connected_calls,
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
                **{
                    field: values[field]
                    for field in (
                        "recorded_offers",
                        "answers",
                        "forwarded_away",
                        "unknown_status_exclusions",
                    )
                    if field not in values
                },
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
        data_through=metadata_data_through or inferred_data_through,
        coverage_inferred=inferred_data_through is not None,
        holidays=(
            holiday_calendar.closures_in(week.start, week.end)
            if holiday_calendar is not None
            else ()
        ),
        calendar_coverage_warning=(
            holiday_calendar is None
            or not holiday_calendar.covers(week.start, week.end)
            or holiday_calendar.coverage_end <= week.end
        ),
    )
