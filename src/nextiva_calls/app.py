"""Command-line entry point for the Nextiva report importer."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from nextiva_calls.config import Config, ConfigError
from nextiva_calls.importer import run_import
from nextiva_calls.mailbox import MailboxError
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


def main() -> int:
    """Run the CLI and return zero on complete success, otherwise one."""
    try:
        configure_logging()
        config = Config.from_env()
        return 0 if run_import(config) else 1
    except ConfigError as error:
        logger.error("Configuration error: {}", error)
    except MailboxError:
        logger.error("Mailbox connection or authentication failed")
    except StorageError as error:
        logger.error("Local data error: {}", error)
    except Exception:
        logger.error("Import failed unexpectedly")
    return 1
