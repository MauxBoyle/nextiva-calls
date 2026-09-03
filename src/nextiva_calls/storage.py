"""Atomic CSV and JSON state-file storage."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

from nextiva_calls.records import CSV_COLUMNS, CallRecord, clean_text


class StorageError(RuntimeError):
    """Raised when stored CSV or state data is invalid or cannot be saved."""


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    return Path(name)


def load_csv(path: Path) -> list[tuple[str, ...]]:
    """Read existing rows after checking the exact public header."""
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
            if header != list(CSV_COLUMNS):
                raise StorageError("Existing CSV has an unexpected header")
            rows = [tuple(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as error:
        raise StorageError("Existing CSV could not be read") from error
    if any(len(row) != len(CSV_COLUMNS) for row in rows):
        raise StorageError("Existing CSV contains a malformed row")
    return rows


def save_records(path: Path, records: list[CallRecord]) -> int:
    """Add exact-row unique records using a same-directory atomic replacement."""
    existing = load_csv(path)
    known = {tuple(clean_text(value) for value in row) for row in existing}
    additions: list[tuple[str, ...]] = []
    for record in records:
        row = record.as_csv_row()
        normalized_row = tuple(clean_text(value) for value in row)
        if normalized_row not in known:
            additions.append(row)
            known.add(normalized_row)
    if path.exists() and not additions:
        return 0
    try:
        temporary = _temporary_path(path)
    except OSError as error:
        raise StorageError("CSV temporary file could not be created") from error
    try:
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(existing)
            writer.writerows(additions)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise StorageError("CSV could not be written") from error
    finally:
        temporary.unlink(missing_ok=True)
    return len(additions)


def load_state(path: Path) -> set[str]:
    """Load processed report identifiers from versioned JSON."""
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StorageError("State file is unreadable or corrupt") from error
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("processed"), list)
        or not all(isinstance(item, str) for item in payload["processed"])
    ):
        raise StorageError("State file has an unsupported format")
    return set(payload["processed"])


def save_state(path: Path, processed: set[str]) -> None:
    """Save processed identifiers with an atomic replacement."""
    try:
        temporary = _temporary_path(path)
    except OSError as error:
        raise StorageError("State temporary file could not be created") from error
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"version": 1, "processed": sorted(processed)}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise StorageError("State file could not be written") from error
    finally:
        temporary.unlink(missing_ok=True)
