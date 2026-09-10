"""Clean raw Nextiva call rows into analysis-ready call segments."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.parser import ParserError
from dateutil.parser import parse as parse_datetime

from nextiva_calls.records import CSV_COLUMNS, clean_text

CENTRAL_TIME = ZoneInfo("America/Chicago")
ANALYSIS_COLUMNS = CSV_COLUMNS + (
    "call_timestamp_ct",
    "from_number_normalized",
    "to_number_normalized",
    "destination_label",
    "destination_type",
    "is_voicemail_destination",
    "is_duplicate",
    "is_anomaly",
    "anomaly_reasons",
    "is_business_hours",
    "is_holiday",
)

# These are the 2026 office closures used when classifying business hours.
CLOSURE_DATES_2026 = frozenset(
    {
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # Martin Luther King Jr. Day
        "2026-02-16",  # Presidents Day
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-10-12",  # Indigenous Peoples' Day
        "2026-11-11",  # Veterans Day
        "2026-11-26",  # Thanksgiving Day
        "2026-11-27",  # Day after Thanksgiving
        "2026-12-25",  # Christmas Day
    }
)
_PHONE_CHARACTERS = re.compile(r"^[0-9+().\-\s]+$")


class AgentLookupError(ValueError):
    """Raised when the externally supplied agent lookup cannot be used."""


@dataclass(frozen=True)
class Destination:
    """A manually maintained destination and its role in call routing."""

    display_name: str
    department: str
    destination_type: str


@dataclass(frozen=True)
class AgentLookup:
    """Full phone-number destinations and their possibly ambiguous extensions."""

    by_number: dict[str, Destination]
    by_extension: dict[str, frozenset[Destination]]


def normalize_phone_number(value: str) -> str | None:
    """Return all phone digits, or ``None`` when the displayed value is unusable.

    No country-code or customer-number shortening is performed.  Punctuation is
    accepted only when it is normal telephone punctuation, so labels such as
    ``"unknown"`` cannot accidentally become a usable number.
    """
    if not value or not _PHONE_CHARACTERS.fullmatch(value):
        return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None


def load_agent_lookup(path: Path | None) -> AgentLookup:
    """Read and validate the role-aware destination lookup CSV."""
    if path is None:
        raise AgentLookupError("NEXTIVA_AGENT_LOOKUP_FILE is required")
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != [
                "phone_number",
                "display_name",
                "department",
                "destination_type",
            ]:
                raise AgentLookupError(
                    "Agent lookup must have exactly phone_number,display_name,department,destination_type headers"
                )
            mappings: dict[str, Destination] = {}
            for row_number, row in enumerate(reader, start=2):
                phone = clean_text(row.get("phone_number") or "")
                display_name = clean_text(row.get("display_name") or "")
                department = clean_text(row.get("department") or "")
                destination_type = clean_text(row.get("destination_type") or "").casefold()
                normalized = normalize_phone_number(phone)
                if (
                    normalized is None
                    or len(normalized) < 4
                    or not display_name
                    or not department
                    or destination_type not in {"agent", "system"}
                    or None in row
                ):
                    raise AgentLookupError(
                        f"Agent lookup row {row_number} has a blank or invalid value"
                    )
                destination = Destination(display_name, department, destination_type)
                existing = mappings.get(normalized)
                if existing is not None and existing != destination:
                    raise AgentLookupError(
                        f"Agent lookup maps {normalized} to conflicting destinations"
                    )
                mappings[normalized] = destination
    except AgentLookupError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise AgentLookupError("Agent lookup file could not be read") from error
    if not mappings:
        raise AgentLookupError("Agent lookup must contain at least one mapping")
    extensions: defaultdict[str, set[Destination]] = defaultdict(set)
    for number, destination in mappings.items():
        if len(number) >= 4:
            extensions[number[-4:]].add(destination)
    return AgentLookup(
        mappings, {extension: frozenset(agents) for extension, agents in extensions.items()}
    )


def _timestamp_ct(value: str) -> datetime | None:
    try:
        parsed = parse_datetime(value, fuzzy=False)
    except (ParserError, OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CENTRAL_TIME)
    return parsed.astimezone(CENTRAL_TIME)


def _normalized_answered(value: str) -> tuple[str, bool]:
    key = clean_text(value).casefold().replace("–", "-").replace("—", "-")
    key = " ".join(key.replace("-", " ").split())
    if key in {"yes", "answered"}:
        return "Yes", True
    if key in {"no", "not answered", "unanswered"}:
        return "No", True
    if key in {"yes forwarded", "forwarded", "answered forwarded"}:
        return "Yes - Forwarded", True
    return value, False


def clean_segment(row: tuple[str, ...], lookup: AgentLookup) -> tuple[str, ...]:
    """Enrich one raw row; anomalies remain available for analysis, not rejection."""
    name, called_at, duration, direction, answered, source, destination = row
    reasons: list[str] = []
    timestamp = _timestamp_ct(called_at)
    if timestamp is None:
        reasons.append("bad_timestamp")
    source_normalized = normalize_phone_number(source)
    if source_normalized is None:
        reasons.append("unusable_from_number")
    destination_normalized = normalize_phone_number(destination)
    if destination_normalized is None:
        reasons.append("unusable_to_number")

    destination_label = "Unknown"
    destination_type = "unknown"
    voicemail = destination_normalized == "9999"
    if voicemail:
        destination_label = "Voicemail"
        destination_type = "voicemail"
    elif destination_normalized is not None:
        if destination_normalized in lookup.by_number:
            destination = lookup.by_number[destination_normalized]
            destination_label = destination.display_name
            destination_type = destination.destination_type
        elif len(destination_normalized) >= 4:
            candidates = lookup.by_extension.get(destination_normalized[-4:], frozenset())
            if len(candidates) == 1:
                destination = next(iter(candidates))
                destination_label = destination.display_name
                destination_type = destination.destination_type
            elif len(candidates) > 1:
                reasons.append("ambiguous_destination_extension")
    normalized_answered, known_answered = _normalized_answered(answered)
    if not known_answered:
        reasons.append("unknown_answered")

    holiday = timestamp is not None and timestamp.date().isoformat() in CLOSURE_DATES_2026
    business_hours = bool(
        timestamp is not None
        and timestamp.weekday() < 5
        and not holiday
        and time(9) <= timestamp.timetz().replace(tzinfo=None) < time(17)
    )
    return (
        name,
        called_at,
        duration,
        direction,
        normalized_answered,
        source,
        destination,
        timestamp.isoformat() if timestamp is not None else "",
        source_normalized or "",
        destination_normalized or "",
        destination_label,
        destination_type,
        str(voicemail),
        "False",
        str(bool(reasons)),
        ";".join(reasons),
        str(business_hours),
        str(holiday),
    )
