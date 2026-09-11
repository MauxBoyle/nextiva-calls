"""Coordinate mailbox, report, CSV, and state operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
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
from nextiva_calls.holiday_calendar import resolve_holiday_calendar
from nextiva_calls.mailbox import ImapMailbox, RawMessage
from nextiva_calls.records import CallRecord
from nextiva_calls.report import ReportError, ReportResult, load_report
from nextiva_calls.segments import AgentLookupError, load_agent_lookup
from nextiva_calls.storage import (
    MetadataStore,
    StorageError,
    append_records,
    load_csv,
    load_state,
    report_fingerprint,
    save_analysis,
    save_candidate_calls,
    save_records,
    save_state,
)

MailboxFactory = Callable[[Config], Iterable[RawMessage]]
ReportLoader = Callable[[str, float], ReportResult | list[CallRecord]]


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
    metadata_store_factory: Callable[[Path], MetadataStore] = MetadataStore,
) -> bool:
    """Import all new reports, returning whether every required report succeeded."""
    # Validate this external dependency before any metadata, raw CSV, or state
    # file can be created or changed.
    try:
        lookup = load_agent_lookup(config.agent_lookup_file)
    except AgentLookupError as error:
        raise StorageError("Agent lookup file is invalid") from error
    year = date.today().year
    try:
        holiday_calendar, cached_calendar = resolve_holiday_calendar(
            date(year, 1, 1),
            date(year, 12, 31),
            cache_path=config.holiday_cache_file,
            overrides_path=config.closure_dates_file,
            url=config.opm_calendar_url,
        )
    except ValueError as error:
        raise StorageError("Holiday calendar is unavailable") from error
    if cached_calendar:
        logger.warning("Using cached OPM holiday calendar after refresh failure")
    processed = state_reader(config.state_file)
    metadata_path = config.metadata_file or config.output_file.with_suffix(
        ".metadata.sqlite3"
    )
    analysis_path = config.analysis_file or config.output_file.with_suffix(
        ".analysis.csv"
    )
    candidate_path = config.candidate_calls_file or config.output_file.with_suffix(
        ".candidate-calls.csv"
    )
    metadata = metadata_store_factory(metadata_path)
    metadata.initialize()
    matched = imported = added_rows = report_failures = 0

    for raw in mailbox_factory(config):
        message = BytesParser(policy=policy.default).parsebytes(raw.content)
        sender = parseaddr(decoded_header(message, "From"))[1].casefold()
        subject = decoded_header(message, "Subject")
        if sender != config.email_sender.casefold() or subject != config.email_subject:
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
        try:
            url = extract_report_url(message, config.allowed_hosts)
        except MessageError:
            report_failures += 1
            logger.error(
                "Skipped one matching message because its report link could not be "
                "extracted or validated"
            )
            continue
        try:
            loaded = report_loader(url, config.report_timeout_seconds)
            # Supporting a record list keeps custom loaders written for older
            # versions compatible while the Selenium loader returns ReportResult.
            result = loaded if isinstance(loaded, ReportResult) else ReportResult(loaded)
        except ReportError as error:
            report_failures += 1
            logger.debug("Report loading or parsing failed: {}", error)
            logger.error(
                "Skipped one matching message because its report could not be "
                "loaded or parsed"
            )
            continue
        except ValueError:
            report_failures += 1
            logger.debug("Report loading or parsing failed validation")
            logger.error(
                "Skipped one matching message because its report could not be "
                "loaded or parsed"
            )
            continue

        fingerprint = report_fingerprint(
            result.period_start, result.period_end, result.records
        )
        warnings = list(result.warnings)
        if metadata.existing_report(fingerprint) is not None:
            metadata.store_report(
                message_id=identifier,
                fingerprint=fingerprint,
                period_start=result.period_start,
                period_end=result.period_end,
                warnings=tuple(warnings),
                records=result.records,
            )
            logger.warning("Repeated report detected; no raw rows were added")
        else:
            if metadata.has_overlap(result.period_start, result.period_end):
                warnings.append("Report period overlaps an earlier imported report")
                logger.warning("Report period overlaps an earlier imported report")
            # Validate metadata before touching the raw file.  This prevents a
            # corrupt SQLite file from turning a message into a partial import.
            # The raw export is an append-only audit trail.  Exact repeats are
            # deliberately retained here and removed only in the analysis view.
            additions = result.records
            # csv_writer remains injectable for existing callers and tests. The
            # built-in coordinator uses append-only storage for raw data.
            if csv_writer is save_records:
                added_rows += append_records(config.output_file, additions)
            else:
                added_rows += csv_writer(config.output_file, additions)
            metadata.store_report(
                message_id=identifier,
                fingerprint=fingerprint,
                period_start=result.period_start,
                period_end=result.period_end,
                warnings=tuple(warnings),
                records=result.records,
            )
            written = save_analysis(
                analysis_path, config.output_file, lookup, holiday_calendar
            )
            save_candidate_calls(
                candidate_path,
                analysis_path,
                membership_hunt_group=config.membership_hunt_group,
                membership_simultaneous_from=config.membership_simultaneous_from,
            )
            omitted = len(load_csv(config.output_file)) - written
            if omitted:
                logger.info("Analysis omitted {} exact duplicate raw row(s)", omitted)
        for warning in warnings:
            logger.warning("{}", warning)
        processed.add(identifier)
        state_writer(config.state_file, processed)
        imported += 1
        logger.info("Imported report with {} validated call rows", len(result.records))

    logger.info(
        "Import complete: {} matching messages, {} reports processed, {} rows added",
        matched,
        imported,
        added_rows,
    )
    return report_failures == 0
