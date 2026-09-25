"""Printable, privacy-conscious PDF dashboard for weekly manager metrics."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from nextiva_calls.segments import CENTRAL_TIME
from nextiva_calls.weekly_metrics import (
    CONNECTED_CALL_ATTRIBUTION_CATEGORIES,
    OUTCOME_BUCKETS,
    WeeklyInsight,
    WeeklySummary,
    build_weekly_insights,
)

NAVY, PALE, RED = colors.HexColor("#123B5D"), colors.HexColor("#EAF2F7"), colors.HexColor("#A61B1B")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Keep the chart's coordinate system identical to the table directly below it.
# That makes every bar sit over the number for the same day.
OUTCOME_COLUMN_WIDTH = 1.13 * inch
DAILY_OUTCOME_COLUMN_WIDTH = .42 * inch
DAILY_OUTCOME_DAY_COUNT = 14


def _seconds(value: float) -> str:
    sign = "-" if value < 0 else ""
    minutes, seconds = divmod(round(abs(value)), 60)
    return f"{sign}{minutes}m {seconds:02d}s"


def _delta(value: int) -> str:
    return f"{value:+d}" if value else "0"


def _percentage(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.0%}" if denominator else "—"


def _percentage_delta(current: int, current_total: int, prior: int, prior_total: int) -> str:
    if not current_total or not prior_total:
        return "—"
    points = round(100 * (current / current_total - prior / prior_total))
    return f"{points:+d} pp" if points else "0 pp"


def _table(rows: list[list[object]], widths: list[float] | None = None, *, small: bool = False) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.5 if small else 8.5),
        ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#AAB8C2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 if small else 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 if small else 3.5),
    ]))
    return table


class StackedBarChart(Flowable):
    """Compact stacked daily outcome chart for a prior/current department pair."""

    COLORS = {
        "Yes": colors.HexColor("#2176AE"),
        "No": colors.HexColor("#A61B1B"),
        "Voicemail": colors.HexColor("#7A5C99"),
        "Unknown / Ambiguous": colors.HexColor("#6C757D"),
    }

    def __init__(
        self,
        summary: WeeklySummary,
        department: str = "Membership",
        height: float = 1.1 * inch,
        prior: WeeklySummary | None = None,
    ):
        super().__init__()
        self.summary, self.prior, self.department = summary, prior, department
        self.width = OUTCOME_COLUMN_WIDTH + DAILY_OUTCOME_COLUMN_WIDTH * DAILY_OUTCOME_DAY_COUNT
        self.height = height
        # The table below is centered by ReportLab, so center the chart too.
        # Its left Outcome-column space then starts at exactly the same x-position.
        self.hAlign = "CENTER"
        summaries = (prior, summary) if prior is not None else (summary,)
        self.entries = tuple(
            (item, item.week.start + timedelta(days=offset))
            for item in summaries
            for offset in range(7)
        )
        self.days = tuple(day.strftime("%a") for _, day in self.entries)
        self.day_labels = tuple(self._day_label(day) for _, day in self.entries)
        self.buckets = list(OUTCOME_BUCKETS)

    def _day_label(self, day) -> str:
        holiday = self.summary.holidays
        matching = next((item for item in holiday if item.day == day), None)
        if matching is None:
            return day.strftime("%a")
        return f"{day:%a} — {matching.name} (closed)"

    def draw(self) -> None:
        canvas = self.canv
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(0, self.height - 9, f"Daily {self.department} call outcomes (prior week | current week)")
        legend_x = 150
        canvas.setFont("Helvetica", 6.5)
        for bucket in self.buckets:
            canvas.setFillColor(self.COLORS[bucket])
            canvas.rect(legend_x, self.height - 24, 6, 6, fill=1, stroke=0)
            canvas.setFillColor(colors.black)
            canvas.drawString(legend_x + 8, self.height - 23, bucket)
            legend_x += 22 + canvas.stringWidth(bucket, "Helvetica", 6.5)
        totals = [sum(item.weekday_outcomes[(day.strftime("%a"), bucket)] for bucket in self.buckets) for item, day in self.entries]
        maximum, chart_height = max(totals, default=0) or 1, self.height - 47
        left, bottom = OUTCOME_COLUMN_WIDTH, 13
        step = DAILY_OUTCOME_COLUMN_WIDTH
        bar_width = step * .62
        canvas.setStrokeColor(colors.HexColor("#9BAAB5"))
        canvas.line(left, bottom, self.width, bottom)
        for index, ((item, calendar_day), day, label, total) in enumerate(zip(self.entries, self.days, self.day_labels, totals, strict=True)):
            x, stacked = left + index * step + (step - bar_width) / 2, 0
            for bucket in self.buckets:
                value = item.weekday_outcomes[(day, bucket)]
                height = chart_height * value / maximum
                if value:
                    canvas.setFillColor(self.COLORS[bucket])
                    canvas.rect(x, bottom + stacked, bar_width, height, fill=1, stroke=0)
                    stacked += height
            canvas.setFillColor(colors.black)
            canvas.setFont("Helvetica", 5.2)
            canvas.drawCentredString(x + bar_width / 2, 2, f"{day[0]}:{calendar_day.month}/{calendar_day.day}")
            if label != day:
                # Retain the full date-aware label in the PDF text layer even
                # where a compact chart needs its weekday abbreviation below.
                canvas.drawCentredString(x + bar_width / 2, 8, label)
            if total:
                canvas.drawCentredString(x + bar_width / 2, bottom + stacked + 2, str(total))
        if self.prior is not None:
            boundary = left + 7 * step
            canvas.setStrokeColor(NAVY)
            canvas.setLineWidth(.8)
            canvas.line(boundary, bottom - 1, boundary, bottom + chart_height + 1)


def _heading(story: list[object], styles: object, text: str) -> None:
    story.extend([Spacer(1, .09 * inch), Paragraph(text, styles["Heading2"]), Spacer(1, .035 * inch)])


def _department_contribution_line(
    membership: WeeklySummary,
    certification: WeeklySummary | None,
    combined: WeeklySummary,
) -> str:
    """Return the compact department total shown below the headline heading."""
    parts = [f"{membership.calls} Membership"]
    if certification is not None:
        parts.append(f"{certification.calls} Certification")
    department_total = membership.calls + (certification.calls if certification is not None else 0)
    detail = " + ".join(parts)
    if department_total == combined.calls:
        return f"{detail} = {combined.calls} total calls this week"
    return f"{detail}; {combined.calls} combined total calls this week (some calls overlap)"


def _metadata_text(summary: WeeklySummary) -> str:
    if summary.data_through is None:
        return "Data through: no continuous metadata-confirmed coverage"
    inclusive = summary.data_through - timedelta(minutes=1)
    qualifier = " (inferred from call dates)" if summary.coverage_inferred else ""
    return f"Data through{qualifier}: {inclusive:%b %-d, %Y %-I:%M %p} CT"


def _closure_names(summary: WeeklySummary) -> str:
    return "; ".join(f"{item.day:%a, %b %-d}: {item.name} (closed)" for item in summary.holidays)


def _offer_answer_rate(values: dict[str, int]) -> str:
    offers = values.get("recorded_offers", 0)
    return f"{100 * values.get('answers', 0) / offers:.1f}%" if offers else "N/A"


def _agent_rows(current: WeeklySummary) -> list[list[object]]:
    rows: list[list[object]] = [[
        "Agent", "Recorded offers", "Answers", "Offer answer rate",
        "Forwarded away", "Unknown-status exclusions",
    ]]
    for name, values in sorted(current.agents.items()):
        rows.append([
            name,
            values.get("recorded_offers", 0),
            values.get("answers", 0),
            _offer_answer_rate(values),
            values.get("forwarded_away", 0),
            values.get("unknown_status_exclusions", 0),
        ])
    return rows


def _daily_outcome_table(prior: WeeklySummary, current: WeeklySummary) -> Table:
    """Return 14 daily buckets in exactly the same order as the chart."""
    summaries = (prior, current)
    dates = [item.week.start + timedelta(days=offset) for item in summaries for offset in range(7)]
    headers = ["Outcome"] + [f"{day.strftime('%a')[0]}:{day.month}/{day.day}" for day in dates]
    rows: list[list[object]] = [headers]
    for bucket in OUTCOME_BUCKETS:
        rows.append([bucket] + [
            summary.weekday_outcomes[(day.strftime("%a"), bucket)]
            for summary in summaries
            for day in (summary.week.start + timedelta(days=offset) for offset in range(7))
        ])
    rows.append(["Daily total"] + [
        sum(summary.weekday_outcomes[(day.strftime("%a"), bucket)] for bucket in OUTCOME_BUCKETS)
        for summary in summaries
        for day in (summary.week.start + timedelta(days=offset) for offset in range(7))
    ])
    return _table(
        rows,
        [OUTCOME_COLUMN_WIDTH] + [DAILY_OUTCOME_COLUMN_WIDTH] * DAILY_OUTCOME_DAY_COUNT,
        small=True,
    )


def _unique_connected_call_rows(summary: WeeklySummary) -> list[list[object]]:
    rows: list[list[object]] = [["Answer attribution", "Unique connected calls"]]
    rows.extend([[category, summary.unique_connected_call_attribution[category]] for category in CONNECTED_CALL_ATTRIBUTION_CATEGORIES])
    rows.append(["Total connected calls", summary.connected_calls])
    return rows


def _insight_paragraph(insight: WeeklyInsight, styles: object) -> Paragraph:
    """Render pre-calculated insight text without introducing new calculations."""
    return Paragraph(insight.text, styles["Normal"])


def render_weekly_report(
    current: WeeklySummary,
    prior: WeeklySummary,
    output: Path,
    generated_at: datetime | None = None,
    membership: WeeklySummary | None = None,
    certification: WeeklySummary | None = None,
    prior_membership: WeeklySummary | None = None,
    prior_certification: WeeklySummary | None = None,
) -> None:
    """Write a fixed four-page US-Letter manager dashboard without phone numbers."""
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_at = (generated_at or datetime.now(CENTRAL_TIME)).astimezone(CENTRAL_TIME)
    document = SimpleDocTemplate(str(output), pagesize=letter, pageCompression=0, rightMargin=38, leftMargin=38, topMargin=33, bottomMargin=33)
    styles = getSampleStyleSheet()
    styles["Title"].fontSize = 18
    styles["Heading2"].textColor = NAVY
    styles["Heading2"].fontSize = 11
    styles.add(ParagraphStyle(name="Tiny", parent=styles["Normal"], fontSize=7.5, leading=9))
    styles.add(ParagraphStyle(name="ReportMeta", parent=styles["Normal"], fontSize=8, leading=9.5))
    story: list[object] = []
    period, prior_period = f"{current.week.start:%b %-d, %Y} – {current.week.end:%b %-d, %Y}", f"{prior.week.start:%b %-d} – {prior.week.end:%b %-d, %Y}"

    # Optional summaries preserve this public function's former call shape for
    # integrations while the CLI supplies the department-specific views.
    membership = membership or current
    story.append(Paragraph("Weekly Membership + Certification Call Dashboard", styles["Title"]))
    story.append(Paragraph(f"Reporting period: <b>{period}</b> (Central Time)<br/>Prior period: {prior_period}<br/>{_metadata_text(current)}<br/>Generated: {generated_at:%b %-d, %Y %-I:%M %p %Z}", styles["ReportMeta"]))
    if current.preliminary or prior.preliminary:
        story.extend([Spacer(1, .04 * inch), Paragraph("<font color='#A61B1B'><b>PRELIMINARY</b></font> — metadata coverage is incomplete for one or both comparison weeks. Use directional results with care.", styles["ReportMeta"])])
    comparison_closures = [
        text
        for text in (_closure_names(current), _closure_names(prior))
        if text
    ]
    caution = "No closures are listed in either comparison week."
    if comparison_closures:
        caution = "Closures can affect week-over-week volume: " + " | ".join(comparison_closures)
    story.extend([Spacer(1, .04 * inch), Paragraph(f"<b>Calendar caution:</b> {caution}", styles["Tiny"])])
    if current.calendar_coverage_warning or prior.calendar_coverage_warning:
        story.append(Paragraph("<b>Calendar coverage warning:</b> the available holiday calendar does not extend beyond one or both comparison periods.", styles["Tiny"]))
    if current.holidays:
        story.append(Paragraph(f"<b>Current-week closure note:</b> {_closure_names(current)}", styles["Tiny"]))
    _heading(
        story,
        styles,
        "Combined Membership + Certification headline metrics"
        f"<br/><font name='Helvetica' size='8'>{_department_contribution_line(membership, certification, current)}</font>",
    )
    confirmed = "Confirmed human answered"
    story.append(_table([["Measure", "Current", "Prior", "Change"], ["All scoped inbound calls", current.eligible_inbound_calls, prior.eligible_inbound_calls, _delta(current.eligible_inbound_calls-prior.eligible_inbound_calls)], [confirmed, current.confirmed_known_agent_answers, prior.confirmed_known_agent_answers, _delta(current.confirmed_known_agent_answers-prior.confirmed_known_agent_answers)], ["% confirmed human answered", _percentage(current.confirmed_known_agent_answers, current.eligible_inbound_calls), _percentage(prior.confirmed_known_agent_answers, prior.eligible_inbound_calls), _percentage_delta(current.confirmed_known_agent_answers, current.eligible_inbound_calls, prior.confirmed_known_agent_answers, prior.eligible_inbound_calls)], ["Attribution coverage", _percentage(current.attribution_coverage_numerator, current.attribution_coverage_denominator), _percentage(prior.attribution_coverage_numerator, prior.attribution_coverage_denominator), _percentage_delta(current.attribution_coverage_numerator, current.attribution_coverage_denominator, prior.attribution_coverage_numerator, prior.attribution_coverage_denominator)], ["Answered-call talk time", _seconds(current.answered_talk_seconds), _seconds(prior.answered_talk_seconds), _seconds(current.answered_talk_seconds-prior.answered_talk_seconds)]], [2.6*inch, 1.25*inch, 1.25*inch, 1.25*inch]))
    story.append(Paragraph(
        "<b>Attribution Coverage:</b> confirmed known-agent answers divided by connected calls. "
        "Connected calls include confirmed human answered, connected / unknown attribution, answered / unattributed, "
        "and ambiguous outcomes; forwarded / routing-only calls are excluded.",
        styles["Tiny"],
    ))
    _heading(story, styles, "Department-specific daily outcomes (prior week | current week)")
    prior_membership = prior_membership or prior
    story.extend([StackedBarChart(membership, "Membership", prior=prior_membership), _daily_outcome_table(prior_membership, membership)])
    if certification is not None:
        prior_certification = prior_certification or prior
        story.extend([Spacer(1, .04 * inch), StackedBarChart(certification, "Certification", prior=prior_certification), _daily_outcome_table(prior_certification, certification)])
    # The preceding 14-day section fills page one. Let ReportLab advance
    # naturally so an explicit break cannot introduce an otherwise blank page.

    coverage_table = _table(
        [["Time category", "Calls", "Share"]]
        + [[category, current.time_categories[category], f"{current.time_categories[category] / current.calls:.0%}" if current.calls else "—"] for category in ("Business hours", "After hours", "Weekend", "Holiday")],
        [2.5 * inch, 1 * inch, 1 * inch],
    )
    # Keep this short section together, so its title is never stranded at the
    # bottom of the preceding page without the table it introduces.
    story.append(KeepTogether([
        Paragraph("Combined coverage and routing detail", styles["Title"]),
        Spacer(1, .09 * inch),
        Paragraph("Business-hours coverage (combined Membership + Certification)", styles["Heading2"]),
        Spacer(1, .035 * inch),
        coverage_table,
    ]))
    _heading(story, styles, "Approved hunt groups (all calls; different scope)")
    story.append(Paragraph("This comparison includes only calls in Reception, Membership, Certification, and Bookstore. It does not include lookup-matched combined candidates routed through other hunt groups, so its total can differ from the combined headline.", styles["Tiny"]))
    story.append(Spacer(1, .035 * inch))
    groups = [["Hunt group", "Calls", "Confirmed", "Sequential", "Simultaneous"]] + [[name, values.get("calls", 0), values.get(confirmed, 0), values.get("Sequential", 0), values.get("Simultaneous", 0)] for name, values in current.hunt_groups.items()]
    story.append(_table(groups, [2.1*inch, .75*inch, 1*inch, 1.1*inch, 1.1*inch], small=True))
    _heading(story, styles, "Weekday / Central-hour call heatmap (combined Membership + Certification)")
    heat_hours, maximum = list(range(8, 19)), max((current.weekday_hour_volume[(day, hour)] for day in DAYS for hour in range(8, 19)), default=0) or 1
    heat_rows: list[list[object]] = [["Day"] + [f"{hour:02d}:00" for hour in heat_hours]]
    heat_colors = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), .25, colors.white), ("FONTSIZE", (0, 0), (-1, -1), 7), ("ALIGN", (0, 0), (-1, -1), "CENTER")]
    for row_index, day in enumerate(DAYS, start=1):
        heat_rows.append([day] + [current.weekday_hour_volume[(day, hour)] for hour in heat_hours])
        for col, hour in enumerate(heat_hours, start=1):
            shade = 1 - (.82 * current.weekday_hour_volume[(day, hour)] / maximum)
            heat_colors.append(("BACKGROUND", (col, row_index), (col, row_index), colors.Color(shade, .95, 1)))
    heat = Table(heat_rows, colWidths=[.48 * inch] + [.43 * inch] * len(heat_hours))
    heat.setStyle(TableStyle(heat_colors))
    story.append(heat)
    _heading(story, styles, "Routing-attempt distribution")
    story.append(_table([["Offered destinations", "Calls"]] + [[str(count), value] for count, value in current.routing_attempt_distribution.items()], [2.3*inch, 1*inch]))
    story.append(PageBreak())

    story.append(Paragraph("Manager-only Membership + Certification agent attribution", styles["Title"]))
    story.append(Paragraph("Only lookup-listed Membership and Certification agents are shown. This is the percentage of recorded offers answered, not a performance score or miss rate. Simultaneous routing can record one call as an offer to multiple agents.", styles["Normal"]))
    agent_rows = _agent_rows(current)
    _heading(story, styles, "Recorded-offer answer rate by agent")
    story.append(_table(agent_rows, [1.35*inch, .9*inch, .55*inch, .95*inch, .8*inch, 1.2*inch], small=True))
    _heading(story, styles, "Unique connected calls by answer attribution")
    story.append(Paragraph("Each connected call appears exactly once in this reconciliation; it is not an offer or a call-agent pair.", styles["Tiny"]))
    story.append(_table(_unique_connected_call_rows(current), [2.8 * inch, 1.4 * inch], small=True))
    _heading(story, styles, "Definitions and data-quality notes")
    story.append(Paragraph("Combined metrics, business-hours coverage, and the heatmap use the union of calls to the configured Membership hunt group, the Certification hunt group, or an offered Membership or Certification agent. A cross-department call counts once in combined totals, but appears in each matching department's 14-day outcome detail. The hunt-group comparison is a different scope: all in-period candidates for Reception, Membership, Certification, and Bookstore. Confirmed human answered is the only Yes measure and requires reconstructed known-agent answer evidence. Attribution coverage is confirmed known-agent answers divided by connected calls; connected calls are confirmed answers plus connected / unknown attribution, answered / unattributed, and ambiguous outcomes. Forwarded / routing only is not connected. The unique connected-call reconciliation assigns every connected call to exactly one named agent, Multiple named agents, or Other; it is not an offer measure. A recorded offer is one duplicate-free call-agent pair with normalized <b>Yes</b> or <b>No</b>; <b>Yes</b> also counts as an answer. <b>Yes - Forwarded</b> appears only as forwarded away. Unfamiliar statuses appear only as per-agent data-quality exclusions and never enter the rate. A zero-offer rate is N/A.", styles["Normal"]))
    story.extend([Spacer(1, .06*inch), _table([["Anomaly", "Count"]] + [[name, current.anomalies[name]] for name in ("Ambiguous outcomes", "Unknown outcomes", "Unattributed answers")], [2.6*inch, .9*inch]), Spacer(1, .1*inch)])
    story.append(Paragraph("Duration is the available maximum-duration proxy. This report contains no customer or agent phone numbers, repeat-caller listings, outbound measures, speed-of-answer, wait-time, or agent-miss claims.", styles["Tiny"]))
    story.append(PageBreak())

    story.append(Paragraph("Automated insights", styles["Title"]))
    story.append(Paragraph(
        "These automated observations use only combined Membership + Certification call-level outcomes. "
        "They contain no customer or agent phone numbers and make no agent-miss, wait-time, speed-of-answer, or outbound claims.",
        styles["Normal"],
    ))
    _heading(story, styles, "Evidence-based weekly observations")
    for insight in build_weekly_insights(current, prior):
        story.append(_insight_paragraph(insight, styles))
        story.append(Spacer(1, .08 * inch))
    story.append(Paragraph(
        "Rules: only voicemail rate and confirmed-human-answer rate are compared. A change is significant only "
        "when both weeks have at least 20 scoped calls and the rate changes by at least 10 percentage points. "
        "When either week is PRELIMINARY, trend observations are suppressed and only coverage information is shown.",
        styles["Tiny"],
    ))
    document.build(story)
