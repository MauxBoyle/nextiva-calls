# Usage

## Setup

Install Python dependencies and make sure Google Chrome is installed:

```bash
uv sync
cp .env.example .env
```

Create a Google app password after enabling two-step verification. Add it to the
local `.env`; do not use a normal Google password and do not commit this file.

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `EMAIL_USERNAME` | Yes | — | Gmail address |
| `EMAIL_APP_PASSWORD` | Yes | — | Google app password |
| `NEXTIVA_EMAIL_SUBJECT` | Yes | — | Exact decoded subject |
| `NEXTIVA_AGENT_LOOKUP_FILE` | Yes | — | Role-aware destination lookup CSV |
| `EMAIL_IMAP_SERVER` | No | `imap.gmail.com` | IMAP hostname |
| `NEXTIVA_EMAIL_SENDER` | No | `analytics@nextiva.com` | Exact sender address |
| `NEXTIVA_ALLOWED_HOSTS` | No | `ct.nextiva.com` | Comma-separated exact HTTPS hosts |
| `NEXTIVA_REPORT_TIMEOUT_SECONDS` | No | `30` | Dynamic-table wait timeout |
| `NEXTIVA_OUTPUT_FILE` | No | `NextivaCallData.csv` | Destination CSV |
| `NEXTIVA_STATE_FILE` | No | derived beside CSV | Processed-message JSON |
| `NEXTIVA_METADATA_FILE` | No | derived beside CSV | SQLite report provenance |
| `NEXTIVA_ANALYSIS_FILE` | No | derived beside CSV | Duplicate-free analysis CSV |
| `NEXTIVA_CANDIDATE_CALLS_FILE` | No | derived beside CSV | Reconstructed candidate-call CSV |
| `NEXTIVA_MEMBERSHIP_HUNT_GROUP` | No | `Membership` | Hunt group with a routing-policy boundary |
| `NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM` | No | `2026-08-15T00:00:00-05:00` | Inclusive Central-time simultaneous-ring start |
| `LOG_LEVEL` | No | `INFO` | Console log level |
| `LOG_FILE` | No | `app.log` | Debug log location; blank disables it |

Run with the environment file:

```bash
uv run --env-file .env nextiva_calls
```

It can also run as a module:

```bash
uv run --env-file .env python -m nextiva_calls
```

## Weekly manager report

Create a Membership + Certification PDF comparing the seven complete Central-time days ending
yesterday with the preceding seven days:

```bash
uv run --env-file .env nextiva_calls weekly-report
uv run --env-file .env nextiva_calls weekly-report --week-start 2026-08-17
```

`--week-start` accepts any date in `YYYY-MM-DD` format as the beginning of a
seven-day historical period. The default output is the printable three-page Letter PDF
`reports/Nextiva_Weekly_<start>_to_<end>.pdf`; pass `--output PATH` to write
elsewhere.

Headline metrics, business-hours coverage, and the heatmap include the combined
union of calls to `NEXTIVA_MEMBERSHIP_HUNT_GROUP`, the literal `Certification`
hunt group, and calls that offer an exact lookup phone-number match or a uniquely
mapped final-four extension for a Membership or Certification agent. A call that
matches both departments counts once in these combined totals. Every scoped call
is in the headline denominator, including after-hours, weekend, holiday, and
voicemail-only calls. The hunt-group table is the exception: it compares
all in-period candidates limited to Reception, Membership, Certification, and
Bookstore.

