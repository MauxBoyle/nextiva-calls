import csv

import pytest

from nextiva_calls.segments import (
    ANALYSIS_COLUMNS,
    CLOSURE_DATES_2026,
    AgentLookupError,
    clean_segment,
    load_agent_lookup,
    normalize_phone_number,
)


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
        "phone_number,agent\n,Alex\n",
        "phone_number,agent\n555-0100,\n",
        "phone_number,agent\n555-0100,Alex\n5550100,Blair\n",
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
        "phone_number,agent\n5550200,Full match\n12125550300,Extension match\n9999,Not voicemail\n",
    )
    full = clean_segment(raw_row("555-0200"), lookup)
    extension = clean_segment(raw_row("(212) 555-0300"), lookup)
    voicemail = clean_segment(raw_row("9999"), lookup)
    assert full[ANALYSIS_COLUMNS.index("destination_label")] == "Full match"
    assert extension[ANALYSIS_COLUMNS.index("destination_label")] == "Extension match"
    assert voicemail[ANALYSIS_COLUMNS.index("is_voicemail_destination")] == "True"


def test_clean_segment_marks_ambiguous_extensions_and_unknowns(tmp_path):
    lookup = make_lookup(
        tmp_path, "phone_number,agent\n1115550200,Alex\n2225550200,Blair\n"
    )
    cleaned = clean_segment(raw_row("555-0200", "bad date", "maybe"), lookup)
    assert cleaned[ANALYSIS_COLUMNS.index("destination_label")] == "Other"
    assert cleaned[ANALYSIS_COLUMNS.index("is_anomaly")] == "True"
    assert cleaned[ANALYSIS_COLUMNS.index("anomaly_reasons")] == (
        "bad_timestamp;ambiguous_destination_extension;unknown_answered"
    )


def test_clean_segment_handles_dst_and_business_hour_boundaries(tmp_path):
    lookup = make_lookup(tmp_path, "phone_number,agent\n5550200,Alex\n")
    cst = clean_segment(raw_row(timestamp="2026-01-02T15:00:00+00:00"), lookup)
    cdt = clean_segment(raw_row(timestamp="2026-07-02T14:00:00+00:00"), lookup)
    closing = clean_segment(raw_row(timestamp="2026-07-02 5:00 PM"), lookup)
    assert cst[ANALYSIS_COLUMNS.index("call_timestamp_ct")].endswith("-06:00")
    assert cdt[ANALYSIS_COLUMNS.index("call_timestamp_ct")].endswith("-05:00")
    assert cst[ANALYSIS_COLUMNS.index("is_business_hours")] == "True"
    assert cdt[ANALYSIS_COLUMNS.index("is_business_hours")] == "True"
    assert closing[ANALYSIS_COLUMNS.index("is_business_hours")] == "False"


@pytest.mark.parametrize("closure", sorted(CLOSURE_DATES_2026))
def test_closures_are_holidays_and_not_business_hours(tmp_path, closure):
    lookup = make_lookup(tmp_path, "phone_number,agent\n5550200,Alex\n")
    cleaned = clean_segment(raw_row(timestamp=f"{closure} 10:00 AM"), lookup)
    assert cleaned[ANALYSIS_COLUMNS.index("is_holiday")] == "True"
    assert cleaned[ANALYSIS_COLUMNS.index("is_business_hours")] == "False"


def test_analysis_header_is_csv_safe():
    assert len(ANALYSIS_COLUMNS) == 17
    assert list(csv.reader([",".join(ANALYSIS_COLUMNS)]))[0] == list(ANALYSIS_COLUMNS)
