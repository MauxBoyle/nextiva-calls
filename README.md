# nextiva-calls

`nextiva-calls` imports call records from Nextiva report links delivered to Gmail.
It validates each report before updating a CSV and remembers processed emails so
running the command again is safe.

## Installation

Clone the repository, move into the project directory, and install the dependencies:

```bash
git clone <repository-url>
cd nextiva-calls
uv sync
```

Google Chrome must also be installed. Selenium starts Chrome in headless mode, so
no browser window appears.

For Gmail, enable two-step verification and create an app password for this
command. Put that app password—not your normal Google password—in
`EMAIL_APP_PASSWORD`. Never commit your `.env` file.

## Usage

Run the application through its CLI entrypoint:

```bash
uv run nextiva_calls
```

Load the development environment explicitly and run the CLI:

```bash
uv run --env-file .env nextiva_calls
```

You can also run it as a Python module:

```bash
uv run python -m nextiva_calls
```

Create a management-shareable Membership weekly PDF. By default it covers the
seven complete Central-time days ending yesterday:

```bash
uv run --env-file .env nextiva_calls weekly-report
uv run --env-file .env nextiva_calls weekly-report --week-start 2026-08-17
```

`--week-start` accepts any ISO date (`YYYY-MM-DD`) as the start of a seven-day
historical period. By default, the printable three-page Letter PDF is written as
`reports/Nextiva_Weekly_<start>_to_<end>.pdf`; use `--output PATH` to choose a
different location.

## Environment Variables

`.env.example` is the environment template. Copy it to `.env` for development:

```bash
cp .env.example .env
```

- Required: `EMAIL_USERNAME`, `EMAIL_APP_PASSWORD`, `NEXTIVA_EMAIL_SUBJECT` (an
  exact subject match), and `NEXTIVA_AGENT_LOOKUP_FILE`. The lookup is an
  externally supplied CSV with exactly `phone_number`, `display_name`,
  `department`, and `destination_type` headers. Types are `agent` or `system`;
  values must be nonblank, phone values valid, and mappings non-conflicting.
- Mail defaults: `EMAIL_IMAP_SERVER=imap.gmail.com` and
  `NEXTIVA_EMAIL_SENDER=analytics@nextiva.com`.
- Security and timing defaults: `NEXTIVA_ALLOWED_HOSTS=ct.nextiva.com` and
  `NEXTIVA_REPORT_TIMEOUT_SECONDS=30`. Multiple allowed hosts are comma-separated.
- Output defaults: `NEXTIVA_OUTPUT_FILE=NextivaCallData.csv`. If
  `NEXTIVA_STATE_FILE` is omitted, `NextivaCallData.state.json` is created beside
  that CSV. `NEXTIVA_METADATA_FILE` and `NEXTIVA_ANALYSIS_FILE` similarly default
  to `NextivaCallData.metadata.sqlite3` and `NextivaCallData.analysis.csv`.
  `NEXTIVA_CANDIDATE_CALLS_FILE` defaults to
  `NextivaCallData.candidate-calls.csv` beside the raw export.
- Routing defaults: `NEXTIVA_MEMBERSHIP_HUNT_GROUP=Membership` and
  `NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM=2026-08-15T00:00:00-05:00`. The latter
  is midnight Central Time on August 15, 2026; it is inclusive and must include
  a timezone offset. Change these two settings if the Membership routing policy
  or its effective date changes.
- `LOG_LEVEL` defaults to `INFO`; `LOG_FILE` defaults to `app.log`.

The application does not load `.env` automatically. Use `uv run --env-file .env` to load the development settings explicitly.

## Output and repeat runs

The raw CSV columns are exactly `Name`, `Time of Call`, `Duration`, `Direction`,
`Answered`, `From`, and `To`. Duration is stored as whole seconds. Displayed call
times and phone-number formatting are preserved.

Messages are processed oldest-first. A versioned JSON state file records their
Message-IDs (or stable IMAP UID identifiers when Message-ID is absent). The raw
CSV is append-only: it retains the first imported copy of each call row. The
analysis CSV is generated atomically from it and removes repeated whitespace-
normalized rows while keeping first-seen order. Its columns are the seven raw
columns followed by `call_timestamp_ct`, `from_number_normalized`,
`to_number_normalized`, `destination_label`, `destination_type`, `is_voicemail_destination`,
`is_duplicate`, `is_anomaly`, `anomaly_reasons`, `is_business_hours`, and
`is_holiday`. Boolean values are `True` or `False`; admitted analysis rows always
have `is_duplicate=False`.

Phone values in analysis are digits-only without shortening either side. A
destination first matches its complete normalized lookup number, then a uniquely
mapped final four digits; unmatched or ambiguous destinations are `unknown`.
`9999` is always voicemail, regardless of its lookup row. Naive timestamps are interpreted in `America/Chicago`,
and offset-aware timestamps are converted there. Bad timestamps, unusable phone
values, ambiguous final-four matches, and unfamiliar Answered values remain in
analysis and are documented in `anomaly_reasons`.

