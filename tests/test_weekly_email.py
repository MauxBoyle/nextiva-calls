from datetime import date

import pytest

from nextiva_calls.weekly_email import (
    WeeklyReportEmailError,
    build_weekly_report_message,
    load_recipients,
    send_weekly_report,
)
from nextiva_calls.weekly_metrics import Week


class FakeSmtp:
    def __init__(self, *_args, **_kwargs):
        self.login_args = None
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message, from_addr, to_addrs):
        self.sent = (message, from_addr, to_addrs)


def test_load_recipients_allows_comments_and_blank_lines(tmp_path):
    recipients = tmp_path / "recipients.txt"
    recipients.write_text(
        "# Managers\n\ncharmaine@example.test\n todd@example.test \n",
        encoding="utf-8",
    )

    assert load_recipients(recipients) == (
        "charmaine@example.test",
        "todd@example.test",
    )


@pytest.mark.parametrize("content", ["not-an-address\n", "# no addresses\n\n"])
def test_load_recipients_rejects_invalid_or_empty_files(tmp_path, content):
    recipients = tmp_path / "recipients.txt"
    recipients.write_text(content, encoding="utf-8")

    with pytest.raises(WeeklyReportEmailError):
        load_recipients(recipients)


def test_message_contains_preview_and_pdf_attachment(tmp_path):
    pdf = tmp_path / "dashboard.pdf"
    pdf.write_bytes(b"%PDF-test")

    message = build_weekly_report_message(
        pdf,
        Week(date(2026, 8, 17)),
        17,
        "sender@example.test",
        ("one@example.test", "two@example.test"),
    )

    assert message["From"] == "sender@example.test"
    assert message["To"] == "one@example.test, two@example.test"
    assert "August 17, 2026" in message["Subject"]
    assert "17" in message.get_body().get_content()
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "dashboard.pdf"
    assert attachment.get_payload(decode=True) == b"%PDF-test"


def test_normal_send_adds_sender_as_hidden_bcc_envelope_recipient(tmp_path):
    pdf = tmp_path / "dashboard.pdf"
    pdf.write_bytes(b"%PDF-test")
    recipients = tmp_path / "recipients.txt"
    recipients.write_text("manager@example.test\n", encoding="utf-8")
    smtp = FakeSmtp()

    send_weekly_report(
        pdf,
        Week(date(2026, 8, 17)),
        3,
        "sender@example.test",
        "not-in-message",
        recipients_file=recipients,
        smtp_factory=lambda *_args, **_kwargs: smtp,
    )

    message, sender, envelope = smtp.sent
    assert sender == "sender@example.test"
    assert envelope == ("manager@example.test", "sender@example.test")
    assert message["Bcc"] is None
    assert "not-in-message" not in message.as_string()


def test_test_send_routes_only_to_sender(tmp_path):
    pdf = tmp_path / "dashboard.pdf"
    pdf.write_bytes(b"%PDF-test")
    smtp = FakeSmtp()

    send_weekly_report(
        pdf,
        Week(date(2026, 8, 17)),
        3,
        "sender@example.test",
        "password",
        test=True,
        recipients_file=tmp_path / "missing.txt",
        smtp_factory=lambda *_args, **_kwargs: smtp,
    )

    message, _sender, envelope = smtp.sent
    assert message["To"] == "sender@example.test"
    assert envelope == ("sender@example.test",)
