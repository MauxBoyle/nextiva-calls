from dataclasses import dataclass

import pytest
from selenium.webdriver.common.by import By

from nextiva_calls.records import CSV_COLUMNS
from nextiva_calls.report import (
    ReportError,
    load_report,
    normalized_header,
    parse_rendered_report_rows,
    parse_report_period,
    parse_report_rows,
)


def test_parse_report_selects_columns_by_normalized_header():
    headers = [
        " To ",
        "NAME",
        "Answered",
        "Duration",
        "From",
        "Direction",
        "Time   of Call",
    ]
    rows = [
        [
            "+1 555 010 0200",
            "Alex Example",
            "Yes",
            "1m 2s",
            "(555) 010-0100",
            "Inbound",
            "January 2, 2026 9:30 AM",
        ]
    ]
    result = parse_report_rows(headers, rows)
    assert result[0].name == "Alex Example"
    assert result[0].duration == 62
    assert result[0].to_number == "+1 555 010 0200"
    assert normalized_header(" Time\n OF   Call ") == "time of call"


@pytest.mark.parametrize(
    ("headers", "rows"),
    [
        (["Name"], [["Alex"]]),
        (list(CSV_COLUMNS) + ["Name"], []),
        (list(CSV_COLUMNS), [["short"]]),
    ],
)
def test_parse_report_rejects_incomplete_or_malformed_tables(headers, rows):
    with pytest.raises(ReportError):
        parse_report_rows(headers, rows)


def test_parse_report_keeps_malformed_timestamp_for_analysis_cleaning():
    records = parse_report_rows(
        CSV_COLUMNS, [["Alex", "bad time", "1s", "In", "Yes", "1", "2"]]
    )
    assert records[0].time_of_call == "bad time"


@dataclass
class Element:
    text: str = ""
    headings: list | None = None
    rows: list | None = None
    cells: list | None = None

    def find_elements(self, by, value):
        if by == By.TAG_NAME and value == "th":
            return self.headings or []
        if by == By.CSS_SELECTOR and value == "tbody tr":
            return self.rows or []
        if by == By.TAG_NAME and value == "td":
            return self.cells or []
        return []


class Driver:
    def __init__(self, tables, nextiva_body_tables=None, page_text=""):
        self.tables = tables
        self.nextiva_body_tables = nextiva_body_tables or []
        self.page_text = page_text
        self.visited = None
        self.quit_called = False

    def get(self, url):
        self.visited = url

    def find_elements(self, by, value):
        if (by, value) == (By.TAG_NAME, "table"):
            return self.tables
        if (by, value) == (By.CSS_SELECTOR, "table.nx-table_body"):
            return self.nextiva_body_tables
        return []

    def find_element(self, by, value):
        if (by, value) == (By.TAG_NAME, "body"):
            return Element(text=self.page_text)
        raise LookupError

    def quit(self):
        self.quit_called = True


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, condition):
        result = condition(self.driver)
        if not result:
            raise TimeoutError
        return result


def valid_table():
    headings = [Element(text=value) for value in CSV_COLUMNS]
    cells = [
        Element(text=value)
        for value in [
            "Alex",
            "Jan 2 2026 9:30 AM",
            "2s",
            "Inbound",
            "Yes",
            "555-0100",
            "555-0200",
        ]
    ]
    return Element(headings=headings, rows=[Element(cells=cells)])


def test_load_report_waits_for_matching_table_and_always_quits():
    wrong = Element(headings=[Element(text="Wrong")])
    driver = Driver([wrong, valid_table()])
    result = load_report(
        "https://ct.nextiva.com/inactive",
        7,
        browser_factory=lambda: driver,
        wait_factory=ImmediateWait,
    )
    assert result[0].duration == 2
    assert driver.visited.endswith("/inactive")
    assert driver.quit_called is True


def test_load_report_infers_period_when_nextiva_omits_its_label():
    driver = Driver([valid_table()])

    result = load_report(
        "https://ct.nextiva.com/inactive", 7,
        browser_factory=lambda: driver, wait_factory=ImmediateWait,
    )

    assert result.period_start.date().isoformat() == "2026-01-02"
    assert result.period_end.date().isoformat() == "2026-01-02"
    assert result.warnings == ("Report period inferred from earliest and latest call dates",)


