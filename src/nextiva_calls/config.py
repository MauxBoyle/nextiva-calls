"""Environment-based application configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nextiva_calls.holiday_calendar import HolidayCalendarError, load_holiday_overrides


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
    candidate_calls_file: Path | None = None
    agent_lookup_file: Path | None = None
    # Kept as an attribute name for integrations from the earlier release; it
    # now points at date,name,status holiday overrides.
    closure_dates_file: Path = Path("closure_dates.csv")
    holiday_cache_file: Path = Path("opm_holidays.ics")
    holiday_status_file: Path | None = None
    opm_calendar_url: str = "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/holidays.ics"
    membership_hunt_group: str = "Membership"
    certification_hunt_group: str = "Certification Hunt Group"
    membership_simultaneous_from: str = "2026-08-15T00:00:00-05:00"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        """Build configuration from environment variables.

        Only variable names are included in errors; secret values are never shown.
        """
        values = os.environ if environ is None else environ
        required = (
            "EMAIL_USERNAME",
            "EMAIL_APP_PASSWORD",
            "NEXTIVA_EMAIL_SUBJECT",
            "NEXTIVA_AGENT_LOOKUP_FILE",
        )
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
        candidate_value = values.get("NEXTIVA_CANDIDATE_CALLS_FILE", "").strip()
        candidate_calls = (
            Path(candidate_value)
            if candidate_value
            else output.with_suffix(".candidate-calls.csv")
        )
        membership_hunt_group = values.get(
            "NEXTIVA_MEMBERSHIP_HUNT_GROUP", "Membership"
        ).strip()
        if not membership_hunt_group:
            raise ConfigError("NEXTIVA_MEMBERSHIP_HUNT_GROUP must not be blank")
        certification_hunt_group = values.get(
            "NEXTIVA_CERTIFICATION_HUNT_GROUP", "Certification Hunt Group"
        ).strip()
        if not certification_hunt_group:
            raise ConfigError("NEXTIVA_CERTIFICATION_HUNT_GROUP must not be blank")
        membership_simultaneous_from = values.get(
            "NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM", "2026-08-15T00:00:00-05:00"
        ).strip()
        try:
            routing_start = datetime.fromisoformat(membership_simultaneous_from)
        except ValueError as error:
            raise ConfigError(
                "NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM must be an ISO-8601 timestamp"
            ) from error
        if routing_start.tzinfo is None:
            raise ConfigError(
                "NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM must include a timezone offset"
            )
        lookup = Path(values["NEXTIVA_AGENT_LOOKUP_FILE"].strip())
        if lookup.name == "":
            raise ConfigError("NEXTIVA_AGENT_LOOKUP_FILE must name a file")
        closure_value = values.get(
            "NEXTIVA_HOLIDAY_OVERRIDES_FILE",
            values.get("NEXTIVA_CLOSURE_DATES_FILE", "closure_dates.csv"),
        ).strip()
        closure_dates = Path(closure_value)
        if closure_dates.name == "":
            raise ConfigError("NEXTIVA_HOLIDAY_OVERRIDES_FILE must name a file")
        try:
            load_holiday_overrides(closure_dates)
        except HolidayCalendarError as error:
            raise ConfigError(
                f"NEXTIVA_HOLIDAY_OVERRIDES_FILE (NEXTIVA_CLOSURE_DATES_FILE legacy alias) is invalid: {error}"
            ) from error
        cache_value = values.get("NEXTIVA_HOLIDAY_CACHE_FILE", "").strip()
        holiday_cache = Path(cache_value) if cache_value else output.with_suffix(".opm-holidays.ics")
        if holiday_cache.name == "":
            raise ConfigError("NEXTIVA_HOLIDAY_CACHE_FILE must name a file")
        status_value = values.get("NEXTIVA_HOLIDAY_STATUS_FILE", "").strip()
        holiday_status = (
            Path(status_value)
            if status_value
            else output.with_suffix(".holiday-refresh.json")
        )
        if holiday_status.name == "":
            raise ConfigError("NEXTIVA_HOLIDAY_STATUS_FILE must name a file")
        opm_calendar_url = values.get(
            "NEXTIVA_OPM_CALENDAR_URL",
            "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/holidays.ics",
        ).strip()
        if not opm_calendar_url.startswith("https://"):
            raise ConfigError("NEXTIVA_OPM_CALENDAR_URL must be an HTTPS URL")
        named_paths = {
            "NEXTIVA_OUTPUT_FILE": output,
            "NEXTIVA_STATE_FILE": state,
            "NEXTIVA_METADATA_FILE": metadata,
            "NEXTIVA_ANALYSIS_FILE": analysis,
            "NEXTIVA_CANDIDATE_CALLS_FILE": candidate_calls,
            "NEXTIVA_AGENT_LOOKUP_FILE": lookup,
            "NEXTIVA_CLOSURE_DATES_FILE": closure_dates,
            "NEXTIVA_HOLIDAY_CACHE_FILE": holiday_cache,
            "NEXTIVA_HOLIDAY_STATUS_FILE": holiday_status,
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
            candidate_calls_file=candidate_calls,
            agent_lookup_file=lookup,
            closure_dates_file=closure_dates,
            holiday_cache_file=holiday_cache,
            holiday_status_file=holiday_status,
            opm_calendar_url=opm_calendar_url,
            membership_hunt_group=membership_hunt_group,
            certification_hunt_group=certification_hunt_group,
            membership_simultaneous_from=membership_simultaneous_from,
        )
