"""Call-record validation and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dateutil.parser import ParserError
from dateutil.parser import parse as parse_datetime

CSV_COLUMNS = (
    "Name",
    "Time of Call",
    "Duration",
    "Direction",
    "Answered",
    "From",
    "To",
)
_DURATION_PATTERN = re.compile(
    r"^\s*(?:(?P<hours>\d+)\s*h)?\s*(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?\s*$",
    re.IGNORECASE,
)


class RecordError(ValueError):
    """Raised when a call record cannot be validated."""


def clean_text(value: str) -> str:
    """Collapse display whitespace while leaving punctuation unchanged."""
    return " ".join(value.split())


def duration_to_seconds(value: str) -> int:
    """Convert a strict Nextiva ``h/m/s`` duration to integer seconds."""
    match = _DURATION_PATTERN.fullmatch(value)
    if not match or not any(match.groupdict().values()):
        raise RecordError("Duration must use h, m, or s units")
    parts = {name: int(number or 0) for name, number in match.groupdict().items()}
    if parts["minutes"] >= 60 or parts["seconds"] >= 60:
        raise RecordError("Duration minutes and seconds must be below 60")
    return parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


@dataclass(frozen=True)
class CallRecord:
    """The seven values written for one call."""

    name: str
    time_of_call: str
    duration: int
    direction: str
    answered: str
    from_number: str
    to_number: str

    @classmethod
    def from_cells(cls, cells: list[str] | tuple[str, ...]) -> CallRecord:
        """Validate and create a record from seven displayed table cells."""
        if len(cells) != len(CSV_COLUMNS):
            raise RecordError("A call row must contain exactly seven cells")
        cleaned = [clean_text(cell) for cell in cells]
        try:
            parse_datetime(cleaned[1])
        except (ParserError, OverflowError, ValueError) as error:
            raise RecordError(
                "Time of Call is not a recognizable date and time"
            ) from error
        return cls(
            name=cleaned[0],
            time_of_call=cleaned[1],
            duration=duration_to_seconds(cleaned[2]),
            direction=cleaned[3],
            answered=cleaned[4],
            from_number=cleaned[5],
            to_number=cleaned[6],
        )

    def as_csv_row(self) -> tuple[str, ...]:
        """Return values in the public CSV column order."""
        return (
            self.name,
            self.time_of_call,
            str(self.duration),
            self.direction,
            self.answered,
            self.from_number,
            self.to_number,
        )
