"""Environment-based application configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class Config:
    """Validated settings used by the importer."""

    email_username: str
    email_app_password: str
    email_subject: str
    imap_server: str = "imap.gmail.com"
    email_sender: str = "analytics@nextiva.com"
    output_file: Path = Path("NextivaCallData.csv")
    allowed_hosts: frozenset[str] = frozenset({"ct.nextiva.com"})
    report_timeout_seconds: float = 30
    state_file: Path = Path("NextivaCallData.state.json")
    metadata_file: Path | None = None
    analysis_file: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        """Build configuration from environment variables.

        Only variable names are included in errors; secret values are never shown.
        """
        values = os.environ if environ is None else environ
        required = ("EMAIL_USERNAME", "EMAIL_APP_PASSWORD", "NEXTIVA_EMAIL_SUBJECT")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

        raw_hosts = values.get("NEXTIVA_ALLOWED_HOSTS", "ct.nextiva.com")
        hosts = frozenset(
            host.strip().lower().rstrip(".")
            for host in raw_hosts.split(",")
            if host.strip()
        )
        if not hosts or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is None
            for host in hosts
        ):
            raise ConfigError("NEXTIVA_ALLOWED_HOSTS must contain hostnames")

        raw_timeout = values.get("NEXTIVA_REPORT_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw_timeout)
        except ValueError as error:
            raise ConfigError(
                "NEXTIVA_REPORT_TIMEOUT_SECONDS must be a number"
            ) from error
        if timeout <= 0:
            raise ConfigError(
                "NEXTIVA_REPORT_TIMEOUT_SECONDS must be greater than zero"
            )

        output = Path(values.get("NEXTIVA_OUTPUT_FILE", "NextivaCallData.csv"))
        if output.name == "":
            raise ConfigError("NEXTIVA_OUTPUT_FILE must name a file")
        imap_server = values.get("EMAIL_IMAP_SERVER", "imap.gmail.com").strip()
        email_sender = values.get(
            "NEXTIVA_EMAIL_SENDER", "analytics@nextiva.com"
        ).strip()
        if not imap_server:
            raise ConfigError("EMAIL_IMAP_SERVER must not be blank")
        if not email_sender or "@" not in email_sender:
            raise ConfigError("NEXTIVA_EMAIL_SENDER must be an email address")
        state_value = values.get("NEXTIVA_STATE_FILE", "").strip()
        state = Path(state_value) if state_value else output.with_suffix(".state.json")
        metadata_value = values.get("NEXTIVA_METADATA_FILE", "").strip()
        metadata = (
            Path(metadata_value)
            if metadata_value
            else output.with_suffix(".metadata.sqlite3")
        )
        analysis_value = values.get("NEXTIVA_ANALYSIS_FILE", "").strip()
        analysis = (
            Path(analysis_value)
            if analysis_value
            else output.with_suffix(".analysis.csv")
        )
        named_paths = {
            "NEXTIVA_OUTPUT_FILE": output,
            "NEXTIVA_STATE_FILE": state,
            "NEXTIVA_METADATA_FILE": metadata,
            "NEXTIVA_ANALYSIS_FILE": analysis,
        }
        resolved_paths = [path.resolve() for path in named_paths.values()]
        if len(resolved_paths) != len(set(resolved_paths)):
            raise ConfigError("Output, state, metadata, and analysis files must differ")

        return cls(
            email_username=values["EMAIL_USERNAME"].strip(),
            email_app_password=values["EMAIL_APP_PASSWORD"],
            email_subject=values["NEXTIVA_EMAIL_SUBJECT"].strip(),
            imap_server=imap_server,
            email_sender=email_sender,
            output_file=output,
            allowed_hosts=hosts,
            report_timeout_seconds=timeout,
            state_file=state,
            metadata_file=metadata,
            analysis_file=analysis,
        )