def test_load_report_supports_nextiva_body_table_without_header_cells():
    cells = [
        Element(text=value)
        for value in [
            "Membership",
            "09/03/26 07:56:24 AM",
            "18s",
            "Terminating",
            "Yes - Forwarded",
            "+12818976523",
            "+13126702401",
        ]
    ]
    body_table = Element(headings=[], rows=[Element(cells=cells)])
    driver = Driver([], nextiva_body_tables=[body_table])

    result = load_report(
        "https://ct.nextiva.com/inactive",
        7,
        browser_factory=lambda: driver,
        wait_factory=ImmediateWait,
    )

    assert result[0].name == "Membership"
    assert result[0].answered == "Yes - Forwarded"
    assert driver.quit_called is True


def test_load_report_falls_back_to_validated_rendered_nextiva_text():
    body_table = Element(headings=[], rows=[Element(cells=[Element(text="extra")] * 8)])
    page_text = """Missed Call Daily Report
Name
Time of Call
Duration
Direction
Answered
From
To
Actions
Membership
09/03/26 07:56:24 AM
18s
Terminating
Yes - Forwarded
+12818976523
+13126702401"""
    driver = Driver([], nextiva_body_tables=[body_table], page_text=page_text)

    result = load_report(
        "https://ct.nextiva.com/inactive",
        7,
        browser_factory=lambda: driver,
        wait_factory=ImmediateWait,
    )

    assert result[0].name == "Membership"
    assert result[0].duration == 18


def test_load_report_waits_for_nextiva_rendered_text_without_a_table():
    page_text = """Missed Call Daily Report
Name
Time of Call
Duration
Direction
Answered
From
To
Actions
Membership
09/03/26 07:56:24 AM
18s
Terminating
Yes - Forwarded
+12818976523
+13126702401"""
    driver = Driver([], page_text=page_text)

    result = load_report(
        "https://ct.nextiva.com/inactive",
        7,
        browser_factory=lambda: driver,
        wait_factory=ImmediateWait,
    )

    assert result[0].to_number == "+13126702401"
    assert driver.quit_called is True


def test_rendered_report_text_requires_a_name_header():
    with pytest.raises(ReportError, match="Name header"):
        parse_rendered_report_rows("Missed Call Daily Report")


def test_load_report_wraps_timeout_without_leaking_url_and_quits():
    driver = Driver([])
    with pytest.raises(ReportError) as caught:
        load_report(
            "https://ct.nextiva.com/report?secret=DO_NOT_LOG",
            1,
            browser_factory=lambda: driver,
            wait_factory=ImmediateWait,
        )
    assert "DO_NOT_LOG" not in str(caught.value)
    assert driver.quit_called is True


def test_load_report_wraps_browser_startup_failure():
    def fail_to_start():
        raise RuntimeError("token=DO_NOT_LOG")

    with pytest.raises(ReportError) as caught:
        load_report(
            "https://ct.nextiva.com/inactive",
            1,
            browser_factory=fail_to_start,
            wait_factory=ImmediateWait,
        )
    assert "DO_NOT_LOG" not in str(caught.value)


@pytest.mark.parametrize("label", ["Date Range", "Report Period"])
def test_parse_labelled_period_uses_central_time(label):
    start, end, warnings = parse_report_period(
        f"Summary\n{label}: Jan 2, 2026 - Jan 3, 2026\nCalls"
    )
    assert start.isoformat() == "2026-01-02T00:00:00-06:00"
    assert end.isoformat() == "2026-01-03T00:00:00-06:00"
    assert warnings == ()


def test_parse_period_allows_a_value_on_the_next_rendered_line():
    start, end, warnings = parse_report_period(
        "Date Range\nJan 2, 2026 through Jan 3, 2026"
    )
    assert start.date().isoformat() == "2026-01-02"
    assert end.date().isoformat() == "2026-01-03"
    assert warnings == ()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Summary", "not found"),
        ("Date Range: someday", "malformed"),
        ("Report Period: Jan 3, 2026 - Jan 2, 2026", "ends before"),
    ],
)
def test_bad_periods_are_warnings_not_report_errors(text, expected):
    _, _, warnings = parse_report_period(text)
    assert expected in warnings[0]
