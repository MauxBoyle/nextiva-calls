"""Email delivery helpers for the optional weekly PDF dashboard."""

from __future__ import annotations

import re
import smtplib
from collections.abc import Callable, Sequence
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from nextiva_calls.weekly_metrics import Week


class WeeklyReportEmailError(ValueError):
    """Raised when a weekly dashboard cannot be prepared or delivered."""


# This deliberately accepts ordinary mailbox addresses only.  The recipient
# file is an operational safety boundary, not a place for display names.
_EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_recipients(path: Path = Path("config/weekly_report_recipients.txt")) -> tuple[str, ...]:
    """Load one valid recipient address per non-comment line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise WeeklyReportEmailError("Weekly report recipient file could not be read") from error

    recipients: list[str] = []
    for number, raw_line in enumerate(lines, start=1):
        address = raw_line.strip()
        if not address or address.startswith("#"):
            continue
        _, parsed = parseaddr(address)
        if parsed != address or _EMAIL_ADDRESS.fullmatch(address) is None:
            raise WeeklyReportEmailError(
                f"Weekly report recipient file has an invalid address on line {number}"
            )
        if address not in recipients:
            recipients.append(address)
    if not recipients:
        raise WeeklyReportEmailError("Weekly report recipient file has no recipient addresses")
    return tuple(recipients)


def format_report_period(week: Week) -> str:
    """Format the selected seven-day period without OS-specific strftime codes."""
    def format_day(day) -> str:
        return f"{day.strftime('%B')} {day.day}, {day.year}"

    return f"{format_day(week.start)} – {format_day(week.end)}"


def build_weekly_report_message(
    pdf_path: Path,
    week: Week,
    combined_calls: int,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    """Build the non-sensitive dashboard email and attach its already-made PDF."""
    try:
        pdf = pdf_path.read_bytes()
    except OSError as error:
        raise WeeklyReportEmailError("Generated weekly PDF could not be attached") from error

    period = format_report_period(week)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = f"Membership & Certification Calls Dashboard — {period}"
    message.set_content(
        f"The attached Membership & Certification Calls Dashboard covers {period}.\n\n"
        f"Combined calls for this seven-day period: {combined_calls}."
    )
    message.add_attachment(
        pdf,
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )
    return message


SmtpFactory = Callable[..., smtplib.SMTP_SSL]


def send_weekly_report(
    pdf_path: Path,
    week: Week,
    combined_calls: int,
    username: str,
    app_password: str,
    *,
    test: bool = False,
    recipients_file: Path = Path("config/weekly_report_recipients.txt"),
    smtp_factory: SmtpFactory = smtplib.SMTP_SSL,
) -> None:
    """Deliver a dashboard, with a sender-only route for safe test sends."""
    recipients = (username,) if test else load_recipients(recipients_file)
    if not test:
        recipients = tuple(
            address for address in recipients if address.casefold() != username.casefold()
        )
        if not recipients:
            raise WeeklyReportEmailError(
                "Weekly report recipient file has no recipients other than EMAIL_USERNAME"
            )
    message = build_weekly_report_message(
        pdf_path, week, combined_calls, username, recipients
    )
    # In normal delivery, the sender receives an envelope-only BCC copy.  No
    # Bcc header is created, so it cannot be exposed to visible recipients.
    envelope_recipients = recipients if test else (*recipients, username)
    try:
        with smtp_factory("smtp.gmail.com", 465, timeout=30) as server:
            server.login(username, app_password)
            server.send_message(message, from_addr=username, to_addrs=envelope_recipients)
    except (OSError, smtplib.SMTPException) as error:
        # Do not include SMTP exception text: providers can echo credentials
        # or recipient addresses in it.
        raise WeeklyReportEmailError("Weekly report email could not be sent") from error
