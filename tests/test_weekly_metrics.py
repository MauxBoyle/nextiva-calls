from datetime import date

from nextiva_calls.segments import AgentLookup
from nextiva_calls.weekly_metrics import OTHER, Week, parse_week_start, summarize_week


def row(**updates):
    values = {
        "call_timestamp_ct": "2026-08-17T10:00:00-05:00",
        "from_number_normalized": "15550001",
        "hunt_group": "Membership",
        "routing_mode": "Simultaneous",
        "offered_destinations": "15550101;15550102",
        "possible_answering_destinations": "15550101",
        "maximum_duration_seconds": "75",
        "outcome": "Human answered",
    }
    values.update(updates)
    return values


def lookup():
    return AgentLookup(
        {"15550101": "Alex", "15550102": "Blair"},
        {"0101": frozenset({"Alex"}), "0102": frozenset({"Blair"})},
    )


def test_week_start_requires_monday_and_selects_prior_week():
    week = parse_week_start("2026-08-17")
    assert week == Week(date(2026, 8, 17))
    assert week.end == date(2026, 8, 23)
    assert week.prior.start == date(2026, 8, 10)


def test_summary_counts_outcomes_time_categories_and_agent_reconciliation(tmp_path):
    rows = [
        row(),
        row(
            call_timestamp_ct="2026-08-17T20:00:00-05:00",
            outcome="Unanswered",
            possible_answering_destinations="",
            offered_destinations="9999",
        ),
        row(
            call_timestamp_ct="2026-08-22T11:00:00-05:00",
            offered_destinations="18880000",
            possible_answering_destinations="18880000",
        ),
        row(
            call_timestamp_ct="2026-08-18T09:00:00-05:00",
            possible_answering_destinations="15550101;15550102",
        ),
    ]
    summary = summarize_week(
        rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3"
    )
    assert summary.preliminary
    assert summary.calls == 4
    assert summary.outcomes["Human answered"] == 3
    assert summary.outcomes["Unanswered"] == 1
    assert summary.routing_attempts == 6
    assert summary.time_categories == {
        "Business hours": 2,
        "After hours": 1,
        "Weekend": 1,
        "Holiday": 0,
    }
    assert summary.repeat_callers == {"15550001": 4}
    assert summary.agents["Alex"] == {"offers": 2, "answers": 1, "talk_seconds": 75, "median_seconds": 75}
    assert summary.agents[OTHER] == {"offers": 2, "answers": 2, "talk_seconds": 150, "median_seconds": 75}
    assert summary.weekday_hour_volume[("Mon", 10)] == 1
    assert summary.voicemail_unanswered_by_hour[("Mon", 20)] == 1
    assert summary.routing_attempt_distribution == {1: 2, 2: 2}
    assert summary.anomalies == {"Ambiguous outcomes": 0, "Unknown outcomes": 0, "Unattributed answers": 2}
    assert "misses" not in str(summary.agents).lower()


def test_metadata_gap_makes_week_preliminary(tmp_path):
    import sqlite3

    database = tmp_path / "metadata.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE reports (period_start TEXT, period_end TEXT)")
    connection.execute(
        "INSERT INTO reports VALUES (?, ?)",
        ("2026-08-17T00:00:00-05:00", "2026-08-19T00:00:00-05:00"),
    )
    connection.commit()
    connection.close()
    summary = summarize_week([], Week(date(2026, 8, 17)), lookup(), database)
    assert summary.preliminary


def test_complete_inclusive_metadata_period_is_not_preliminary(tmp_path):
    import sqlite3

    database = tmp_path / "metadata.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE reports (period_start TEXT, period_end TEXT)")
    connection.execute(
        "INSERT INTO reports VALUES (?, ?)",
        ("2026-08-17T00:00:00-05:00", "2026-08-23T00:00:00-05:00"),
    )
    connection.commit()
    connection.close()
    summary = summarize_week([], Week(date(2026, 8, 17)), lookup(), database)
    assert not summary.preliminary
    assert summary.data_through is not None
    assert summary.data_through.isoformat() == "2026-08-24T00:00:00-05:00"
