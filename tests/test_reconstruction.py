from nextiva_calls.reconstruction import CANDIDATE_COLUMNS, reconstruct_calls
from nextiva_calls.segments import ANALYSIS_COLUMNS


def segment(*, name="Membership", timestamp="2026-08-15T00:00:00-05:00", source="15550100", destination="15550200", duration="12", answered="No", destination_type="agent", anomaly_reasons=""):
    values = {"Name": name, "Time of Call": "Aug 15 2026 12:00 AM", "Duration": duration, "Direction": "Inbound", "Answered": answered, "From": source, "To": destination, "call_timestamp_ct": timestamp, "from_number_normalized": source, "to_number_normalized": destination, "destination_label": "Destination", "destination_type": destination_type, "is_voicemail_destination": str(destination == "9999"), "is_duplicate": "False", "is_anomaly": str(bool(anomaly_reasons)), "anomaly_reasons": anomaly_reasons, "is_business_hours": "False", "is_holiday": "False"}
    return tuple(values[column] for column in ANALYSIS_COLUMNS)


def candidate(*segments, **kwargs):
    return reconstruct_calls(list(segments) or [segment(**kwargs)])[0]


def value(row, column):
    return row[CANDIDATE_COLUMNS.index(column)]


def test_groups_segments_and_assigns_every_segment_once():
    calls = reconstruct_calls([segment(destination="15550200"), segment(destination="15550300"), segment(source="15550101")])
    assert [value(call, "segment_count") for call in calls] == ["2", "1"]
    fingerprints = ";".join(value(call, "segment_fingerprints") for call in calls).split(";")
    assert len(fingerprints) == len(set(fingerprints)) == 3


def test_evidence_is_ordered_and_duplicate_free():
    row = candidate(segment(destination="15550200", answered="Yes"), segment(destination="15550200", answered="Yes"), segment(destination="15550300", answered="No"), segment(destination="15550400", answered="Yes - Forwarded"), segment(destination="15550500", destination_type="system"))
    assert value(row, "offered_destinations") == "15550200;15550300;15550400;15550500"
    assert value(row, "offered_agent_destinations") == "15550200;15550300;15550400"
    assert value(row, "confirmed_answered_agent_destinations") == "15550200"
    assert value(row, "forwarded_destinations") == "15550400"
    assert value(row, "system_routing_destinations") == "15550500"


def test_role_aware_outcomes_and_evidence():
    agent_yes = candidate(segment(answered="Yes"))
    agent_no = candidate(segment(answered="No"))
    forwarded = candidate(segment(answered="Yes - Forwarded"))
    system_yes = candidate(segment(answered="Yes", destination_type="system"))
    unknown_yes = candidate(segment(answered="Yes", destination_type="unknown"))
    voicemail = candidate(segment(destination="9999", answered="No", destination_type="voicemail"))
    assert value(agent_yes, "outcome") == "Confirmed human answered"
    assert value(agent_no, "outcome") == "Unanswered"
    assert value(forwarded, "outcome") == "Forwarded / routing only"
    assert value(system_yes, "outcome") == "Connected / unknown attribution"
    assert value(system_yes, "offered_agent_destinations") == ""
    assert value(unknown_yes, "outcome") == "Answered / unattributed"
    assert value(unknown_yes, "unknown_answered_destinations") == "15550200"
    assert value(voicemail, "outcome") == "Voicemail"
    assert value(voicemail, "reached_voicemail") == "True"


def test_voicemail_plus_affirmative_evidence_is_ambiguous():
    row = candidate(segment(destination="9999", destination_type="voicemail"), segment(answered="Yes"))
    assert value(row, "outcome") == "Ambiguous"
    assert value(row, "conflict_reasons") == "voicemail_and_non_voicemail_answer"


def test_missing_invalid_or_conflicting_data_stays_unknown():
    invalid = candidate(segment(answered="maybe", anomaly_reasons="unknown_answered"))
    unknown_no = candidate(segment(destination_type="unknown"))
    missing = reconstruct_calls([segment(source="")])[0]
    assert value(invalid, "outcome") == "Unknown"
    assert value(unknown_no, "outcome") == "Unknown"
    assert value(missing, "outcome") == "Unknown"


def test_membership_routing_boundary_is_inclusive_and_other_groups_are_sequential():
    assert value(candidate(timestamp="2026-08-14T23:59:59-05:00"), "routing_mode") == "Sequential"
    assert value(candidate(timestamp="2026-08-15T00:00:00-05:00"), "routing_mode") == "Simultaneous"
    assert value(candidate(name="Support"), "routing_mode") == "Sequential"
