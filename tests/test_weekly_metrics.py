from dataclasses import replace
from datetime import date

import pytest

from nextiva_calls.segments import AgentLookup, Destination
from nextiva_calls.storage import StorageError
from nextiva_calls.weekly_metrics import (
    OTHER,
    Week,
    build_weekly_insights,
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


def test_configured_closure_is_a_holiday_not_business_hours(tmp_path):
    summary = summarize_week(
        [row(call_timestamp_ct="2026-08-17T10:00:00-05:00")],
        Week(date(2026, 8, 17)),
        lookup(),
        tmp_path / "missing.sqlite3",
        closure_dates=frozenset({"2026-08-17"}),
    )
    assert summary.time_categories["Holiday"] == 1
    assert summary.time_categories["Business hours"] == 0


def test_certification_scope_uses_configured_hunt_group(tmp_path):
    certification = Destination("Casey", "Certification", "agent")
    scoped_lookup = AgentLookup({"15550202": certification}, {"0202": frozenset({certification})})
    values = row(hunt_group="Certification Hunt Group", offered_agent_destinations="")

    summary = summarize_week(
        [values], Week(date(2026, 8, 17)), scoped_lookup, tmp_path / "missing.sqlite3",
        scope="Certification",
    )

    assert summary.calls == 1
    assert summary.hunt_groups["Certification Hunt Group"]["calls"] == 1


@pytest.mark.parametrize(
    ("raw_hunt_group", "report_hunt_group"),
    [
        ("Reception1", "Reception"),
        ("Book Store", "Bookstore"),
    ],
)
def test_hunt_group_comparison_normalizes_nextiva_export_aliases(
    tmp_path, raw_hunt_group, report_hunt_group
):
    summary = summarize_week(
        [row(hunt_group=raw_hunt_group)],
        Week(date(2026, 8, 17)),
        lookup(),
        tmp_path / "missing.sqlite3",
    )

    assert summary.hunt_groups[report_hunt_group]["calls"] == 1


def test_answered_talk_time_includes_unattributed_confirmed_answer(tmp_path):
    values = row(
        confirmed_answered_agent_destinations="",
        recorded_offer_agent_destinations="",
        offered_agent_destinations="",
        maximum_duration_seconds="90",
    )

    summary = summarize_week([values], Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")

    assert summary.answered_talk_seconds == 90


def test_candidate_date_range_is_transparently_inferred_as_coverage(tmp_path):
    week = Week(date(2026, 8, 17))
    candidates = [
        row(call_timestamp_ct=f"2026-08-{day:02d}T10:00:00-05:00")
        for day in range(17, 24)
    ]

    summary = summarize_week(candidates, week, lookup(), tmp_path / "missing.sqlite3")

    assert not summary.preliminary
    assert summary.coverage_inferred
    assert summary.data_through == week.end_at


def test_candidate_coverage_is_preliminary_when_an_interior_date_is_missing(tmp_path):
    week = Week(date(2026, 8, 17))
    candidates = [
        row(call_timestamp_ct=f"2026-08-{day:02d}T10:00:00-05:00")
        for day in (17, 18, 19, 21, 22, 23)
    ]

    summary = summarize_week(candidates, week, lookup(), tmp_path / "missing.sqlite3")

    assert summary.preliminary
    assert not summary.coverage_inferred


def insight_summary(tmp_path, *, calls, voicemail, confirmed, preliminary=False):
    """Small complete summary for testing insight rules without PDF layout."""
    base = summarize_week([], Week(date(2026, 8, 17)), lookup(), tmp_path / "missing.sqlite3")
    return replace(
        base,
        preliminary=preliminary,
        calls=calls,
        eligible_inbound_calls=calls,
        confirmed_known_agent_answers=confirmed,
        outcomes={**base.outcomes, "Voicemail": voicemail},
    )


def test_preliminary_insights_show_coverage_only(tmp_path):
    current = insight_summary(tmp_path, calls=30, voicemail=15, confirmed=3, preliminary=True)
    prior = insight_summary(tmp_path, calls=30, voicemail=1, confirmed=20)

    insights = build_weekly_insights(current, prior)

    assert [insight.kind for insight in insights] == ["coverage"]
    assert "PRELIMINARY coverage" in insights[0].text
    assert "Voicemail rate" not in insights[0].text


@pytest.mark.parametrize(
    ("current_voicemail", "prior_voicemail", "change"),
    [(3, 1, "+10"), (1, 3, "-10")],
)
def test_voicemail_rate_change_at_threshold_is_significant(tmp_path, current_voicemail, prior_voicemail, change):
    current = insight_summary(tmp_path, calls=20, voicemail=current_voicemail, confirmed=5)
    prior = insight_summary(tmp_path, calls=20, voicemail=prior_voicemail, confirmed=5)

    insight = build_weekly_insights(current, prior)[0]

    assert insight.kind == "significant_change"
    assert insight.metric == "Voicemail rate"
    assert "Current (Aug 17, 2026–Aug 23, 2026):" in insight.text
    assert "3/20" in insight.text or "1/20" in insight.text
    assert f"{change} percentage points" in insight.text


def test_confirmed_human_answer_rate_is_compared(tmp_path):
    current = insight_summary(tmp_path, calls=20, voicemail=2, confirmed=15)
    prior = insight_summary(tmp_path, calls=20, voicemail=2, confirmed=5)

    insights = build_weekly_insights(current, prior)

    assert [insight.metric for insight in insights] == ["Confirmed-human-answer rate"]
    assert insights[0].current_count == 15
    assert insights[0].prior_count == 5


def test_low_sample_change_shows_counts_without_significance_claim(tmp_path):
    current = insight_summary(tmp_path, calls=19, voicemail=10, confirmed=5)
    prior = insight_summary(tmp_path, calls=20, voicemail=0, confirmed=5)

    insight = build_weekly_insights(current, prior)[0]

    assert insight.kind == "low_sample"
    assert "10/19" in insight.text
    assert "no significance claim is made" in insight.text


def test_no_insight_message_when_no_documented_rule_is_met(tmp_path):
    current = insight_summary(tmp_path, calls=30, voicemail=5, confirmed=10)
    prior = insight_summary(tmp_path, calls=30, voicemail=4, confirmed=11)

    insights = build_weekly_insights(current, prior)

    assert [insight.kind for insight in insights] == ["no_observation"]
    assert insights[0].text == "No automated observations met the documented rules."


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


def test_combined_scope_counts_cross_department_call_once_and_keeps_department_outcomes(tmp_path):
    membership = Destination("Alex", "Membership", "agent")
    certification = Destination("Casey", "Certification", "agent")
    other = Destination("Devon", "Support", "agent")
    combined_lookup = AgentLookup(
        {"15550101": membership, "15550202": certification, "15550303": other},
        {"0101": frozenset({membership}), "0202": frozenset({certification}), "0303": frozenset({other})},
    )
    rows = [
        row(offered_agent_destinations="15550101;15550202", offered_destinations="15550101;15550202", recorded_offer_agent_destinations="15550101;15550202", confirmed_answered_agent_destinations="15550101", outcome="Confirmed human answered"),
        row(hunt_group="Certification", offered_agent_destinations="15550202", offered_destinations="15550202", recorded_offer_agent_destinations="15550202", confirmed_answered_agent_destinations="", outcome="Voicemail"),
        row(hunt_group="Support", offered_agent_destinations="15550303", offered_destinations="15550303", recorded_offer_agent_destinations="15550303", confirmed_answered_agent_destinations="", outcome="Unanswered"),
    ]
    week = Week(date(2026, 8, 17))
    combined = summarize_week(rows, week, combined_lookup, tmp_path / "missing.sqlite3", scope="combined")
    membership_summary = summarize_week(rows, week, combined_lookup, tmp_path / "missing.sqlite3", scope="Membership")
    certification_summary = summarize_week(rows, week, combined_lookup, tmp_path / "missing.sqlite3", scope="Certification")

    assert combined.calls == 2
    assert combined.time_categories["Business hours"] == 2
    assert combined.weekday_hour_volume[("Mon", 10)] == 2
    assert membership_summary.outcomes["Confirmed human answered"] == 1
    assert certification_summary.outcomes == {**{key: 0 for key in certification_summary.outcomes}, "Confirmed human answered": 1, "Voicemail": 1}
    assert set(combined.agents) == {"Alex", "Casey"}


def test_certification_scope_can_be_empty_without_agent_or_coverage_values(tmp_path):
    membership = Destination("Alex", "Membership", "agent")
    certification = Destination("Casey", "Certification", "agent")
    scoped_lookup = AgentLookup(
        {"15550101": membership, "15550202": certification},
        {"0101": frozenset({membership}), "0202": frozenset({certification})},
    )
    summary = summarize_week([row()], Week(date(2026, 8, 17)), scoped_lookup, tmp_path / "missing.sqlite3", scope="Certification")

    assert summary.calls == 0
    assert summary.attribution_coverage_denominator == 0
    assert set(summary.agents) == {"Casey"}
