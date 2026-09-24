"""Federal-holiday calendar loading, caching, and local overrides.

The OPM feed already contains observed days (for example, a Friday closure when
the calendar holiday falls on Saturday).  We deliberately use its event date
instead of attempting to reimplement those rules.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

OPM_ICALENDAR_URL = "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/holidays.ics"


class HolidayCalendarError(ValueError):
    """Raised when an authoritative calendar cannot safely be used."""


@dataclass(frozen=True, order=True)
class Holiday:
    """One calendar-day exception to normal business hours."""

    day: date
    name: str
    closed: bool = True


@dataclass(frozen=True)
class HolidayCalendar:
    """Closures indexed by local calendar day, with source coverage bounds."""

    entries: dict[date, Holiday]
    coverage_start: date
    coverage_end: date
    source: str = "OPM"

    def holiday_on(self, day: date) -> Holiday | None:
        """Return a closure for *day*, or ``None`` when the office is open."""
        holiday = self.entries.get(day)
        return holiday if holiday is not None and holiday.closed else None

    def closures_in(self, start: date, end: date) -> tuple[Holiday, ...]:
        """Return closed holidays from an inclusive date interval."""
        return tuple(
            holiday
            for day, holiday in sorted(self.entries.items())
            if start <= day <= end and holiday.closed
        )

    def covers(self, start: date, end: date) -> bool:
        return self.coverage_start <= start and end <= self.coverage_end


def _unfold_ical(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ical_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    return key.split(";", 1)[0].upper(), value.strip()


def parse_opm_icalendar(text: str) -> HolidayCalendar:
    """Parse OPM VEVENT dates and summaries into a validated calendar."""
    events: list[Holiday] = []
    event: dict[str, str] | None = None
    for line in _unfold_ical(text):
        if line == "BEGIN:VEVENT":
            if event is not None:
                raise HolidayCalendarError("OPM calendar contains nested events")
            event = {}
        elif line == "END:VEVENT":
            if event is None:
                raise HolidayCalendarError("OPM calendar has an unmatched event end")
            raw_day, name = event.get("DTSTART"), event.get("SUMMARY")
            if raw_day is None or not name:
                raise HolidayCalendarError("OPM calendar event is missing DTSTART or SUMMARY")
            try:
                day = date.fromisoformat(
                    f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}"
                    if len(raw_day) == 8 and raw_day.isdigit()
                    else raw_day
                )
            except ValueError as error:
                raise HolidayCalendarError("OPM calendar has an invalid DTSTART") from error
            events.append(Holiday(day, name))
            event = None
        elif event is not None and (item := _ical_value(line)) is not None:
            key, value = item
            if key in {"DTSTART", "SUMMARY"}:
                event[key] = value.replace("\\,", ",").replace("\\n", " ")
    if event is not None or not events:
        raise HolidayCalendarError("OPM calendar contains no complete holiday events")
    entries: dict[date, Holiday] = {}
    for holiday in events:
        existing = entries.get(holiday.day)
        if existing is None:
            entries[holiday.day] = holiday
            continue
        # OPM can publish two independent closures on one date (for example,
        # Martin Luther King Jr. Day and Inauguration Day in 2025).  The
        # importer only needs to know that the office is closed, but retaining
        # both names keeps the calendar auditable.
        entries[holiday.day] = Holiday(
            holiday.day, f"{existing.name}; {holiday.name}", closed=True
        )
    return HolidayCalendar(entries, min(entries), max(entries))


def load_holiday_overrides(path: Path) -> tuple[Holiday, ...]:
    """Read ``date,name,status`` local changes without accepting ambiguous rows."""
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error) as error:
        raise HolidayCalendarError("Holiday overrides file could not be read") from error
    if not rows or rows[0] != ["date", "name", "status"]:
        raise HolidayCalendarError("Holiday overrides file must have date,name,status header")
    overrides: list[Holiday] = []
    seen: set[date] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != 3:
            raise HolidayCalendarError(f"Holiday override row {row_number} must have three columns")
        raw_day, raw_name, raw_status = (value.strip() for value in row)
        try:
            day = date.fromisoformat(raw_day)
        except ValueError as error:
            raise HolidayCalendarError(f"Holiday override row {row_number} must use YYYY-MM-DD") from error
        if day.isoformat() != raw_day:
            raise HolidayCalendarError(f"Holiday override row {row_number} must use YYYY-MM-DD")
        status = raw_status.casefold()
        if status not in {"closed", "open"}:
            raise HolidayCalendarError(f"Holiday override row {row_number} status must be closed or open")
        if status == "closed" and not raw_name:
            raise HolidayCalendarError(f"Holiday override row {row_number} needs a closure name")
        if day in seen:
            raise HolidayCalendarError(f"Holiday overrides file contains duplicate date {raw_day}")
        seen.add(day)
        overrides.append(Holiday(day, raw_name, closed=status == "closed"))
    return tuple(overrides)


def apply_overrides(calendar: HolidayCalendar, overrides: tuple[Holiday, ...]) -> HolidayCalendar:
    """Apply closed replacements/additions and explicit open removals."""
    entries = dict(calendar.entries)
    for override in overrides:
        if override.closed:
            entries[override.day] = override
        else:
            entries.pop(override.day, None)
    return HolidayCalendar(entries, calendar.coverage_start, calendar.coverage_end, calendar.source)


def _fetch_opm(url: str) -> str:
    try:
        with urlopen(url, timeout=15) as response:  # noqa: S310 - configured official URL
            return response.read().decode("utf-8")
    except (OSError, UnicodeError, URLError) as error:
        raise HolidayCalendarError("OPM holiday calendar refresh failed") from error


def _write_cache(path: Path, contents: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    except OSError as error:
        raise HolidayCalendarError("OPM holiday calendar cache could not be written") from error


def _load_cache(path: Path) -> HolidayCalendar:
    try:
        return parse_opm_icalendar(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise HolidayCalendarError("OPM holiday calendar cache could not be read") from error


def resolve_holiday_calendar(
    requested_start: date,
    requested_end: date,
    *,
    cache_path: Path,
    overrides_path: Path,
    url: str = OPM_ICALENDAR_URL,
    fetcher: Callable[[str], str] = _fetch_opm,
    today: date | None = None,
    refresh_failure_handler: Callable[[], None] | None = None,
) -> tuple[HolidayCalendar, bool]:
    """Return a usable OPM calendar following the annual refresh policy.

    A cache is used without a request while it covers this year.  Starting on
    December 15, it must also cover next year before requests stop.  The boolean
    is ``True`` when the returned calendar came from cache.  ``refresh_failure_handler``
    is called only when a requested refresh fails, including when a usable cache
    lets processing continue.
    """
    overrides = load_holiday_overrides(overrides_path)
    current_day = today or date.today()
    required_start = date(current_day.year, 1, 1)
    required_end = date(current_day.year + (current_day.month == 12 and current_day.day >= 15), 12, 31)
    cached: HolidayCalendar | None
    try:
        cached = _load_cache(cache_path)
    except HolidayCalendarError:
        cached = None

    # A multi-year cache is deliberately trusted: OPM's calendar data changes
    # rarely, and this avoids a network request every nightly import.
    if cached is not None and cached.covers(required_start, required_end):
        if cached.covers(requested_start, requested_end):
            return apply_overrides(cached, overrides), True

    try:
        contents = fetcher(url)
        calendar = parse_opm_icalendar(contents)
        if not calendar.covers(requested_start, requested_end):
            raise HolidayCalendarError("OPM holiday calendar does not cover the requested report period")
        _write_cache(cache_path, contents)
        return apply_overrides(calendar, overrides), False
    except HolidayCalendarError as refresh_error:
        if refresh_failure_handler is not None:
            refresh_failure_handler()
        if cached is None:
            raise HolidayCalendarError(
                "OPM holiday calendar refresh failed and no valid cached calendar is available"
            ) from refresh_error
        if not cached.covers(requested_start, requested_end):
            raise HolidayCalendarError(
                "OPM holiday calendar refresh failed and cached calendar does not cover the requested report period"
            ) from refresh_error
        return apply_overrides(cached, overrides), True
