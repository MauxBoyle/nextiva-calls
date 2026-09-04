"""Atomic CSV and JSON state-file storage."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
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


def normalized_row(row: tuple[str, ...]) -> tuple[str, ...]:
    """Return the stable form used for exact duplicate comparisons."""
    return tuple(clean_text(value) for value in row)


def row_fingerprint(row: tuple[str, ...]) -> str:
    """Create a deterministic identifier for one normalized CSV row."""
    return hashlib.sha256("\x1f".join(normalized_row(row)).encode()).hexdigest()


def append_records(path: Path, records: list[CallRecord]) -> int:
    """Append rows without rewriting or de-duplicating the raw call-data CSV."""
    existing = load_csv(path)
    if not records:
        return 0
    try:
        temporary = _temporary_path(path)
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(existing)
            writer.writerows(record.as_csv_row() for record in records)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise StorageError("Raw CSV could not be appended") from error
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
    return len(records)


def save_analysis(path: Path, raw_path: Path) -> int:
    """Atomically derive a first-seen, exact-row-unique CSV from raw data."""
    rows = load_csv(raw_path)
    unique: list[tuple[str, ...]] = []
    known: set[tuple[str, ...]] = set()
    for row in rows:
        key = normalized_row(row)
        if key not in known:
            known.add(key)
            unique.append(row)
    try:
        temporary = _temporary_path(path)
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(unique)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise StorageError("Analysis CSV could not be written") from error
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
    return len(unique)


def report_fingerprint(
    period_start: datetime | None,
    period_end: datetime | None,
    records: list[CallRecord],
) -> str:
    """Fingerprint a report from its period and ordered, normalized call rows."""
    period = "|".join(
        value.isoformat() if value is not None else ""
        for value in (period_start, period_end)
    )
    rows = "\x1e".join(row_fingerprint(record.as_csv_row()) for record in records)
    return hashlib.sha256(f"{period}\x1d{rows}".encode()).hexdigest()


class MetadataStore:
    """SQLite provenance storage for reports, messages, and call segments."""

    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StorageError("Metadata database integrity check failed")
            return connection
        except (OSError, sqlite3.DatabaseError) as error:
            raise StorageError("Metadata database could not be opened") from error

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    period_start TEXT,
                    period_end TEXT,
                    imported_at TEXT NOT NULL,
                    warnings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_messages (
                    message_id TEXT PRIMARY KEY,
                    report_id INTEGER NOT NULL REFERENCES reports(id)
                );
                CREATE TABLE IF NOT EXISTS call_segments (
                    id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    raw_row TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_segments (
                    report_id INTEGER NOT NULL REFERENCES reports(id),
                    segment_id INTEGER NOT NULL REFERENCES call_segments(id),
                    PRIMARY KEY (report_id, segment_id)
                );
                """
            )
            connection.commit()
        except sqlite3.DatabaseError as error:
            connection.rollback()
            raise StorageError("Metadata database has an unsupported format") from error
        finally:
            connection.close()

    def existing_report(self, fingerprint: str) -> int | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id FROM reports WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            return None if row is None else int(row[0])
        except sqlite3.DatabaseError as error:
            raise StorageError("Metadata database could not be read") from error
        finally:
            connection.close()

    def has_overlap(self, start: datetime | None, end: datetime | None) -> bool:
        if start is None or end is None or end < start:
            return False
        connection = self._connect()
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM reports WHERE period_start IS NOT NULL "
                    "AND period_end IS NOT NULL AND period_start <= ? AND period_end >= ? "
                    "LIMIT 1",
                    (end.isoformat(), start.isoformat()),
                ).fetchone()
                is not None
            )
        except sqlite3.DatabaseError as error:
            raise StorageError("Metadata database could not be read") from error
        finally:
            connection.close()

    def store_report(
        self,
        *,
        message_id: str,
        fingerprint: str,
        period_start: datetime | None,
        period_end: datetime | None,
        warnings: tuple[str, ...],
        records: list[CallRecord],
    ) -> bool:
        """Store one report transactionally; return False when it is a repeat."""
        connection = self._connect()
        try:
            with connection:
                found = connection.execute(
                    "SELECT id FROM reports WHERE fingerprint = ?", (fingerprint,)
                ).fetchone()
                if found:
                    connection.execute(
                        "INSERT OR IGNORE INTO source_messages (message_id, report_id) VALUES (?, ?)",
                        (message_id, found[0]),
                    )
                    return False
                cursor = connection.execute(
                    "INSERT INTO reports (fingerprint, period_start, period_end, imported_at, warnings) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        fingerprint,
                        period_start.isoformat() if period_start else None,
                        period_end.isoformat() if period_end else None,
                        datetime.now(UTC).isoformat(),
                        json.dumps(list(warnings)),
                    ),
                )
                report_id = cursor.lastrowid
                connection.execute(
                    "INSERT INTO source_messages (message_id, report_id) VALUES (?, ?)",
                    (message_id, report_id),
                )
                for record in records:
                    row = record.as_csv_row()
                    fingerprint_row = row_fingerprint(row)
                    connection.execute(
                        "INSERT OR IGNORE INTO call_segments (fingerprint, raw_row) VALUES (?, ?)",
                        (fingerprint_row, json.dumps(row)),
                    )
                    segment_id = connection.execute(
                        "SELECT id FROM call_segments WHERE fingerprint = ?",
                        (fingerprint_row,),
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT OR IGNORE INTO report_segments (report_id, segment_id) VALUES (?, ?)",
                        (report_id, segment_id),
                    )
            return True
        except sqlite3.DatabaseError as error:
            raise StorageError("Metadata database could not be updated") from error
        finally:
            connection.close()


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
