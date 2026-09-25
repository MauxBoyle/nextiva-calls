import csv
from pathlib import Path

import pytest

from nextiva_calls.segments import (
    ANALYSIS_COLUMNS,
    AgentLookupError,
    ClosureDatesError,
    clean_segment,
    load_agent_lookup,
    load_closure_dates,
    normalize_phone_number,
)


def test_load_closure_dates_accepts_unique_iso_dates(tmp_path):
    calendar = tmp_path / "closures.csv"
    calendar.write_text("date\n2026-01-01\n2026-12-25\n", encoding="utf-8")
    assert load_closure_dates(calendar) == frozenset({"2026-01-01", "2026-12-25"})


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("wrong\n2026-01-01\n", "header"),
        ("date\nnot-a-date\n", "YYYY-MM-DD"),
        ("date\n\n", "nonblank"),
        ("date\n2026-01-01\n2026-01-01\n", "duplicate"),
    ],
)
def test_load_closure_dates_rejects_invalid_files(tmp_path, contents, message):
    calendar = tmp_path / "closures.csv"
    calendar.write_text(contents, encoding="utf-8")
    with pytest.raises(ClosureDatesError, match=message):
        load_closure_dates(calendar)


def test_load_closure_dates_rejects_missing_file(tmp_path):
    with pytest.raises(ClosureDatesError, match="could not be read"):
        load_closure_dates(tmp_path / "missing.csv")


def make_lookup(tmp_path, content):
    path = tmp_path / "agents.csv"
    path.write_text(content, encoding="utf-8")
    return load_agent_lookup(path)


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("+1 (555) 010-0200", "15550100200"),
        (" 555.010.0200 ", "5550100200"),
        ("customer unknown", None),
        ("", None),
    ],
)
def test_phone_normalization(display, expected):
    assert normalize_phone_number(display) == expected


@pytest.mark.parametrize(
    "content",
    [
        "number,agent\n5550100,Alex\n",
        "phone_number,display_name,department,destination_type\n,Alex,Membership,agent\n",
        "phone_number,display_name,department,destination_type\n555-0100,,Membership,agent\n",
        "phone_number,display_name,department,destination_type\n555-0100,Alex,Membership,agent\n5550100,Blair,Membership,agent\n",
        "phone_number,display_name,department,destination_type\n555-0100,Alex,Membership,hunt_group\n",
    ],
)
def test_lookup_rejects_invalid_content(tmp_path, content):
    path = tmp_path / "agents.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AgentLookupError):
        load_agent_lookup(path)


def raw_row(destination="555-0200", timestamp="Jan 2, 2026 9:30 AM", answered="YES"):
    return ("Caller", timestamp, "60", "Inbound", answered, "+1 555 010 0000", destination)


def test_clean_segment_prefers_full_number_then_unique_extension_and_voicemail(tmp_path):
    lookup = make_lookup(
        tmp_path,
        "phone_number,display_name,department,destination_type\n5550200,Full match,Membership,agent\n12125550300,Extension match,Membership,agent\n9999,Not voicemail,All,agent\n",
    )
    full = clean_segment(raw_row("555-0200"), lookup)
    extension = clean_segment(raw_row("(212) 555-0300"), lookup)
    voicemail = clean_segment(raw_row("9999"), lookup)
    assert full[ANALYSIS_COLUMNS.index("destination_label")] == "Full match"
    assert extension[ANALYSIS_COLUMNS.index("destination_label")] == "Extension match"
    assert voicemail[ANALYSIS_COLUMNS.index("is_voicemail_destination")] == "True"
    assert voicemail[ANALYSIS_COLUMNS.index("destination_type")] == "voicemail"


def test_lookup_exposes_display_name_department_and_role(tmp_path):
    lookup = make_lookup(
        tmp_path,
        "phone_number,display_name,department,destination_type\n5550200,Router,Membership,system\n",
    )
    destination = lookup.by_number["5550200"]
    assert (destination.display_name, destination.department, destination.destination_type) == (
        "Router",
        "Membership",
        "system",
    )


def test_clean_segment_marks_ambiguous_extensions_and_unknowns(tmp_path):
    lookup = make_lookup(
        tmp_path, "phone_number,display_name,department,destination_type\n1115550200,Alex,Membership,agent\n2225550200,Blair,Membership,agent\n"
    )
    cleaned = clean_segment(raw_row("555-0200", "bad date", "maybe"), lookup)
    assert cleaned[ANALYSIS_COLUMNS.index("destination_label")] == "Unknown"
    assert cleaned[ANALYSIS_COLUMNS.index("destination_type")] == "unknown"
    assert cleaned[ANALYSIS_COLUMNS.index("is_anomaly")] == "True"
    assert cleaned[ANALYSIS_COLUMNS.index("anomaly_reasons")] == (
        "bad_timestamp;ambiguous_destination_extension;unknown_answered"
    )


def test_clean_segment_handles_dst_and_business_hour_boundaries(tmp_path):
    lookup = make_lookup(tmp_path, "phone_number,display_name,department,destination_type\n5550200,Alex,Membership,agent\n")
    cst = clean_segment(raw_row(timestamp="2026-01-02T15:00:00+00:00"), lookup)
    cdt = clean_segment(raw_row(timestamp="2026-07-02T14:00:00+00:00"), lookup)
    closing = clean_segment(raw_row(timestamp="2026-07-02 5:00 PM"), lookup)
    assert cst[ANALYSIS_COLUMNS.index("call_timestamp_ct")].endswith("-06:00")
    assert cdt[ANALYSIS_COLUMNS.index("call_timestamp_ct")].endswith("-05:00")
    assert cst[ANALYSIS_COLUMNS.index("is_business_hours")] == "True"
    assert cdt[ANALYSIS_COLUMNS.index("is_business_hours")] == "True"
    assert closing[ANALYSIS_COLUMNS.index("is_business_hours")] == "False"


def test_clean_segment_uses_configured_closure_dates(tmp_path):
    lookup = make_lookup(
        tmp_path,
        "phone_number,display_name,department,destination_type\n555-0200,Alex,Membership,agent\n",
    )
    cleaned = clean_segment(
        raw_row(timestamp="2026-08-17 10:00 AM"),
        lookup,
        frozenset({"2026-08-17"}),
    )
    assert cleaned[ANALYSIS_COLUMNS.index("is_holiday")] == "True"
    assert cleaned[ANALYSIS_COLUMNS.index("is_business_hours")] == "False"


@pytest.mark.parametrize(
    "closure",
    sorted(load_closure_dates(Path(__file__).parents[1] / "config" / "closure_dates.csv")),
)
def test_closures_are_holidays_and_not_business_hours(tmp_path, closure):
    lookup = make_lookup(tmp_path, "phone_number,display_name,department,destination_type\n5550200,Alex,Membership,agent\n")
    cleaned = clean_segment(
        raw_row(timestamp=f"{closure} 10:00 AM"),
        lookup,
        load_closure_dates(Path(__file__).parents[1] / "config" / "closure_dates.csv"),
    )
    assert cleaned[ANALYSIS_COLUMNS.index("is_holiday")] == "True"
    assert cleaned[ANALYSIS_COLUMNS.index("is_business_hours")] == "False"


def test_analysis_header_is_csv_safe():
    assert len(ANALYSIS_COLUMNS) == 18
    assert list(csv.reader([",".join(ANALYSIS_COLUMNS)]))[0] == list(ANALYSIS_COLUMNS)
