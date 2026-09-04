import csv
from email.message import EmailMessage

import pytest

from nextiva_calls.config import Config
from nextiva_calls.importer import run_import
from nextiva_calls.mailbox import RawMessage
from nextiva_calls.records import CallRecord
from nextiva_calls.report import ReportError, ReportResult
from nextiva_calls.storage import StorageError, load_csv, load_state


def config(tmp_path):
    lookup = tmp_path / "agents.csv"
    lookup.write_text("phone_number,agent\n555-0200,Alex\n", encoding="utf-8")
    return Config(
        email_username="learner@example.test",
        email_app_password="invented-secret",
        email_subject="Daily Report",
        output_file=tmp_path / "calls.csv",
        state_file=tmp_path / "calls.state.json",
        agent_lookup_file=lookup,
    )


def raw_message(
    uid="1",
    message_id="one",
    sender="analytics@nextiva.com",
    subject="Daily Report",
    link="https://ct.nextiva.com/inactive",
):
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    if message_id is not None:
        message["Message-ID"] = f"<{message_id}@example.test>"
    message.set_content(link)
    return RawMessage(uid=uid, uidvalidity="9", content=message.as_bytes())


RECORD = CallRecord(
    "Alex", "Jan 2 2026 9:30 AM", 2, "Inbound", "Yes", "555-0100", "555-0200"
)


def test_no_matches_is_success_and_does_not_write(tmp_path):
    writes = []
    result = run_import(
        config(tmp_path),
        mailbox_factory=lambda _: [],
        csv_writer=lambda *_: writes.append("csv"),
        state_reader=lambda _: set(),
        state_writer=lambda *_: writes.append("state"),
    )
    assert result is True
    assert writes == []


def test_import_filters_exact_sender_subject_tracks_id_and_writes_state_after_csv(
    tmp_path,
):
    events = []
    messages = [
        raw_message(uid="1", sender="other@example.test"),
        raw_message(uid="2", subject="Different"),
        raw_message(uid="3", message_id="already"),
        raw_message(uid="4", message_id="new"),
    ]

    def csv_writer(path, records):
        events.append(("csv", records))
        return 1

    def state_writer(path, processed):
        events.append(("state", set(processed)))

    result = run_import(
        config(tmp_path),
        mailbox_factory=lambda _: messages,
        report_loader=lambda url, timeout: [RECORD],
        csv_writer=csv_writer,
        state_reader=lambda _: {"message-id:<already@example.test>"},
        state_writer=state_writer,
    )
    assert result is True
    assert [event[0] for event in events] == ["csv", "state"]
    assert "message-id:<new@example.test>" in events[1][1]


def test_uid_fallback_is_saved(tmp_path):
    saved = []
    run_import(
        config(tmp_path),
        mailbox_factory=lambda _: [raw_message(uid="42", message_id=None)],
        report_loader=lambda *_: [RECORD],
        csv_writer=lambda *_: 1,
        state_reader=lambda _: set(),
        state_writer=lambda path, processed: saved.extend(processed),
    )
    assert saved == ["imap:imap.gmail.com:INBOX:9:42"]


def test_message_report_failures_continue_but_return_failure(tmp_path):
    loaded = []

    def loader(url, timeout):
        loaded.append(url)
        if "bad" in url:
            raise ReportError("secret url omitted")
        return [RECORD]

    messages = [
        raw_message(uid="1", message_id="unsafe", link="http://ct.nextiva.com/report"),
        raw_message(uid="2", message_id="bad", link="https://ct.nextiva.com/bad"),
        raw_message(uid="3", message_id="good", link="https://ct.nextiva.com/good"),
    ]
    result = run_import(
        config(tmp_path),
        mailbox_factory=lambda _: messages,
        report_loader=loader,
        csv_writer=lambda *_: 1,
        state_reader=lambda _: set(),
        state_writer=lambda *_: None,
    )
    assert result is False
    assert loaded[-1].endswith("/good")


def test_csv_and_state_failures_stop_immediately(tmp_path):
    common = {
        "mailbox_factory": lambda _: [raw_message()],
        "report_loader": lambda *_: [RECORD],
        "state_reader": lambda _: set(),
    }
    state_calls = []
    with pytest.raises(StorageError):
        run_import(
            config(tmp_path),
            csv_writer=lambda *_: (_ for _ in ()).throw(StorageError("csv")),
            state_writer=lambda *_: state_calls.append(True),
            **common,
        )
    assert state_calls == []

    events = []
    with pytest.raises(StorageError):
        run_import(
            config(tmp_path),
            csv_writer=lambda *_: events.append("csv") or 1,
            state_writer=lambda *_: (_ for _ in ()).throw(StorageError("state")),
            **common,
        )
    assert events == ["csv"]


def test_repeated_report_under_a_new_message_keeps_raw_and_analysis_unique(tmp_path):
    settings = config(tmp_path)
    result = ReportResult([RECORD], warnings=("Report period was not found",))
    assert run_import(
        settings,
        mailbox_factory=lambda _: [raw_message(uid="1", message_id="one")],
        report_loader=lambda *_: result,
    )
    raw_before = (tmp_path / "calls.csv").read_bytes()
    analysis_before = (tmp_path / "calls.analysis.csv").read_bytes()
    assert run_import(
        settings,
        mailbox_factory=lambda _: [raw_message(uid="2", message_id="two")],
        report_loader=lambda *_: result,
    )
    assert (tmp_path / "calls.csv").read_bytes() == raw_before
    assert (tmp_path / "calls.analysis.csv").read_bytes() == analysis_before
    assert len(load_csv(tmp_path / "calls.csv")) == 1
    assert load_state(tmp_path / "calls.state.json") == {
        "message-id:<one@example.test>",
        "message-id:<two@example.test>",
    }


def test_invalid_lookup_fails_before_creating_raw_or_metadata_files(tmp_path):
    settings = config(tmp_path)
    settings.agent_lookup_file.write_text("wrong,header\n1,Alex\n", encoding="utf-8")
    with pytest.raises(StorageError, match="lookup"):
        run_import(
            settings,
            mailbox_factory=lambda _: [raw_message()],
            report_loader=lambda *_: [RECORD],
        )
    assert not settings.output_file.exists()
    assert not (tmp_path / "calls.metadata.sqlite3").exists()


def test_raw_duplicates_are_preserved_but_analysis_omits_them(tmp_path):
    settings = config(tmp_path)
    result = ReportResult([RECORD, RECORD])
    assert run_import(
        settings,
        mailbox_factory=lambda _: [raw_message()],
        report_loader=lambda *_: result,
    )
    assert len(load_csv(settings.output_file)) == 2
    analysis = settings.output_file.with_suffix(".analysis.csv")
    with analysis.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.reader(stream))) == 2
