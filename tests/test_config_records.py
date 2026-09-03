from pathlib import Path

import pytest

from nextiva_calls.config import Config, ConfigError
from nextiva_calls.records import CallRecord, RecordError, duration_to_seconds

BASE_ENV = {
    "EMAIL_USERNAME": "learner@example.test",
    "EMAIL_APP_PASSWORD": "invented-secret",
    "NEXTIVA_EMAIL_SUBJECT": "Daily Nextiva Report",
}


def test_config_defaults_and_derived_state_file():
    config = Config.from_env(BASE_ENV)
    assert config.imap_server == "imap.gmail.com"
    assert config.email_sender == "analytics@nextiva.com"
    assert config.output_file == Path("NextivaCallData.csv")
    assert config.state_file == Path("NextivaCallData.state.json")
    assert config.allowed_hosts == frozenset({"ct.nextiva.com"})
    assert config.report_timeout_seconds == 30


def test_config_custom_values():
    config = Config.from_env(
        BASE_ENV
        | {
            "EMAIL_IMAP_SERVER": "mail.example.test",
            "NEXTIVA_EMAIL_SENDER": "reports@example.test",
            "NEXTIVA_OUTPUT_FILE": "output/calls.csv",
            "NEXTIVA_STATE_FILE": "state/custom.json",
            "NEXTIVA_ALLOWED_HOSTS": " CT.NEXTIVA.COM, reports.example.test. ",
            "NEXTIVA_REPORT_TIMEOUT_SECONDS": "4.5",
        }
    )
    assert config.state_file == Path("state/custom.json")
    assert config.allowed_hosts == frozenset({"ct.nextiva.com", "reports.example.test"})
    assert config.report_timeout_seconds == 4.5


@pytest.mark.parametrize("missing", list(BASE_ENV))
def test_config_requires_values_without_leaking_password(missing):
    values = BASE_ENV | {missing: "  "}
    with pytest.raises(ConfigError) as caught:
        Config.from_env(values)
    assert missing in str(caught.value)
    assert "invented-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"NEXTIVA_ALLOWED_HOSTS": ""}, "hostnames"),
        ({"NEXTIVA_ALLOWED_HOSTS": "https://ct.nextiva.com"}, "hostnames"),
        ({"NEXTIVA_ALLOWED_HOSTS": "ct.nextiva.com:443"}, "hostnames"),
        ({"NEXTIVA_REPORT_TIMEOUT_SECONDS": "soon"}, "number"),
        ({"NEXTIVA_REPORT_TIMEOUT_SECONDS": "0"}, "greater than zero"),
        ({"NEXTIVA_OUTPUT_FILE": ""}, "name a file"),
        (
            {"NEXTIVA_OUTPUT_FILE": "same.csv", "NEXTIVA_STATE_FILE": "same.csv"},
            "must differ",
        ),
        ({"EMAIL_IMAP_SERVER": "  "}, "must not be blank"),
        ({"NEXTIVA_EMAIL_SENDER": "not-an-address"}, "email address"),
    ],
)
def test_config_rejects_unusable_values(extra, message):
    with pytest.raises(ConfigError, match=message):
        Config.from_env(BASE_ENV | extra)


@pytest.mark.parametrize(
    ("display", "seconds"),
    [("4s", 4), (" 2m  3s ", 123), ("1h", 3600), ("1h 2m 3s", 3723), ("0s", 0)],
)
def test_duration_conversion(display, seconds):
    assert duration_to_seconds(display) == seconds


@pytest.mark.parametrize("display", ["", "90", "1:30", "1m 60s", "1.5m", "words"])
def test_duration_rejects_malformed_values(display):
    with pytest.raises(RecordError):
        duration_to_seconds(display)


def test_call_record_cleans_whitespace_but_preserves_phone_format():
    record = CallRecord.from_cells(
        [
            "  Alex   Example ",
            " Jan 2, 2026  9:30 AM ",
            "1m 2s",
            " Inbound ",
            " Yes ",
            " (555) 010-0100 ",
            "+1 555 010 0200",
        ]
    )
    assert record.name == "Alex Example"
    assert record.time_of_call == "Jan 2, 2026 9:30 AM"
    assert record.from_number == "(555) 010-0100"
    assert record.as_csv_row()[2] == "62"


@pytest.mark.parametrize(
    "cells",
    [
        ["too", "few"],
        ["Name", "not a date", "1s", "Out", "Yes", "111", "222"],
    ],
)
def test_call_record_rejects_bad_rows(cells):
    with pytest.raises(RecordError):
        CallRecord.from_cells(cells)