The PDF includes period, data-through, and generation timestamps; separate
Membership and Certification daily outcome charts and reconciliation tables,
plus combined coverage, routing, hunt groups, and weekday/hour views. Outcome
detail is omitted when a department has no calls; a cross-department call appears
in both matching department outcome views. The chart's `Yes` bucket contains only `Confirmed human
answered`; forwarding or routing-only activity does not count as an answer. It
shows forwarded/routing-only, voicemail, connected/unknown attribution,
answered/unattributed, ambiguous, unknown, and unanswered outcomes separately.
The chart has `No` (unanswered) and `Voicemail` segments, adding `Unknown /
Ambiguous` only when needed for accurate totals. Its manager-only agent table shows only
Membership and Certification lookup agents; other departments, system routing,
and voicemail never create agent-level attribution. It shows every
lookup-listed agent, excluding hunt groups, voicemail, and `Other / Unattributed`. Attribution coverage is
`confirmed known-agent answers / connected calls`, where connected calls are
confirmed human answers plus `Connected / unknown attribution`, `Answered /
unattributed`, and `Ambiguous` outcomes; forwarded/routing-only calls are not
connected. A zero denominator is displayed as a dash. It never displays
customer or agent phone numbers or repeat callers.
It contains no outbound, speed-of-answer, wait-time, or “agent miss” metrics.
The table's offer answer rate is the percentage of recorded offers answered, not
a performance score or miss rate. A recorded offer is a duplicate-free
call-agent pair with exact normalized `Yes` or `No`; `Yes` also counts as an
answer. `Yes - Forwarded` is counted only as forwarded away. Unfamiliar agent
statuses are shown as data-quality exclusions and never enter the rate. A
zero-offer agent displays `N/A`. Simultaneous routing can offer one call to
multiple agents, so agent offers are not call counts.

The report is visibly **PRELIMINARY** when valid report periods in the metadata
database do not continuously cover every moment of either selected week. Missing,
invalid, or gapped metadata therefore keeps the label even if calls are present.
“Data through” shows the selected week’s latest continuous metadata-confirmed
coverage boundary in Central Time.

## Processing behavior

The importer searches without marking email as read, then checks sender and
subject again in Python. It processes IMAP UIDs oldest-first. HTML email is
preferred over plain text, attachments are ignored, and exactly one HTML anchor
visibly labeled `Missed Calls` (case-insensitive and whitespace-normalized) is
used as the report link. Other Nextiva navigation and footer links are ignored.
The selected link must use HTTPS and an exact allowlisted hostname. Plain-text
messages continue to extract report URLs without requiring that label.

Chrome waits for a table with the required normalized headers. Every row must
have seven cells and a duration such as `4s`, `2m 3s`, or `1h 2m 3s`. Malformed
call timestamps are retained and flagged in the analysis CSV rather than blocking
the complete report.

The CSV header is exactly:

```text
Name,Time of Call,Duration,Direction,Answered,From,To
```

`NextivaCallData.csv` is the append-only raw source. A separate analysis CSV is
rebuilt with an atomic replacement; it preserves first-seen order and omits exact
whitespace-normalized duplicate rows already present in raw data. Its header is
the seven raw columns plus `call_timestamp_ct`, `from_number_normalized`,
`to_number_normalized`, `destination_label`, `destination_type`, `is_voicemail_destination`,
`is_duplicate`, `is_anomaly`, `anomaly_reasons`, `is_business_hours`, and
`is_holiday`. Booleans are written as `True`/`False`.

The lookup CSV is required and must have exactly
`phone_number,display_name,department,destination_type` headers. Values are
nonblank, types are `agent` or `system`, and conflicting full-number mappings
are rejected. Analysis phone
numbers are digits-only and never shortened. Destinations match full lookup
numbers before uniquely mapped final-four extensions; otherwise their type is
`unknown`. `9999` is voicemail regardless of lookup label. Naive timestamps use
`America/Chicago`; offset-aware timestamps are converted to it. Business hours
are weekdays from 9:00 AM inclusive through 5:00 PM exclusive CT, excluding the
2026 closures Jan 1, Jan 19, Feb 16, May 25, Jun 19, Jul 3, Sep 7, Oct 12, Nov
11, Nov 26–27, and Dec 25. Invalid timestamps and other row anomalies are kept
and recorded in `anomaly_reasons`.

SQLite metadata records each report fingerprint, source message, Central-time
labelled period, import time, warnings, and every report-to-call-segment link. A
repeat report sent in a different email is associated with its original report but
does not add rows. Missing or malformed labelled periods, reversed periods, and
overlap with an earlier period log warnings while retaining valid calls. Back up
the metadata database along with the raw CSV; if it is corrupt, restore it before
retrying rather than deleting the audit trail. `NextivaCallData.metadata.sqlite3`
is tracked in Git. A successful import can update it, so review and commit an
intentional change with the related import work instead of deleting the file to
make the working tree clean.

## Candidate-call reconstruction

Whenever the analysis CSV is rebuilt, the importer atomically writes a candidate
CSV named `NextivaCallData.candidate-calls.csv` by default. It groups segments by
`Name`, Central timestamp, and normalized `From`. Any missing grouping value
makes that segment a standalone `Unknown` candidate, which guarantees one and
only one candidate contains each duplicate-free segment.

Candidate rows contain a hashed ID, segment fingerprints, routing mode, ordered
unique offered destinations and role-aware evidence columns:
`offered_agent_destinations`, `recorded_offer_agent_destinations`,
`confirmed_answered_agent_destinations`, `forwarded_destinations`,
`unknown_status_agent_destinations`, `system_routing_destinations`,
`reached_voicemail`, and `unknown_answered_destinations`. Voicemail is never an
agent answer. Outcomes
separate confirmed agent answers, system/unattributed connections, routing-only,
voicemail, unanswered, unknown, and ambiguous calls.

The default assumption is that `Membership` changed from sequential to
simultaneous ringing at midnight Central Time on August 15, 2026, inclusive.
Update `NEXTIVA_MEMBERSHIP_HUNT_GROUP` and
`NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM` together when that business rule changes;
the timestamp must be ISO-8601 and include its offset. Other hunt groups remain
sequential.

Use the [versioned 20-row manual-validation checklist](candidate-call-validation-checklist-v1.csv)
to compare candidate output with Nextiva evidence.

The candidate CSV is derived data. If `weekly-report` finds it has an older
header, it atomically rebuilds it from the analysis CSV before reporting.

Exit status `0` means all required reports succeeded. Exit status `1` means at
least one report failed or a fatal configuration, mailbox, CSV, or state error
occurred. Invalid individual messages are skipped so later messages can run.
