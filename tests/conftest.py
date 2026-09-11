"""Shared test fixtures."""

from datetime import date

import pytest

from nextiva_calls.holiday_calendar import HolidayCalendar


@pytest.fixture(autouse=True)
def _test_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "test.log"))


@pytest.fixture(autouse=True)
def _offline_opm_refresh(monkeypatch):
    """Keep unrelated importer/PDF tests deterministic and network-free."""
    calendar = HolidayCalendar({}, date(2020, 1, 1), date(2030, 12, 31), "test")

    def resolve(*_args, **_kwargs):
        return calendar, False

    monkeypatch.setattr("nextiva_calls.importer.resolve_holiday_calendar", resolve)
    monkeypatch.setattr("nextiva_calls.app.resolve_holiday_calendar", resolve)
