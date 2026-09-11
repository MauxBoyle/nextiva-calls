"""Command-line entry point for the Nextiva report importer."""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from nextiva_calls.config import Config, ConfigError
from nextiva_calls.holiday_calendar import resolve_holiday_calendar
from nextiva_calls.importer import run_import
from nextiva_calls.mailbox import MailboxError
from nextiva_calls.segments import AgentLookupError, load_agent_lookup
from nextiva_calls.storage import StorageError


def configure_logging(environ: Mapping[str, str] | None = None) -> None:
    """Configure safe console logging and optional local file logging."""
    values = os.environ if environ is None else environ
    log_level = values.get("LOG_LEVEL", "INFO")
    log_file = values.get("LOG_FILE", "app.log")
    logger.remove()
    logger.add(sys.stderr, level=log_level)
    if log_file:
        logger.add(Path(log_file), level="DEBUG", rotation="50 KB", retention=1)


def _weekly_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nextiva_calls weekly-report")
    parser.add_argument("--week-start", metavar="YYYY-MM-DD")
    parser.add_argument("--output", type=Path, metavar="PATH")
    return parser


def _run_weekly_report(arguments: list[str]) -> int:
    """Generate a weekly PDF without requiring mailbox credentials."""
    from nextiva_calls.reconstruction import MEMBERSHIP_SIMULTANEOUS_FROM
    from nextiva_calls.storage import save_candidate_calls
    from nextiva_calls.weekly_metrics import (
        load_candidates,
        parse_week_start,
        summarize_week,
        week_for,
    )
    from nextiva_calls.weekly_report import render_weekly_report

    parsed = _weekly_parser().parse_args(arguments)
    week = parse_week_start(parsed.week_start) if parsed.week_start else week_for()
    raw = Path(os.environ.get("NEXTIVA_OUTPUT_FILE", "NextivaCallData.csv"))
    candidates = Path(
        os.environ.get("NEXTIVA_CANDIDATE_CALLS_FILE", "")
        or raw.with_suffix(".candidate-calls.csv")
    )
    metadata = Path(
        os.environ.get("NEXTIVA_METADATA_FILE", "")
        or raw.with_suffix(".metadata.sqlite3")
    )
    analysis = Path(
        os.environ.get("NEXTIVA_ANALYSIS_FILE", "")
        or raw.with_suffix(".analysis.csv")
    )
    lookup_value = os.environ.get("NEXTIVA_AGENT_LOOKUP_FILE", "").strip()
    if not lookup_value:
        raise ConfigError("NEXTIVA_AGENT_LOOKUP_FILE is required for weekly reports")
    overrides_file = Path(
        os.environ.get(
            "NEXTIVA_HOLIDAY_OVERRIDES_FILE",
            os.environ.get("NEXTIVA_CLOSURE_DATES_FILE", "closure_dates.csv"),
        ).strip()
    )
    membership_hunt_group = os.environ.get(
        "NEXTIVA_MEMBERSHIP_HUNT_GROUP", "Membership"
    ).strip()
    if not membership_hunt_group:
        raise ConfigError("NEXTIVA_MEMBERSHIP_HUNT_GROUP must not be blank")
    output = parsed.output or Path("reports") / (
        f"Nextiva_Weekly_{week.start.isoformat()}_to_{week.end.isoformat()}.pdf"
    )
    lookup = load_agent_lookup(Path(lookup_value))
    cache = Path(
        os.environ.get("NEXTIVA_HOLIDAY_CACHE_FILE", "")
        or raw.with_suffix(".opm-holidays.ics")
    )
    holiday_calendar, used_cache = resolve_holiday_calendar(
        week.prior.start,
        week.end,
        cache_path=cache,
        overrides_path=overrides_file,
        url=os.environ.get("NEXTIVA_OPM_CALENDAR_URL", "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/holidays.ics"),
    )
    if used_cache:
        logger.warning("Using cached OPM holiday calendar after refresh failure")
    try:
        rows = load_candidates(candidates)
    except StorageError as error:
        if str(error) != "Candidate calls CSV has an unexpected header":
            raise
        # Candidate calls are derived data. Rebuilding an older schema from
        # its analysis CSV lets historical local exports use new metrics.
        save_candidate_calls(
            candidates,
            analysis,
            membership_hunt_group=membership_hunt_group,
            membership_simultaneous_from=os.environ.get(
                "NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM",
                MEMBERSHIP_SIMULTANEOUS_FROM,
            ),
        )
        rows = load_candidates(candidates)
    render_weekly_report(
        summarize_week(rows, week, lookup, metadata, membership_hunt_group, "combined", holiday_calendar),
        summarize_week(rows, week.prior, lookup, metadata, membership_hunt_group, "combined", holiday_calendar),
        output,
        membership=summarize_week(rows, week, lookup, metadata, membership_hunt_group, "Membership", holiday_calendar),
        certification=summarize_week(rows, week, lookup, metadata, membership_hunt_group, "Certification", holiday_calendar),
    )
    logger.info("Wrote weekly report to {}", output)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return zero on complete success, otherwise one."""
    try:
        configure_logging()
        command = sys.argv[1:] if argv is None else argv
        if command and command[0] == "weekly-report":
            return _run_weekly_report(command[1:])
        config = Config.from_env()
        return 0 if run_import(config) else 1
    except ConfigError as error:
        logger.error("Configuration error: {}", error)
    except MailboxError:
        logger.error("Mailbox connection or authentication failed")
    except StorageError as error:
        logger.error("Local data error: {}", error)
    except (AgentLookupError, ValueError) as error:
        logger.error("Weekly report configuration error: {}", error)
    except SystemExit as error:
        # argparse has already printed a short, useful usage message.
        return int(error.code) if isinstance(error.code, int) else 1
    except Exception:
        logger.error("Import failed unexpectedly")
    return 1
