"""PDF presentation for weekly manager metrics."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from nextiva_calls.weekly_metrics import OTHER, OUTCOMES, WeeklySummary


def _seconds(value: float) -> str:
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes}m {seconds:02d}s"


def _table(rows: list[list[object]], widths: list[float] | None = None) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183A5A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C4CE")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#EEF3F6")],
                ),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _heading(story: list[object], styles: object, text: str) -> None:
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(text, styles["Heading2"]))
    story.append(Spacer(1, 0.06 * inch))


def render_weekly_report(
    current: WeeklySummary, prior: WeeklySummary, output: Path
) -> None:
    """Write a management-shareable PDF comparing two weekly summaries."""
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output),
        pagesize=letter,
        pageCompression=0,
        rightMargin=42,
        leftMargin=42,
        topMargin=42,
        bottomMargin=42,
    )
    styles = getSampleStyleSheet()
    story: list[object] = []
    period = f"{current.week.start:%b %-d, %Y} – {current.week.end:%b %-d, %Y}"
    prior_period = f"{prior.week.start:%b %-d} – {prior.week.end:%b %-d, %Y}"
    story.append(Paragraph("Weekly Inbound Call Metrics", styles["Title"]))
    story.append(
        Paragraph(
            f"Current week: {period}<br/>Prior week: {prior_period}", styles["Normal"]
        )
    )
    if current.preliminary or prior.preliminary:
        labels = " and ".join(
            label
            for label, summary in (("current week", current), ("prior week", prior))
            if summary.preliminary
        )
        story.append(Spacer(1, 0.08 * inch))
        story.append(
            Paragraph(
                f"<b>PRELIMINARY:</b> metadata report-period coverage is incomplete for the {labels}.",
                styles["Normal"],
            )
        )
    _heading(story, styles, "Executive comparison")
    story.append(
        _table(
            [
                ["Measure", "Current", "Prior", "Change"],
                ["Calls", current.calls, prior.calls, current.calls - prior.calls],
                [
                    "Human answered",
                    current.outcomes["Human answered"],
                    prior.outcomes["Human answered"],
                    current.outcomes["Human answered"]
                    - prior.outcomes["Human answered"],
                ],
                [
                    "Routing attempts",
                    current.routing_attempts,
                    prior.routing_attempts,
                    current.routing_attempts - prior.routing_attempts,
                ],
                [
                    "Average duration",
                    _seconds(current.durations.average_seconds),
                    _seconds(prior.durations.average_seconds),
                    "—",
                ],
                [
                    "Median duration",
                    _seconds(current.durations.median_seconds),
                    _seconds(prior.durations.median_seconds),
                    "—",
                ],
                [
                    "Maximum duration",
                    _seconds(current.durations.maximum_seconds),
                    _seconds(prior.durations.maximum_seconds),
                    "—",
                ],
                [
                    "Answered-call talk time",
                    _seconds(current.answered_talk_seconds),
                    _seconds(prior.answered_talk_seconds),
                    "—",
                ],
                [
                    "Repeat callers",
                    len(current.repeat_callers),
                    len(prior.repeat_callers),
                    len(current.repeat_callers) - len(prior.repeat_callers),
                ],
            ],
            [2.5 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch],
        )
    )
    _heading(story, styles, "Organization outcomes and time categories")
    story.append(
        _table(
            [["Outcome", "Calls", "Rate"]]
            + [
                [
                    outcome,
                    current.outcomes[outcome],
                    f"{current.outcomes[outcome] / current.calls:.1%}"
                    if current.calls
                    else "—",
                ]
                for outcome in OUTCOMES
            ],
            [2.6 * inch, 1.2 * inch, 1.2 * inch],
        )
    )
    story.append(Spacer(1, 0.12 * inch))
    story.append(
        _table(
            [["Time category", "Calls"]]
            + [[key, value] for key, value in current.time_categories.items()],
            [2.6 * inch, 1.2 * inch],
        )
    )
    story.append(PageBreak())
    story.append(Paragraph("Weekly Inbound Call Metrics — Detail", styles["Title"]))
    _heading(story, styles, "Hunt groups")
    group_rows = [
        ["Hunt group", "Calls", "Human answered", "Sequential", "Simultaneous"]
    ]
    for name, values in current.hunt_groups.items():
        group_rows.append(
            [
                name,
                values.get("calls", 0),
                values.get("Human answered", 0),
                values.get("Sequential", 0),
                values.get("Simultaneous", 0),
            ]
        )
    story.append(
        _table(group_rows, [1.8 * inch, 0.7 * inch, 1.2 * inch, 1 * inch, 1 * inch])
    )
    _heading(story, styles, "Agent reconciliation")
    story.append(
        Paragraph(
            f"Every offered destination is counted as an offer. A named answer is credited only when exactly one known agent is the single possible answer destination; untracked or non-unique answers are assigned to <b>{OTHER}</b>.",
            styles["Normal"],
        )
    )
    agent_rows = [["Agent", "Offers", "Answers", "Talk time"]]
    for name, values in current.agents.items():
        agent_rows.append(
            [
                name,
                values.get("offers", 0),
                values.get("answers", 0),
                _seconds(values.get("talk_seconds", 0)),
            ]
        )
    story.append(_table(agent_rows, [2.4 * inch, 0.8 * inch, 0.8 * inch, 1.2 * inch]))
    _heading(story, styles, "Weekday, hour, and repeat callers")
    story.append(
        _table(
            [["Day", "Calls"]]
            + [[key, value] for key, value in current.weekday_volume.items()],
            [1.2 * inch, 0.8 * inch],
        )
    )
    story.append(Spacer(1, 0.12 * inch))
    active_hours = [
        [f"{hour:02d}:00", count]
        for hour, count in current.hour_volume.items()
        if count
    ]
    story.append(
        _table([["Central hour", "Calls"]] + active_hours, [1.2 * inch, 0.8 * inch])
    )
    if current.repeat_callers:
        story.append(Spacer(1, 0.12 * inch))
        story.append(
            _table(
                [["Repeat caller", "Calls"]]
                + [[caller, count] for caller, count in current.repeat_callers.items()],
                [2.4 * inch, 0.8 * inch],
            )
        )
    document.build(story)