Every analysis rebuild also atomically rebuilds the candidate-call CSV. It groups
duplicate-free segments by `Name`, Central-time timestamp, and normalized `From`
number. A row missing any of those values becomes its own `Unknown` candidate so
no segment is discarded or guessed into another call. Candidate rows keep a
stable hashed ID and their segment fingerprints for review. They include the
ordered unique destinations offered (including voicemail `9999`) and separate,
ordered duplicate-free evidence for offered agents, confirmed agent answers,
forwarding, system routing, voicemail reached, and unknown ordinary-Yes answers.
They also retain maximum duration, routing mode, outcome, and conflicts. `9999`
is never an agent answer. See the versioned [20-row review template](docs/candidate-call-validation-checklist-v1.csv).

Outcomes are conservative: voicemail plus non-voicemail affirmative evidence is
`Ambiguous`; ordinary `Yes` to an agent is `Confirmed human answered`; to a
system is `Connected / unknown attribution`; unknown attribution is `Answered /
unattributed`; forwarding or system routing alone is `Forwarded / routing only`;
then `Voicemail`, `Unanswered`, or `Unknown`. Invalid and conflicting data never
becomes a human answer.

Business hours are Monday through Friday from 9:00 AM (inclusive) to 5:00 PM
(exclusive), Central Time, excluding 2026 closures: Jan 1, Jan 19, Feb 16, May
25, Jun 19, Jul 3, Sep 7, Oct 12, Nov 11, Nov 26–27, and Dec 25.

The metadata SQLite database records report periods, import time, warnings,
source-message IDs, and the relationship between every report and its call
segments. A repeated report delivered under another message ID is recorded as an
additional source but adds no raw or analysis rows. Missing, malformed, reversed,
or overlapping labelled report periods produce a warning without discarding valid
call rows.

The command returns exit status `0` when all required reports succeed. It returns
`1` for unsafe/missing configuration, authentication, browser, parsing, CSV, or
state errors. One bad report does not prevent later messages from being tried.

For HTML messages, the importer identifies the report link by its visible
`Missed Calls` label (ignoring case and extra whitespace). Other Nextiva links,
such as navigation or footer links, are ignored.

## Weekly manager PDF

`weekly-report` compares a seven-day Central-time period with the preceding
seven days. Its default period ends yesterday, avoiding partial current-day
data. Membership metrics include calls to `NEXTIVA_MEMBERSHIP_HUNT_GROUP` and
calls that offer a destination matching the agent lookup (exact number or a
uniquely mapped final-four extension). Unrelated calls do not affect Membership
headlines, coverage, heatmaps, outcomes, routing, or agent metrics. Every
Membership-scoped call is part of the headline denominator, including
after-hours, weekend, holiday, and voicemail-only calls.

It includes reporting-period, data-through, and generation timestamps; a stacked
weekday outcome chart, coverage, routing, hunt-group, and heatmap views; and a
manager-only agent table. `Yes` means only `Confirmed human answered`; forwarded
or routing-only calls never count as `Yes`. It shows forwarded/routing-only,
voicemail, connected/unknown attribution, answered/unattributed, ambiguous,
unknown, and unanswered outcomes separately. The chart shows `No` for
unanswered calls and `Voicemail` separately, and adds `Unknown / Ambiguous` only
when present. The hunt-group comparison is the sole
cross-department exception: it uses all in-period candidates for Reception,
Membership, Certification, and Bookstore. The agent table shows at most five
lookup-listed named agents, then `Other / Unattributed`; additional named agents
are counted but omitted.
It deliberately does not make outbound, speed-of-answer, wait-time, or “agent
miss” claims, and it never displays customer or agent phone numbers.

The agent lookup used when generating the report is authoritative at report time.
Only agent-role destinations count as named-agent offers. Only confirmed
answered-agent evidence receives named-agent answer credit; system routing,
forwarding, voicemail, and unattributed evidence remain visible through their
outcomes and anomaly counts.

The dashboard's attribution coverage is `confirmed known-agent answers /
connected calls`. Connected calls are confirmed human answers plus `Connected /
unknown attribution`, `Answered / unattributed`, and `Ambiguous` outcomes.
`Forwarded / routing only` is not connected. A zero denominator is displayed as
a dash.

A week is marked **PRELIMINARY** unless valid report-period metadata in
`NEXTIVA_METADATA_FILE` continuously covers the entire week. Missing, invalid,
or gapped metadata keeps the label visible even when candidate calls exist.
“Data through” is the latest continuous metadata-confirmed coverage boundary in
the selected week, shown in Central Time.

## Troubleshooting

- **Gmail rejects login:** confirm IMAP access, two-step verification, and the app
  password. Do not use your regular password.
- **Chrome report timeout:** confirm Chrome is installed, the report link is still
  active, and increase `NEXTIVA_REPORT_TIMEOUT_SECONDS` if the page is slow.
- **Unexpected CSV header:** move or rename the existing CSV only after reviewing
  it. The importer refuses to overwrite files with a different schema.
- **Corrupt state file:** inspect or restore it rather than deleting it blindly.
  The metadata database and raw CSV protect report and row retries, but state
  controls which emails are fetched again.
- **Metadata database problem:** restore `NextivaCallData.metadata.sqlite3` from a
  backup if possible. Do not delete it casually: it is the report-to-call audit
  trail. If it must be rebuilt, keep the raw CSV and re-import only after reviewing
  the resulting provenance and analysis CSV.

## Testing

Run the tests:

```bash
uv run pytest
```

Run the tests and measure coverage:

```bash
uv run pytest --cov
```

## Documentation

Preview the documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build the static documentation site:

```bash
uv run mkdocs build
```
