"""Load and parse dynamically rendered Nextiva report tables."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import ParserError
from dateutil.parser import parse as parse_datetime

from nextiva_calls.records import CSV_COLUMNS, CallRecord, RecordError, clean_text


class ReportError(RuntimeError):
    """Raised when a report cannot be loaded or fully validated."""


CENTRAL_TIME = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class ReportResult:
    """Validated report contents plus optional provenance information."""

    records: list[CallRecord]
    period_start: datetime | None = None
    period_end: datetime | None = None
    warnings: tuple[str, ...] = ()

    # Small compatibility conveniences for callers that previously received a
    # list directly from ``load_report``.
    def __getitem__(self, index: int) -> CallRecord:
        return self.records[index]

    def __len__(self) -> int:
        return len(self.records)


_PERIOD_LABEL = re.compile(r"(?:date\s*range|report\s*period)\s*[:\-]?\s*", re.I)
_PERIOD_SEPARATOR = re.compile(r"\s*(?:\bto\b|\bthrough\b|\s+-\s+|\s*[–—]\s*)\s*", re.I)


def parse_report_period(
    page_text: str,
) -> tuple[datetime | None, datetime | None, tuple[str, ...]]:
    """Read a labelled date range from page text, using Central time.

    A missing or unfamiliar label is intentionally non-fatal: call rows are still
    useful, and the warning documents why no period provenance was stored.
    """
    label = _PERIOD_LABEL.search(page_text)
    if label is None:
        return None, None, ("Report period was not found in a labelled header",)
    # A rendered page may place the label and value in separate elements, which
    # Selenium exposes as separate lines of text.
    value = page_text[label.end() :].strip().splitlines()[0].strip()
    parts = _PERIOD_SEPARATOR.split(value, maxsplit=1)
    if len(parts) != 2 or not all(parts):
        return None, None, ("Report period is malformed",)
    try:
        start = parse_datetime(parts[0], fuzzy=False).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=CENTRAL_TIME
        )
        end = parse_datetime(parts[1], fuzzy=False).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=CENTRAL_TIME
        )
    except (ParserError, OverflowError, ValueError):
        return None, None, ("Report period is malformed",)
    if end < start:
        return start, end, ("Report period ends before it starts",)
    return start, end, ()


def infer_report_period(records: Iterable[CallRecord]) -> tuple[datetime | None, datetime | None]:
    """Infer an inclusive Central-time date range from valid call timestamps."""
    dates = []
    for record in records:
        try:
            parsed = parse_datetime(record.time_of_call, fuzzy=False)
        except (ParserError, OverflowError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CENTRAL_TIME)
        else:
            parsed = parsed.astimezone(CENTRAL_TIME)
        dates.append(parsed.date())
    if not dates:
        return None, None
    return (
        datetime.combine(min(dates), datetime.min.time(), tzinfo=CENTRAL_TIME),
        datetime.combine(max(dates), datetime.min.time(), tzinfo=CENTRAL_TIME),
    )


def _with_inferred_period(
    records: list[CallRecord], start: datetime | None, end: datetime | None,
    warnings: tuple[str, ...],
) -> ReportResult:
    if start is None and end is None and warnings == ("Report period was not found in a labelled header",):
        start, end = infer_report_period(records)
        if start is not None:
            warnings = ("Report period inferred from earliest and latest call dates",)
    return ReportResult(records, start, end, warnings)


def normalized_header(value: str) -> str:
    """Normalize table headings for stable comparisons."""
    return clean_text(value).casefold()


def parse_report_rows(
    headers: Iterable[str], rows: Iterable[Iterable[str]]
) -> list[CallRecord]:
    """Map report columns by header name and validate every data row."""
    header_list = [normalized_header(header) for header in headers]
    required = [normalized_header(header) for header in CSV_COLUMNS]
    if len(header_list) != len(set(header_list)) or not all(
        name in header_list for name in required
    ):
        raise ReportError("Report table does not have the required unique columns")
    positions = [header_list.index(name) for name in required]
    records: list[CallRecord] = []
    for row_number, row in enumerate(rows, start=1):
        cells = list(row)
        if len(cells) != len(header_list):
            raise ReportError(f"Report row {row_number} has the wrong number of cells")
        try:
            records.append(
                CallRecord.from_cells([cells[position] for position in positions])
            )
        except RecordError as error:
            raise ReportError(f"Report row {row_number} is invalid: {error}") from error
    return records


def parse_rendered_report_rows(page_text: str) -> list[CallRecord]:
    """Parse Nextiva's rendered text when its table cells are not usable.

    The current Nextiva report displays eight header lines beginning with
    ``Name`` followed by seven values for each call. Rows are still passed
    through the same strict validation as DOM-extracted rows.
    """
    lines = [clean_text(line) for line in page_text.splitlines() if clean_text(line)]
    try:
        header_start = next(
            index for index, line in enumerate(lines) if line.casefold() == "name"
        )
    except StopIteration as error:
        raise ReportError("Rendered report does not contain the Name header") from error
    row_lines = lines[header_start + 8 :]
    rows = [
        row_lines[index : index + len(CSV_COLUMNS)]
        for index in range(0, len(row_lines) - len(CSV_COLUMNS) + 1, len(CSV_COLUMNS))
    ]
    return parse_report_rows(CSV_COLUMNS, rows)


def _has_rendered_name_header(page_text: str) -> bool:
    """Return whether rendered page text contains Nextiva's row header."""
    return any(clean_text(line).casefold() == "name" for line in page_text.splitlines())


