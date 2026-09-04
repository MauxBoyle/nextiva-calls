from nextiva_calls.reconstruction import CANDIDATE_COLUMNS, reconstruct_calls
from nextiva_calls.segments import ANALYSIS_COLUMNS


def segment(
    *,
    name="Membership",
    timestamp="2026-08-15T00:00:00-05:00",
    source="15550100",
    destination="15550200",
    duration="12",
    answered="No",
    voicemail=False,
    anomaly_reasons="",
):
    values = {
        "Name": name,
        "Time of Call": "Aug 15 2026 12:00 AM",
        "Duration": duration,
        "Direction": "Inbound",
        "Answered": answered,
        "From": source,
        "To": destination,
        "call_timestamp_ct": timestamp,
        "from_number_normalized": source,
        "to_number_normalized": destination,
        "destination_label": "Agent",
        "is_voicemail_destination": str(voicemail),
        "is_duplicate": "False",
        "is_anomaly": str(bool(anomaly_reasons)),
        "anomaly_reasons": anomaly_reasons,
        "is_business_hours": "False",
        "is_holiday": "False",
    }
    return tuple(values[column] for column in ANALYSIS_COLUMNS)


def candidate(*segments, **kwargs):
    return reconstruct_calls(list(segments) or [segment(**kwargs)])[0]


def value(row, column):
    return row[CANDIDATE_COLUMNS.index(column)]


def test_groups_segments_and_assigns_every_segment_once():
    calls = reconstruct_calls(
        [segment(destination="15550200"), segment(destination="15550300"), segment(source="15550101")]
    )
    assert [value(call, "segment_count") for call in calls] == ["2", "1"]
    fingerprints = ";".join(value(call, "segment_fingerprints") for call in calls).split(";")
    assert len(fingerprints) == len(set(fingerprints)) == 3


def test_unusable_grouping_value_makes_an_unknown_single_segment_call():
    calls = reconstruct_calls([segment(source=""), segment(source="")])
    assert len(calls) == 2
    assert {value(call, "hunt_group") for call in calls} == {"Unknown"}
    assert {value(call, "outcome") for call in calls} == {"Unknown"}


def test_destinations_are_unique_ordered_and_max_duration_is_used():
    row = candidate(
        segment(destination="15550200", duration="2"),
        segment(destination="15550200", duration="30"),
        segment(destination="9999", duration="4", voicemail=True),
    )
    assert value(row, "offered_destinations") == "15550200;9999"
    assert value(row, "unique_routing_attempts") == "2"
    assert value(row, "maximum_duration_seconds") == "30"


def test_membership_routing_boundary_is_inclusive_and_other_groups_are_sequential():
    before = candidate(timestamp="2026-08-14T23:59:59-05:00")
    at = candidate(timestamp="2026-08-15T00:00:00-05:00")
    other = candidate(name="Support", timestamp="2026-08-16T00:00:00-05:00")
    assert value(before, "routing_mode") == "Sequential"
    assert value(at, "routing_mode") == "Simultaneous"
    assert value(other, "routing_mode") == "Sequential"


def test_outcome_precedence_and_conflict_rules():
    human = candidate(segment(answered="Yes"))
    forwarded = candidate(segment(answered="Yes - Forwarded"))
    voicemail = candidate(segment(destination="9999", voicemail=True, answered="No"))
    unanswered = candidate(segment(answered="No"))
    unknown = candidate(segment(answered="maybe", anomaly_reasons="unknown_answered"))
    ambiguous = candidate(
        segment(destination="9999", voicemail=True, answered="No"),
        segment(answered="Yes", destination="15550300"),
    )
    assert value(human, "outcome") == "Human answered"
    assert value(forwarded, "outcome") == "Forwarded answered"
    assert value(voicemail, "outcome") == "Voicemail"
    assert value(unanswered, "outcome") == "Unanswered"
    assert value(unknown, "outcome") == "Unknown"
    assert value(ambiguous, "outcome") == "Ambiguous"
    assert value(ambiguous, "conflict_reasons") == "voicemail_and_non_voicemail_answer"


def test_all_positive_non_voicemail_destinations_are_preserved():
    row = candidate(
        segment(answered="Yes", destination="15550200"),
        segment(answered="Yes - Forwarded", destination="15550300"),
    )
    assert value(row, "possible_answering_destinations") == "15550200;15550300"
