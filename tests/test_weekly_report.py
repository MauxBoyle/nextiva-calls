import csv

from nextiva_calls import app
from nextiva_calls.reconstruction import CANDIDATE_COLUMNS


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


def test_weekly_report_rejects_non_monday(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXTIVA_AGENT_LOOKUP_FILE", str(tmp_path / "agents.csv"))
    assert app.main(["weekly-report", "--week-start", "2026-08-18"]) == 1
