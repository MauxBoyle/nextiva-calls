"""Coordinate mailbox, report, CSV, and state operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from loguru import logger

from nextiva_calls.config import Config
from nextiva_calls.email_reports import (
    MessageError,
    decoded_header,
    extract_report_url,
    report_identifier,
)
from nextiva_calls.mailbox import ImapMailbox, RawMessage
from nextiva_calls.records import CallRecord
from nextiva_calls.report import ReportError, load_report
from nextiva_calls.storage import load_state, save_records, save_state

MailboxFactory = Callable[[Config], Iterable[RawMessage]]
ReportLoader = Callable[[str, float], list[CallRecord]]


def _default_messages(config: Config) -> Iterable[RawMessage]:
    mailbox = ImapMailbox(
        config.imap_server, config.email_username, config.email_app_password
    )
    return mailbox.messages(config.email_sender, config.email_subject)


def run_import(
    config: Config,
    *,
    mailbox_factory: MailboxFactory = _default_messages,
    report_loader: ReportLoader = load_report,
    csv_writer: Callable[[Path, list[CallRecord]], int] = save_records,
    state_reader: Callable[[Path], set[str]] = load_state,
    state_writer: Callable[[Path, set[str]], None] = save_state,
) -> bool:
    """Import all new reports, returning whether every required report succeeded."""
    processed = state_reader(config.state_file)
    matched = imported = added_rows = report_failures = 0

    for raw in mailbox_factory(config):
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw.content)
            sender = parseaddr(decoded_header(message, "From"))[1].casefold()
            subject = decoded_header(message, "Subject")
            if (
                sender != config.email_sender.casefold()
                or subject != config.email_subject
            ):
                continue
            matched += 1
            identifier = report_identifier(
                message,
                server=config.imap_server,
                uidvalidity=raw.uidvalidity,
                uid=raw.uid,
            )
            if identifier in processed:
                continue
            try:
                report_date = (
                    parsedate_to_datetime(message.get("Date", "")).date().isoformat()
                )
            except (TypeError, ValueError, OverflowError):
                report_date = "unknown"
            logger.info("Processing report message dated {}", report_date)
            url = extract_report_url(message, config.allowed_hosts)
            records = report_loader(url, config.report_timeout_seconds)
        except (MessageError, ReportError, ValueError):
            report_failures += 1
            logger.error("Skipped one matching message because its report was invalid")
            continue

        added_rows += csv_writer(config.output_file, records)
        processed.add(identifier)
        state_writer(config.state_file, processed)
        imported += 1
        logger.info("Imported report with {} validated call rows", len(records))

    logger.info(
        "Import complete: {} matching messages, {} reports processed, {} rows added",
        matched,
        imported,
        added_rows,
    )
    return report_failures == 0
