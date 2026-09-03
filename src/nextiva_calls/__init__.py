"""Import call records from emailed Nextiva reports."""

from nextiva_calls.config import Config
from nextiva_calls.records import CallRecord

__all__ = ["CallRecord", "Config"]
