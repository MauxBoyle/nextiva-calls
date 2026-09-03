"""Load and parse dynamically rendered Nextiva report tables."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from nextiva_calls.records import CSV_COLUMNS, CallRecord, RecordError, clean_text


class ReportError(RuntimeError):
    """Raised when a report cannot be loaded or fully validated."""


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


def _default_browser() -> Any:
    from selenium import webdriver

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def load_report(
    url: str,
    timeout_seconds: float,
    *,
    browser_factory: Callable[[], Any] = _default_browser,
    wait_factory: Callable[[Any, float], Any] | None = None,
) -> list[CallRecord]:
    """Load a dynamic report in headless Chrome and return validated records."""
    if wait_factory is None:
        from selenium.webdriver.support.ui import WebDriverWait

        wait_factory = WebDriverWait

    driver = None
    try:
        driver = browser_factory()
        driver.get(url)

        def find_matching_table(active_driver: Any) -> Any:
            from selenium.webdriver.common.by import By

            required = {normalized_header(header) for header in CSV_COLUMNS}
            for table in active_driver.find_elements(By.TAG_NAME, "table"):
                headings = table.find_elements(By.TAG_NAME, "th")
                if {normalized_header(item.text) for item in headings} == required:
                    return table
            return False

        table = wait_factory(driver, timeout_seconds).until(find_matching_table)
        from selenium.webdriver.common.by import By

        headers = [cell.text for cell in table.find_elements(By.TAG_NAME, "th")]
        raw_rows: list[list[str]] = []
        for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
            raw_rows.append(
                [cell.text for cell in row.find_elements(By.TAG_NAME, "td")]
            )
        return parse_report_rows(headers, raw_rows)
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
