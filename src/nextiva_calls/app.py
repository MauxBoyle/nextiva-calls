"""Command-line entry point for the Nextiva report importer."""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from nextiva_calls.config import Config, ConfigError
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
    lookup_value = os.environ.get("NEXTIVA_AGENT_LOOKUP_FILE", "").strip()
    if not lookup_value:
        raise ConfigError("NEXTIVA_AGENT_LOOKUP_FILE is required for weekly reports")
    membership_hunt_group = os.environ.get(
        "NEXTIVA_MEMBERSHIP_HUNT_GROUP", "Membership"
    ).strip()
    if not membership_hunt_group:
        raise ConfigError("NEXTIVA_MEMBERSHIP_HUNT_GROUP must not be blank")
    output = parsed.output or Path("reports") / (
        f"Nextiva_Weekly_{week.start.isoformat()}_to_{week.end.isoformat()}.pdf"
    )
    lookup = load_agent_lookup(Path(lookup_value))
    rows = load_candidates(candidates)
    render_weekly_report(
        summarize_week(rows, week, lookup, metadata, membership_hunt_group),
        summarize_week(rows, week.prior, lookup, metadata, membership_hunt_group),
        output,
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
