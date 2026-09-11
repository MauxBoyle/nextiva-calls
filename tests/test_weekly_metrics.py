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
    values = {"call_timestamp_ct": "2026-08-17T10:00:00-05:00", "from_number_normalized": "15550001", "hunt_group": "Membership", "routing_mode": "Simultaneous", "offered_destinations": "15550101;15550102", "offered_agent_destinations": "15550101;15550102", "recorded_offer_agent_destinations": "15550101;15550102", "confirmed_answered_agent_destinations": "15550101", "forwarded_destinations": "", "unknown_status_agent_destinations": "", "system_routing_destinations": "", "unknown_answered_destinations": "", "maximum_duration_seconds": "75", "outcome": "Confirmed human answered"}
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


def test_forwarded_known_agent_is_selected_and_counts_as_an_offer(tmp_path):
    rows = [
        row(
            hunt_group="Support",
            offered_agent_destinations="15550101",
            confirmed_answered_agent_destinations="",
            forwarded_destinations="15550101",
            outcome="Forwarded / routing only",
        )
    ]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.calls == 1
    assert summary.routing_attempts == 1
    assert summary.agents["Alex"]["offers"] == 1
    assert summary.agents["Alex"].get("answers", 0) == 0
    assert summary.outcomes["Forwarded / routing only"] == 1


def test_only_confirmed_agent_evidence_receives_answer_credit(tmp_path):
    rows = [
        row(),
        row(outcome="Connected / unknown attribution", offered_agent_destinations="", recorded_offer_agent_destinations="", confirmed_answered_agent_destinations="", system_routing_destinations="15550999"),
        row(outcome="Answered / unattributed", offered_agent_destinations="", recorded_offer_agent_destinations="", confirmed_answered_agent_destinations="", unknown_answered_destinations="18880000"),
        row(outcome="Forwarded / routing only", recorded_offer_agent_destinations="", confirmed_answered_agent_destinations="", forwarded_destinations="15550101"),
    ]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.agents["Alex"] == {"offers": 2, "recorded_offers": 1, "answers": 1, "forwarded_away": 1, "unknown_status_exclusions": 0, "talk_seconds": 75, "median_seconds": 75}
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


def test_conservative_answer_kpi_and_attribution_coverage(tmp_path):
    rows = [
        row(),
        row(outcome="Forwarded / routing only", confirmed_answered_agent_destinations=""),
        row(outcome="Voicemail", confirmed_answered_agent_destinations=""),
        row(outcome="Unknown", confirmed_answered_agent_destinations=""),
        row(outcome="Ambiguous", confirmed_answered_agent_destinations=""),
        row(outcome="Connected / unknown attribution", confirmed_answered_agent_destinations=""),
        row(outcome="Answered / unattributed", confirmed_answered_agent_destinations=""),
    ]

    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")

    assert summary.eligible_inbound_calls == 7
    assert summary.confirmed_known_agent_answers == 1
    assert summary.connected_calls == 4
    assert summary.attribution_coverage_numerator == 1
    assert summary.attribution_coverage_denominator == 4
    assert summary.weekday_outcomes[("Mon", "Yes")] == 1


def test_conservative_answer_kpi_handles_zero_denominators(tmp_path):
    summary = summarize_week([], Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")

    assert summary.eligible_inbound_calls == 0
    assert summary.confirmed_known_agent_answers == 0
    assert summary.connected_calls == 0
    assert summary.attribution_coverage_numerator == 0
    assert summary.attribution_coverage_denominator == 0


def test_load_candidates_rejects_legacy_header(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("candidate_id,possible_answering_destinations\none,15550101\n", encoding="utf-8")
    with pytest.raises(StorageError, match="header"):
        load_candidates(path)


def test_recorded_offer_rate_evidence_excludes_forwarded_and_unknown_statuses(tmp_path):
    rows = [
        row(recorded_offer_agent_destinations="15550101;15550102"),
        row(recorded_offer_agent_destinations="15550101", confirmed_answered_agent_destinations="", outcome="Unanswered"),
        row(recorded_offer_agent_destinations="", confirmed_answered_agent_destinations="", forwarded_destinations="15550101", outcome="Forwarded / routing only"),
        row(recorded_offer_agent_destinations="15550101", confirmed_answered_agent_destinations="", unknown_status_agent_destinations="15550101", outcome="Unknown"),
    ]
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.agents["Alex"]["recorded_offers"] == 3
    assert summary.agents["Alex"]["answers"] == 1
    assert summary.agents["Alex"]["forwarded_away"] == 1
    assert summary.agents["Alex"]["unknown_status_exclusions"] == 1
    assert summary.agents["Blair"]["recorded_offers"] == 1
    assert summary.agents["Blair"]["answers"] == 0


def test_lookup_agent_with_no_offers_is_retained(tmp_path):
    summary = summarize_week([], Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    assert summary.agents["Alex"]["recorded_offers"] == 0
    assert summary.agents["Blair"]["recorded_offers"] == 0
