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
| `NEXTIVA_AGENT_LOOKUP_FILE` | Yes | — | `phone_number,agent` destination lookup CSV |
| `EMAIL_IMAP_SERVER` | No | `imap.gmail.com` | IMAP hostname |
| `NEXTIVA_EMAIL_SENDER` | No | `analytics@nextiva.com` | Exact sender address |
| `NEXTIVA_ALLOWED_HOSTS` | No | `ct.nextiva.com` | Comma-separated exact HTTPS hosts |
| `NEXTIVA_REPORT_TIMEOUT_SECONDS` | No | `30` | Dynamic-table wait timeout |
| `NEXTIVA_OUTPUT_FILE` | No | `NextivaCallData.csv` | Destination CSV |
| `NEXTIVA_STATE_FILE` | No | derived beside CSV | Processed-message JSON |
| `NEXTIVA_METADATA_FILE` | No | derived beside CSV | SQLite report provenance |
| `NEXTIVA_ANALYSIS_FILE` | No | derived beside CSV | Duplicate-free analysis CSV |
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

## Processing behavior

The importer searches without marking email as read, then checks sender and
subject again in Python. It processes IMAP UIDs oldest-first. HTML email is
preferred over plain text, attachments are ignored, and exactly one report link
must use HTTPS and an exact allowlisted hostname.

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
`to_number_normalized`, `destination_label`, `is_voicemail_destination`,
`is_duplicate`, `is_anomaly`, `anomaly_reasons`, `is_business_hours`, and
`is_holiday`. Booleans are written as `True`/`False`.

The lookup CSV is required and must have exactly `phone_number,agent` headers,
valid nonblank values, and no conflicting full-number mappings. Analysis phone
numbers are digits-only and never shortened. Destinations match full lookup
numbers before uniquely mapped final-four extensions; otherwise their label is
`Other`. `9999` is voicemail regardless of lookup label. Naive timestamps use
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
retrying rather than deleting the audit trail.

Exit status `0` means all required reports succeeded. Exit status `1` means at
least one report failed or a fatal configuration, mailbox, CSV, or state error
occurred. Invalid individual messages are skipped so later messages can run.
