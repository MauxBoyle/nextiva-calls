"""Rebuild candidate calls from duplicate-free Nextiva call segments."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Iterable
from datetime import datetime

from nextiva_calls.records import clean_text
from nextiva_calls.segments import ANALYSIS_COLUMNS

CANDIDATE_COLUMNS = (
    "candidate_id",
    "hunt_group",
    "call_timestamp_ct",
    "from_number_normalized",
    "routing_mode",
    "offered_destinations",
    "unique_routing_attempts",
    "offered_agent_destinations",
    "recorded_offer_agent_destinations",
    "confirmed_answered_agent_destinations",
    "forwarded_destinations",
    "unknown_status_agent_destinations",
    "system_routing_destinations",
    "reached_voicemail",
    "unknown_answered_destinations",
    "maximum_duration_seconds",
    "segment_count",
    "outcome",
    "conflict_reasons",
    "segment_fingerprints",
)
MEMBERSHIP_SIMULTANEOUS_FROM = "2026-08-15T00:00:00-05:00"


def _fingerprint(values: Iterable[str]) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _unique(values: Iterable[str]) -> list[str]:
    return list(OrderedDict.fromkeys(value for value in values if value))


def _candidate_key(
    segment: dict[str, str], fingerprint: str, position: int
) -> tuple[str, ...]:
    """Return a grouping key, isolating rows that lack any required value."""
    hunt_group = clean_text(segment["Name"])
    timestamp = segment["call_timestamp_ct"]
    caller = segment["from_number_normalized"]
    if not hunt_group or not timestamp or not caller:
        return ("unknown", fingerprint, str(position))
    return ("known", hunt_group, timestamp, caller)


def _routing_mode(
    hunt_group: str,
    timestamp: str,
    membership_hunt_group: str,
    membership_simultaneous_from: datetime,
) -> str:
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return "Sequential"
    if (
        hunt_group.casefold() == membership_hunt_group.casefold()
        and when >= membership_simultaneous_from
    ):
        return "Simultaneous"
    return "Sequential"


def reconstruct_calls(
    rows: Iterable[tuple[str, ...]],
    *,
    membership_hunt_group: str = "Membership",
    membership_simultaneous_from: str = MEMBERSHIP_SIMULTANEOUS_FROM,
) -> list[tuple[str, ...]]:
    """Combine analysis segments into deterministic, auditable candidate calls.

    A candidate is the exact combination of hunt group, Central timestamp, and
    normalized caller. Rows missing any one of those values intentionally never
    combine with another row: the export cannot support that inference safely.
    """
    try:
        simultaneous_from = datetime.fromisoformat(membership_simultaneous_from)
    except ValueError as error:
        raise ValueError("Membership simultaneous-routing time must be ISO-8601") from error
    if simultaneous_from.tzinfo is None:
        raise ValueError("Membership simultaneous-routing time must include an offset")

    groups: OrderedDict[tuple[str, ...], list[tuple[dict[str, str], str]]] = OrderedDict()
    for position, row in enumerate(rows):
        if len(row) != len(ANALYSIS_COLUMNS):
            raise ValueError("Analysis CSV contains a malformed row")
        segment = dict(zip(ANALYSIS_COLUMNS, row, strict=True))
        fingerprint = _fingerprint(row)
        groups.setdefault(_candidate_key(segment, fingerprint, position), []).append(
            (segment, fingerprint)
        )

    candidates: list[tuple[str, ...]] = []
    for key, segments in groups.items():
        first = segments[0][0]
        known = key[0] == "known"
        hunt_group = clean_text(first["Name"]) if known else "Unknown"
        timestamp = first["call_timestamp_ct"] if known else ""
        caller = first["from_number_normalized"] if known else ""
        fingerprints = [fingerprint for _, fingerprint in segments]
        candidate_id = "candidate_" + _fingerprint(key + tuple(fingerprints))[:20]
        destinations = _unique(segment["to_number_normalized"] for segment, _ in segments)
        offered_agents = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "agent"
            and segment["to_number_normalized"]
        )
        # A recorded offer is deliberately narrower than a routing attempt:
        # only an agent segment with an exact normalized Yes or No status can
        # enter the answer-rate denominator.
        recorded_offers = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "agent"
            and segment["Answered"] in {"Yes", "No"}
            and segment["to_number_normalized"]
        )
        confirmed_agents = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "agent"
            and segment["Answered"] == "Yes"
            and segment["to_number_normalized"]
        )
        forwarded = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["is_voicemail_destination"] != "True"
            and segment["Answered"] == "Yes - Forwarded"
            and segment["to_number_normalized"]
        )
        unknown_status_agents = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "agent"
            and segment["Answered"] not in {"Yes", "No", "Yes - Forwarded"}
            and segment["to_number_normalized"]
        )
        system_routing = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "system"
            and segment["to_number_normalized"]
        )
        unknown_answered = _unique(
            segment["to_number_normalized"]
            for segment, _ in segments
            if segment["destination_type"] == "unknown"
            and segment["Answered"] == "Yes"
            and segment["to_number_normalized"]
        )
        has_voicemail = any(
            segment["is_voicemail_destination"] == "True" for segment, _ in segments
        )
        system_answered = any(
            segment["destination_type"] == "system" and segment["Answered"] == "Yes"
            for segment, _ in segments
        )
        affirmative_non_voicemail = bool(
            confirmed_agents or system_answered or unknown_answered or forwarded
        )
        known_negative_only = bool(segments) and all(
            segment["Answered"] == "No"
            and not segment["anomaly_reasons"]
            and bool(segment["to_number_normalized"])
            and segment["destination_type"] == "agent"
            for segment, _ in segments
        )
        if not known:
            outcome, conflicts = "Unknown", ""
        elif has_voicemail and affirmative_non_voicemail:
            outcome, conflicts = "Ambiguous", "voicemail_and_non_voicemail_answer"
        elif confirmed_agents:
            outcome, conflicts = "Confirmed human answered", ""
        elif system_answered:
            outcome, conflicts = "Connected / unknown attribution", ""
        elif unknown_answered:
            outcome, conflicts = "Answered / unattributed", ""
        elif forwarded or system_routing:
            outcome, conflicts = "Forwarded / routing only", ""
        elif has_voicemail:
            outcome, conflicts = "Voicemail", ""
        elif known_negative_only:
            outcome, conflicts = "Unanswered", ""
        else:
            outcome, conflicts = "Unknown", ""
        durations: list[int] = []
        for segment, _ in segments:
            try:
                durations.append(int(segment["Duration"]))
            except ValueError:
                pass
        candidates.append(
            (
                candidate_id,
                hunt_group,
                timestamp,
                caller,
                _routing_mode(hunt_group, timestamp, membership_hunt_group, simultaneous_from),
                ";".join(destinations),
                str(len(destinations)),
                ";".join(offered_agents),
                ";".join(recorded_offers),
                ";".join(confirmed_agents),
                ";".join(forwarded),
                ";".join(unknown_status_agents),
                ";".join(system_routing),
                str(has_voicemail),
                ";".join(unknown_answered),
                str(max(durations, default=0)),
                str(len(segments)),
                outcome,
                conflicts,
                ";".join(fingerprints),
            )
        )
    return candidates
