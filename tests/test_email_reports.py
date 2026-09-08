from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pytest

from nextiva_calls.email_reports import (
    MessageError,
    extract_report_url,
    message_body,
    report_identifier,
)

FIXTURES = Path(__file__).parent / "fixtures"
HOSTS = frozenset({"ct.nextiva.com"})


@pytest.mark.parametrize("filename", ["plain_report.eml", "multipart_report.eml"])
def test_extracts_plain_and_multipart_encoded_report_links(filename):
    message = BytesParser(policy=policy.default).parsebytes(
        (FIXTURES / filename).read_bytes()
    )
    url = extract_report_url(message, HOSTS)
    assert url.startswith("https://ct.nextiva.com/report?")


def make_message(body, subtype="plain"):
    message = EmailMessage()
    message.set_content(body, subtype=subtype)
    return message


def test_html_is_preferred_and_attachments_are_skipped():
    message = EmailMessage()
    message.set_content("https://ct.nextiva.com/plain")
    message.add_alternative(
        '<a HREF = "https://ct.nextiva.com/html">Missed Calls</a>', subtype="html"
    )
    message.add_attachment(
        b"https://ct.nextiva.com/attachment",
        maintype="text",
        subtype="plain",
        filename="secret.txt",
    )
    body, is_html = message_body(message)
    assert is_html is True
    assert "/html" in body
    assert extract_report_url(message, HOSTS).endswith("/html")


def test_html_selects_the_missed_calls_link_and_ignores_other_nextiva_links():
    message = make_message(
        """
        <a href="https://ct.nextiva.com/home">Home</a>
        <a href="https://ct.nextiva.com/report?token=report">\n Missed   Calls \n</a>
        <a href="https://ct.nextiva.com/preferences">Preferences</a>
        <a href="https://ct.nextiva.com/footer">Footer</a>
        """,
        subtype="html",
    )

    assert extract_report_url(message, HOSTS).endswith("token=report")


@pytest.mark.parametrize(
    "body, error",
    [
        ('<a href="https://ct.nextiva.com/report">Open report</a>', "exactly one"),
        (
            '<a href="https://ct.nextiva.com/one">Missed Calls</a>'
            '<a href="https://ct.nextiva.com/two">MISSED CALLS</a>',
            "exactly one",
        ),
        (
            '<a href="https://ct.nextiva.com/report">Missed Calls</a>'
            '<a href="https://ct.nextiva.com/report">Missed Calls</a>',
            "exactly one",
        ),
    ],
)
def test_html_requires_exactly_one_missed_calls_link(body, error):
    with pytest.raises(MessageError, match=error):
        extract_report_url(make_message(body, subtype="html"), HOSTS)


@pytest.mark.parametrize(
    "url",
    [
        "http://ct.nextiva.com/report",
        "https://ct.nextiva.com:444/report",
        "https://attacker@ct.nextiva.com/report",
        "https://evil.example.test/report",
    ],
)
def test_html_missed_calls_link_must_be_safe_and_allowlisted(url):
    message = make_message(
        f'<a href="{url}">Missed Calls</a>', subtype="html"
    )
    with pytest.raises(MessageError):
        extract_report_url(message, HOSTS)


@pytest.mark.parametrize(
    "body",
    [
        "http://ct.nextiva.com/report",
        "https://evil.example.test/report",
        "https://ct.nextiva.com.evil.example/report",
        "https://ct.nextiva.com:444/report",
        "https://attacker@ct.nextiva.com/report",
        "no link here",
    ],
)
def test_rejects_insecure_or_non_allowlisted_links(body):
    with pytest.raises(MessageError):
        extract_report_url(make_message(body), HOSTS)


def test_rejects_ambiguous_allowed_links_and_deduplicates_identical_links():
    with pytest.raises(MessageError, match="multiple"):
        extract_report_url(
            make_message("https://ct.nextiva.com/one https://ct.nextiva.com/two"), HOSTS
        )
    assert extract_report_url(
        make_message("https://ct.nextiva.com/one https://ct.nextiva.com/one"), HOSTS
    ).endswith("/one")


def test_unknown_declared_charset_falls_back_to_utf8():
    raw = (
        b"Content-Type: text/plain; charset=x-invented\n\nhttps://ct.nextiva.com/report"
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert extract_report_url(message, HOSTS).endswith("/report")


def test_report_identifier_prefers_normalized_message_id_then_uid_fallback():
    message = EmailMessage()
    message["Message-ID"] = "<ABC@Example.Test>"
    assert (
        report_identifier(message, server="IMAP.Test", uidvalidity="8", uid="12")
        == "message-id:<abc@example.test>"
    )
    del message["Message-ID"]
    assert (
        report_identifier(message, server="IMAP.Test", uidvalidity="8", uid="12")
        == "imap:imap.test:INBOX:8:12"
    )
