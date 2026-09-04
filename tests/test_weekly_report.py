import csv
from datetime import date

from nextiva_calls import app
from nextiva_calls.reconstruction import CANDIDATE_COLUMNS
from nextiva_calls.segments import AgentLookup
from nextiva_calls.weekly_metrics import Week, summarize_week
from nextiva_calls.weekly_report import render_weekly_report


def test_weekly_report_creates_preliminary_pdf(monkeypatch, tmp_path):
    raw = tmp_path / "calls.csv"
    candidates = raw.with_suffix(".candidate-calls.csv")
    lookup = tmp_path / "agents.csv"
    output = tmp_path / "manager.pdf"
    lookup.write_text("phone_number,agent\n15550101,Alex\n", encoding="utf-8")
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
            "possible_answering_destinations": "15550101",
            "maximum_duration_seconds": "60",
            "segment_count": "1",
            "outcome": "Human answered",
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
    assert b"15550001" not in content
    assert b"15550101" not in content
    assert content.count(b"/Type /Page\n") == 3


def test_weekly_report_uses_dashboard_default_filename(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "calls.csv"
    candidates = raw.with_suffix(".candidate-calls.csv")
    lookup = tmp_path / "agents.csv"
    lookup.write_text("phone_number,agent\n15550101,Alex\n", encoding="utf-8")
    with candidates.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
    monkeypatch.setenv("NEXTIVA_OUTPUT_FILE", str(raw))
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(lookup))
    assert app.main(["weekly-report", "--week-start", "2026-08-17"]) == 0
    assert (tmp_path / "reports" / "Nextiva_Weekly_2026-08-17_to_2026-08-23.pdf").exists()


def test_agent_dashboard_limits_named_agents_to_top_five(tmp_path):
    rows = []
    numbers = {f"1555010{index}": f"Agent {index}" for index in range(1, 7)}
    for number in numbers:
        values = {column: "" for column in CANDIDATE_COLUMNS}
        values.update(
            {
                "call_timestamp_ct": "2026-08-17T10:00:00-05:00",
                "offered_destinations": number,
                "possible_answering_destinations": number,
                "maximum_duration_seconds": "60",
                "outcome": "Human answered",
            }
        )
        rows.append(values)
    lookup = AgentLookup(numbers, {number[-4:]: frozenset({agent}) for number, agent in numbers.items()})
    summary = summarize_week(rows, Week(date(2026, 8, 17)), lookup, tmp_path / "missing.sqlite3")
    output = tmp_path / "agents.pdf"
    render_weekly_report(summary, summary, output)
    content = output.read_bytes()
    assert b"Agent 1" in content
    assert b"Agent 5" in content
    assert b"Agent 6" not in content
    assert b"1 additional named agent was omitted" in content


def test_weekly_report_rejects_non_monday(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(tmp_path / "agents.csv"))
    assert app.main(["weekly-report", "--week-start", "2026-08-18"]) == 1
