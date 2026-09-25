from datetime import date

import pytest

from nextiva_calls.holiday_calendar import (
    HolidayCalendarError,
    apply_overrides,
    load_holiday_overrides,
    parse_opm_icalendar,
    resolve_holiday_calendar,
)

ICALENDAR = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260703
SUMMARY:Independence Day (observed)
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260907
SUMMARY:Labor Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261225
SUMMARY:Christmas Day
END:VEVENT
END:VCALENDAR
"""

ANNUAL_ICALENDAR = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260101
SUMMARY:New Year's Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261231
SUMMARY:New Year's Eve
END:VEVENT
END:VCALENDAR
"""


def overrides(tmp_path, text="date,name,status\n"):
    path = tmp_path / "overrides.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_icalendar_preserves_observed_dates_and_names():
    calendar = parse_opm_icalendar(ICALENDAR)
    assert calendar.holiday_on(date(2026, 7, 3)).name == "Independence Day (observed)"
    assert calendar.covers(date(2026, 7, 3), date(2026, 12, 25))


def test_icalendar_combines_multiple_closures_on_the_same_date():
    calendar = parse_opm_icalendar(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250120
SUMMARY:Birthday of Martin Luther King, Jr.
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250120
SUMMARY:Inauguration Day
END:VEVENT
END:VCALENDAR
"""
    )

    assert calendar.holiday_on(date(2025, 1, 20)).name == (
        "Birthday of Martin Luther King, Jr.; Inauguration Day"
    )


def test_overrides_can_close_or_reopen_a_day(tmp_path):
    calendar = parse_opm_icalendar(ICALENDAR)
    changes = load_holiday_overrides(overrides(
        tmp_path,
        "date,name,status\n2026-09-07,Office open,open\n2026-12-24,Winter break,closed\n",
    ))
    result = apply_overrides(calendar, changes)
    assert result.holiday_on(date(2026, 9, 7)) is None
    assert result.holiday_on(date(2026, 12, 24)).name == "Winter break"


@pytest.mark.parametrize("text", [
    "date,name,status\n2026-09-07,,closed\n",
    "date,name,status\n2026-09-07,A,maybe\n",
    "date,name,status\n2026-09-07,A,closed\n2026-09-07,B,open\n",
])
def test_invalid_or_duplicate_overrides_are_rejected(tmp_path, text):
    with pytest.raises(HolidayCalendarError):
        load_holiday_overrides(overrides(tmp_path, text))


def test_cache_is_used_only_when_it_covers_requested_period(tmp_path):
    cache = tmp_path / "opm.ics"
    cache.write_text(ICALENDAR, encoding="utf-8")
    calendar, cached = resolve_holiday_calendar(
        date(2026, 7, 3), date(2026, 12, 25), cache_path=cache,
        overrides_path=overrides(tmp_path), fetcher=lambda _: (_ for _ in ()).throw(HolidayCalendarError("offline")),
    )
    assert cached and calendar.holiday_on(date(2026, 9, 7))
    with pytest.raises(HolidayCalendarError, match="does not cover"):
        resolve_holiday_calendar(
            date(2025, 1, 1), date(2026, 12, 25), cache_path=cache,
            overrides_path=overrides(tmp_path), fetcher=lambda _: (_ for _ in ()).throw(HolidayCalendarError("offline")),
        )


def test_valid_cache_does_not_report_a_refresh_failure(tmp_path):
    cache = tmp_path / "opm.ics"
    cache.write_text(ANNUAL_ICALENDAR, encoding="utf-8")
    failures = []

    _, cached = resolve_holiday_calendar(
        date(2026, 1, 1), date(2026, 12, 31), cache_path=cache,
        overrides_path=overrides(tmp_path), today=date(2026, 9, 24),
        refresh_failure_handler=lambda: failures.append(True),
    )

    assert cached
    assert failures == []


def test_refreshes_immediately_when_no_cache_covers_this_year(tmp_path):
    calls = []
    calendar, cached = resolve_holiday_calendar(
        date(2026, 1, 1), date(2026, 12, 31), cache_path=tmp_path / "opm.ics",
        overrides_path=overrides(tmp_path), today=date(2026, 9, 24),
        fetcher=lambda url: calls.append(url) or ANNUAL_ICALENDAR,
    )
    assert not cached and calls and calendar.covers(date(2026, 1, 1), date(2026, 12, 31))


def test_december_refreshes_until_cache_covers_following_year(tmp_path):
    cache = tmp_path / "opm.ics"
    cache.write_text(ANNUAL_ICALENDAR, encoding="utf-8")
    following_year = ANNUAL_ICALENDAR.replace("END:VCALENDAR", """BEGIN:VEVENT
DTSTART;VALUE=DATE:20270101
SUMMARY:New Year's Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20271231
SUMMARY:New Year's Eve
END:VEVENT
END:VCALENDAR""")
    calls = []
    resolve_holiday_calendar(
        date(2026, 1, 1), date(2026, 12, 31), cache_path=cache,
        overrides_path=overrides(tmp_path), today=date(2026, 12, 15),
        fetcher=lambda url: calls.append(url) or following_year,
    )
    assert len(calls) == 1
    resolve_holiday_calendar(
        date(2026, 1, 1), date(2026, 12, 31), cache_path=cache,
        overrides_path=overrides(tmp_path), today=date(2026, 12, 16),
        fetcher=lambda _: pytest.fail("a multi-year cache should suppress refresh"),
    )
