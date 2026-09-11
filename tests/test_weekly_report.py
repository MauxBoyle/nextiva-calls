import csv
from dataclasses import replace
from datetime import date
from pathlib import Path

from nextiva_calls import app
from nextiva_calls.reconstruction import CANDIDATE_COLUMNS
from nextiva_calls.records import CSV_COLUMNS
from nextiva_calls.segments import AgentLookup, Destination, load_agent_lookup
from nextiva_calls.storage import save_analysis
from nextiva_calls.weekly_metrics import Week, summarize_week
from nextiva_calls.weekly_report import (
    StackedBarChart,
    _percentage,
    render_weekly_report,
)


def test_weekly_report_creates_preliminary_pdf(monkeypatch, tmp_path):
    raw = tmp_path / "calls.csv"
    candidates = raw.with_suffix(".candidate-calls.csv")
    lookup = tmp_path / "agents.csv"
    output = tmp_path / "manager.pdf"
    lookup.write_text("phone_number,display_name,department,destination_type\n15550101,Alex,Membership,agent\n", encoding="utf-8")
    values = {column: "" for column in CANDIDATE_COLUMNS}
    values.update(
        {
            "candidate_id": "one",
            "hunt_group": "Support",
            "call_timestamp_ct": "2026-08-17T10:00:00-05:00",
            "from_number_normalized": "15550001",
            "routing_mode": "Sequential",
            "offered_destinations": "15550101",
            "unique_routing_attempts": "1",
            "offered_agent_destinations": "15550101",
            "confirmed_answered_agent_destinations": "15550101",
            "maximum_duration_seconds": "60",
            "segment_count": "1",
            "outcome": "Confirmed human answered",
        }
    )
    with candidates.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        writer.writerow(values)
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(lookup))
    assert (
        app.main(
            ["weekly-report", "--week-start", "2026-08-17", "--output", str(output)]
        )
        == 0
    )
    content = output.read_bytes()
    assert content.startswith(b"%PDF")
    assert b"PRELIMINARY" in content
    assert b"Aug 17, 2026" in content
    assert b"All scoped inbound calls" in content
    assert b"% confirmed human answered" in content
    assert b"Attribution coverage" in content
    assert b"Forwarded / routing only" in content
    assert b"Connected / unknown attribution" in content
    assert b"Automated insights" in content
    assert b"PRELIMINARY coverage" in content
    assert b"trend observations are suppressed" in content
    assert b"no customer or agent phone numbers" in content
    assert b"no agent-miss, wait-time, speed-of-answer, or outbound claims" in content
    assert b"15550001" not in content
    assert b"15550101" not in content
    assert content.count(b"/Type /Page\n") == 4


def test_weekly_report_uses_dashboard_default_filename(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "calls.csv"
    candidates = raw.with_suffix(".candidate-calls.csv")
    lookup = tmp_path / "agents.csv"
    lookup.write_text("phone_number,display_name,department,destination_type\n15550101,Alex,Membership,agent\n", encoding="utf-8")
    with candidates.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(lookup))
    monkeypatch.setenv("NEXTIVA_CLOSURE_DATES_FILE", str(Path(__file__).parents[1] / "closure_dates.csv"))
    assert app.main(["weekly-report", "--week-start", "2026-08-17"]) == 0
    assert (tmp_path / "reports" / "Nextiva_Weekly_2026-08-17_to_2026-08-23.pdf").exists()