def _default_browser() -> Any:
    from selenium import webdriver

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(options=options)


def load_report(
    url: str,
    timeout_seconds: float,
    *,
    browser_factory: Callable[[], Any] = _default_browser,
    wait_factory: Callable[[Any, float], Any] | None = None,
) -> ReportResult:
    """Load a dynamic report in headless Chrome and return validated contents."""
    if wait_factory is None:
        from selenium.webdriver.support.ui import WebDriverWait

        wait_factory = WebDriverWait

    driver = None
    try:
        driver = browser_factory()
        driver.get(url)
        rendered_text_ready = object()

        def find_matching_table(active_driver: Any) -> Any:
            from selenium.webdriver.common.by import By

            required = {normalized_header(header) for header in CSV_COLUMNS}
            for table in active_driver.find_elements(By.TAG_NAME, "table"):
                headings = table.find_elements(By.TAG_NAME, "th")
                if {normalized_header(item.text) for item in headings} == required:
                    return table
            # Nextiva's newer reports render the headers separately from their
            # ``nx-table_body`` table. Its body cells retain the established
            # CSV column order, so they can still be validated safely below.
            body_tables = active_driver.find_elements(
                By.CSS_SELECTOR, "table.nx-table_body"
            )
            if len(body_tables) == 1:
                return body_tables[0]
            # The report can expose its populated data only through rendered
            # text. This is the format used by Nextiva's older export flow.
            try:
                page_text = active_driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                return False
            if _has_rendered_name_header(page_text):
                return rendered_text_ready
            return False

        table = wait_factory(driver, timeout_seconds).until(find_matching_table)
        from selenium.webdriver.common.by import By

        # Read the rendered page only after a report table or its text header
        # has appeared.
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            page_text = ""
        start, end, warnings = parse_report_period(page_text)
        if table is rendered_text_ready:
            return _with_inferred_period(
                parse_rendered_report_rows(page_text), start, end, warnings
            )

        headers = [cell.text for cell in table.find_elements(By.TAG_NAME, "th")]
        raw_rows: list[list[str]] = []
        for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
            raw_rows.append(
                [cell.text for cell in row.find_elements(By.TAG_NAME, "td")]
            )
        # ``nx-table_body`` has no header cells; its column order is the same
        # seven-field order exported by the older report table.
        report_headers = headers or CSV_COLUMNS
        try:
            records = parse_report_rows(report_headers, raw_rows)
        except ReportError:
            records = parse_rendered_report_rows(page_text)
        return _with_inferred_period(records, start, end, warnings)
    except ReportError:
        raise
    except Exception as error:
        # Do not include Selenium's exception text because it can contain the URL.
        raise ReportError("The report could not be loaded in Chrome") from error
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
