from datetime import date

import pytest

from nextiva_calls.segments import AgentLookup, Destination
from nextiva_calls.storage import StorageError
from nextiva_calls.weekly_metrics import (
    OTHER,
    Week,
    load_candidates,
    parse_week_start,
    summarize_week,
    week_for,
)


def lookup():
    alex = Destination("Alex", "Membership", "agent")
    blair = Destination("Blair", "Membership", "agent")
    system = Destination("Hunt group", "Membership", "system")
    return AgentLookup({"15550101": alex, "15550102": blair, "15550999": system}, {"0101": frozenset({alex}), "0102": frozenset({blair}), "0999": frozenset({system})})


def row(**updates):
    values = {"call_timestamp_ct": "2026-08-17T10:00:00-05:00", "from_number_normalized": "15550001", "hunt_group": "Membership", "routing_mode": "Simultaneous", "offered_destinations": "15550101;15550102", "offered_agent_destinations": "15550101;15550102", "confirmed_answered_agent_destinations": "15550101", "forwarded_destinations": "", "system_routing_destinations": "", "unknown_answered_destinations": "", "maximum_duration_seconds": "75", "outcome": "Confirmed human answered"}
    values.update(updates)
    return values


def test_week_helpers():
    assert week_for(date(2026, 8, 20)) == Week(date(2026, 8, 13))
    assert parse_week_start("2026-08-17") == Week(date(2026, 8, 17))


def test_membership_scope_uses_agent_evidence_not_system_or_voicemail(tmp_path):
    rows = [row(hunt_group="Support", offered_agent_destinations="15550101"), row(hunt_group="Support", offered_destinations="15550999", offered_agent_destinations="", system_routing_destinations="15550999", outcome="Forwarded / routing only"), row(hunt_group="Membership", offered_agent_destinations="", outcome="Voicemail")]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.calls == 2
    assert summary.agents["Alex"]["offers"] == 1
    assert OTHER not in summary.agents


def test_only_confirmed_agent_evidence_receives_answer_credit(tmp_path):
    rows = [
        row(),
        row(outcome="Connected / unknown attribution", offered_agent_destinations="", confirmed_answered_agent_destinations="", system_routing_destinations="15550999"),
        row(outcome="Answered / unattributed", offered_agent_destinations="", confirmed_answered_agent_destinations="", unknown_answered_destinations="18880000"),
        row(outcome="Forwarded / routing only", confirmed_answered_agent_destinations="", forwarded_destinations="15550101"),
    ]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.agents["Alex"] == {"offers": 2, "answers": 1, "talk_seconds": 75, "median_seconds": 75}
    assert summary.answered_talk_seconds == 75
    assert summary.anomalies["Unattributed answers"] == 2
    assert summary.outcomes["Confirmed human answered"] == 1
    assert summary.outcomes["Forwarded / routing only"] == 1


def test_new_outcomes_stay_conservative_in_chart(tmp_path):
    rows = [row(outcome=outcome, confirmed_answered_agent_destinations="" if outcome != "Confirmed human answered" else "15550101") for outcome in ("Confirmed human answered", "Forwarded / routing only", "Voicemail", "Unanswered", "Unknown", "Ambiguous", "Connected / unknown attribution", "Answered / unattributed")]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.weekday_outcomes[("Mon", "Yes")] == 1
    assert summary.weekday_outcomes[("Mon", "No")] == 1
    assert summary.weekday_outcomes[("Mon", "Voicemail")] == 1
    assert summary.weekday_outcomes[("Mon", "Unknown / Ambiguous")] == 5


def test_load_candidates_rejects_legacy_header(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("candidate_id,possible_answering_destinations\none,15550101\n", encoding="utf-8")
    with pytest.raises(StorageError, match="header"):
        load_candidates(path)