def test_weekly_report_default_uses_complete_days_ending_yesterday(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "calls.csv"
    lookup = tmp_path / "agents.csv"
    lookup.write_text("phone_number,display_name,department,destination_type\n15550101,Alex,Membership,agent\n", encoding="utf-8")
    with raw.with_suffix(".candidate-calls.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(lookup))
    monkeypatch.setenv("NEXTIVA_CLOSURE_DATES_FILE", str(Path(__file__).parents[1] / "closure_dates.csv"))
    monkeypatch.setattr("nextiva_calls.weekly_metrics.week_for", lambda: Week(date(2026, 8, 13)))

    assert app.main(["weekly-report"]) == 0
    assert (tmp_path / "reports" / "Nextiva_Weekly_2026-08-13_to_2026-08-19.pdf").exists()


def test_agent_dashboard_shows_every_lookup_agent_and_recorded_offer_rate(tmp_path):
    rows = []
    numbers = {f"1555010{index}": f"Agent {index}" for index in range(1, 7)}
    for number in numbers:
        values = {column: "" for column in CANDIDATE_COLUMNS}
        values.update(
            {
                "call_timestamp_ct": "2026-08-17T10:00:00-05:00",
                "offered_destinations": number,
                    "offered_agent_destinations": number,
                    "recorded_offer_agent_destinations": number,
                "confirmed_answered_agent_destinations": number,
                "maximum_duration_seconds": "60",
                "outcome": "Confirmed human answered",
            }
        )
        rows.append(values)
    destinations = {number: Destination(agent, "Membership", "agent") for number, agent in numbers.items()}
    lookup = AgentLookup(destinations, {number[-4:]: frozenset({destination}) for number, destination in destinations.items()})
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup, tmp_path / "missing.sqlite3")
    output = tmp_path / "agents.pdf"
    render_weekly_report(summary, summary, output)
    content = output.read_bytes()
    assert b"Agent 1" in content
    assert b"Agent 5" in content
    assert b"Agent 6" in content
    assert b"Recorded offers" in content
    assert b"Offer answer rate" in content
    assert b"100.0%" in content


def test_daily_outcome_chart_uses_reporting_period_order(tmp_path):
    summary = summarize_week([], Week(date(2026, 8, 20)), AgentLookup({}, {}), tmp_path / "missing.sqlite3")

    assert StackedBarChart(summary).days == ("Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed")


def test_weekly_report_uses_safe_dash_for_zero_kpi_denominators(tmp_path):
    summary = summarize_week([], Week(date(2026, 8, 17)), AgentLookup({}, {}), tmp_path / "missing.sqlite3")
    output = tmp_path / "empty.pdf"

    render_weekly_report(summary, summary, output)

    assert b"Attribution coverage" in output.read_bytes()
    assert _percentage(summary.confirmed_known_agent_answers, summary.eligible_inbound_calls) == "—"
    assert _percentage(summary.attribution_coverage_numerator, summary.attribution_coverage_denominator) == "—"


def test_weekly_report_renders_complete_week_insight_evidence(tmp_path):
    base = summarize_week([], Week(date(2026, 8, 17)), AgentLookup({}, {}), tmp_path / "missing.sqlite3")
    current = replace(
        base,
        preliminary=False,
        calls=20,
        eligible_inbound_calls=20,
        outcomes={**base.outcomes, "Voicemail": 3},
    )
    prior = replace(
        base,
        preliminary=False,
        calls=20,
        eligible_inbound_calls=20,
        outcomes={**base.outcomes, "Voicemail": 1},
    )
    output = tmp_path / "insights.pdf"

    render_weekly_report(current, prior, output)

    content = output.read_bytes()
    assert b"Voicemail rate: significant weekly rate change" in content
    assert b"3/20" in content
    assert b"1/20" in content
    assert b"+10 percentage points" in content


def test_weekly_report_accepts_non_monday(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(tmp_path / "agents.csv"))
    (tmp_path / "agents.csv").write_text("phone_number,display_name,department,destination_type\n15550101,Alex,Membership,agent\n", encoding="utf-8")
    raw = tmp_path / "calls.csv"
    with raw.with_suffix(".candidate-calls.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    assert app.main(["weekly-report", "--week-start", "2026-08-18"]) == 0


def test_weekly_report_rebuilds_a_stale_candidate_schema(monkeypatch, tmp_path):
    raw = tmp_path / "calls.csv"
    analysis = raw.with_suffix(".analysis.csv")
    candidates = raw.with_suffix(".candidate-calls.csv")
    lookup_file = tmp_path / "agents.csv"
    lookup_file.write_text(
        "phone_number,display_name,department,destination_type\n"
        "15550101,Alex,Membership,agent\n",
        encoding="utf-8",
    )
    with raw.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        writer.writerow([
            "Membership", "Aug 17 2026 10:00 AM", "60", "Inbound", "Yes",
            "15550001", "15550101",
        ])
    save_analysis(analysis, raw, load_agent_lookup(lookup_file))
    candidates.write_text("candidate_id,old_column\none,old\n", encoding="utf-8")
    output = tmp_path / "manager.pdf"
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(lookup_file))

    assert app.main(["weekly-report", "--week-start", "2026-08-17", "--output", str(output)]) == 0
    with candidates.open(newline="", encoding="utf-8") as stream:
        assert next(csv.reader(stream)) == list(CANDIDATE_COLUMNS)
    assert b"100.0%" in output.read_bytes()


def test_combined_dashboard_has_separate_department_outcomes_and_hour_labels(tmp_path):
    membership = Destination("Alex", "Membership", "agent")
    certification = Destination("Casey", "Certification", "agent")
    lookup = AgentLookup(
        {"15550101": membership, "15550202": certification},
        {"0101": frozenset({membership}), "0202": frozenset({certification})},
    )
    values = {column: "" for column in CANDIDATE_COLUMNS}
    values.update({
        "call_timestamp_ct": "2026-08-17T10:00:00-05:00",
        "hunt_group": "Certification",
        "offered_destinations": "15550202",
        "offered_agent_destinations": "15550202",
        "recorded_offer_agent_destinations": "15550202",
        "maximum_duration_seconds": "60",
        "outcome": "Voicemail",
    })
    week = Week(date(2026, 8, 17))
    combined = summarize_week([values], week, lookup, tmp_path / "missing.sqlite3", scope="combined")
    membership_summary = summarize_week([values], week, lookup, tmp_path / "missing.sqlite3", scope="Membership")
    certification_summary = summarize_week([values], week, lookup, tmp_path / "missing.sqlite3", scope="Certification")
    output = tmp_path / "combined.pdf"

    render_weekly_report(combined, combined, output, membership=membership_summary, certification=certification_summary)

    content = output.read_bytes()
    assert b"Weekly Membership + Certification Call Dashboard" in content
    assert b"Daily Certification call outcomes" in content
    assert b"Daily Membership call outcomes" not in content
    assert b"08:00" in content
    assert b"Casey" in content
    assert content.count(b"/Type /Page\n") == 4
