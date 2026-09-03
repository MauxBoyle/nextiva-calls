import csv
import json

import pytest

from nextiva_calls.records import CSV_COLUMNS, CallRecord
from nextiva_calls.storage import (
    StorageError,
    load_csv,
    load_state,
    save_records,
    save_state,
)


def record(name="Alex"):
    return CallRecord(
        name, "Jan 2 2026 9:30 AM", 62, "Inbound", "Yes", "555-0100", "555-0200"
    )


def test_new_csv_has_exact_header_and_repeated_rows_are_filtered(tmp_path):
    destination = tmp_path / "calls.csv"
    assert save_records(destination, [record(), record()]) == 1
    assert save_records(destination, [record(), record("Blair")]) == 1
    with destination.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == list(CSV_COLUMNS)
    assert len(rows) == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_comparison_normalizes_all_existing_fields(tmp_path):
    destination = tmp_path / "calls.csv"
    destination.write_text(
        ",".join(CSV_COLUMNS)
        + "\n Alex ,Jan 2 2026 9:30 AM,62,Inbound,Yes,555-0100,555-0200\n",
        encoding="utf-8",
    )
    assert save_records(destination, [record()]) == 0


def test_existing_csv_header_and_rows_are_validated(tmp_path):
    destination = tmp_path / "calls.csv"
    destination.write_text("Bad,Header\n", encoding="utf-8")
    with pytest.raises(StorageError, match="header"):
        save_records(destination, [record()])
    destination.write_text(",".join(CSV_COLUMNS) + "\nonly,short\n", encoding="utf-8")
    with pytest.raises(StorageError, match="malformed"):
        load_csv(destination)


def test_state_round_trip_and_missing_state(tmp_path):
    destination = tmp_path / "nested" / "calls.state.json"
    assert load_state(destination) == set()
    save_state(destination, {"message-id:<two>", "message-id:<one>"})
    assert load_state(destination) == {"message-id:<one>", "message-id:<two>"}
    payload = json.loads(destination.read_text())
    assert payload["version"] == 1
    assert payload["processed"] == sorted(payload["processed"])


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"version": 2, "processed": []}',
        '{"version": 1, "processed": [3]}',
        "[]",
    ],
)
def test_corrupt_or_unsupported_state_is_rejected(tmp_path, content):
    destination = tmp_path / "state.json"
    destination.write_text(content, encoding="utf-8")
    with pytest.raises(StorageError):
        load_state(destination)


def test_atomic_replace_failure_preserves_existing_csv(tmp_path, monkeypatch):
    destination = tmp_path / "calls.csv"
    save_records(destination, [record()])
    original = destination.read_bytes()

    def fail_replace(source, target):
        raise OSError("invented failure")

    monkeypatch.setattr("nextiva_calls.storage.os.replace", fail_replace)
    with pytest.raises(StorageError, match="written"):
        save_records(destination, [record("Blair")])
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_replace_failure_preserves_existing_state(tmp_path, monkeypatch):
    destination = tmp_path / "state.json"
    save_state(destination, {"one"})
    original = destination.read_bytes()
    monkeypatch.setattr(
        "nextiva_calls.storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(StorageError):
        save_state(destination, {"one", "two"})
    assert destination.read_bytes() == original
