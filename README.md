# nextiva-calls

`nextiva-calls` imports call records from Nextiva report links delivered to Gmail.
It validates each report before updating a CSV and remembers processed emails so
running the command again is safe.

## Reliable daily capture

Validated Nextiva reports are saved to the raw CSV, metadata database, and
processed-message state before calendar-based analysis runs. If the OPM holiday
calendar cannot refresh, call capture still succeeds and analysis/candidate CSVs
are rebuilt automatically after a later successful refresh. The importer uses a
local OPM cache: it refreshes when the cache does not cover the current year and,
from December 15, until it also covers the next year. The cache and its adjacent
holiday-refresh status JSON are local operational files, not Git files. One safe
email alert is sent to `EMAIL_USERNAME` for each continuous refresh outage.

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

Create a management-shareable Membership + Certification weekly PDF. By default it covers the
seven complete Central-time days ending yesterday:

```bash
uv run --env-file .env nextiva_calls weekly-report
uv run --env-file .env nextiva_calls weekly-report --week-start 2026-08-17
```

`--week-start` accepts any ISO date (`YYYY-MM-DD`) as the start of a seven-day
historical period. By default, the printable four-page Letter PDF is written as
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
- Holiday calendar: each import and weekly report refreshes OPM's official
  [federal-holiday iCalendar feed](https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/).
  The validated response is cached locally (`NEXTIVA_HOLIDAY_CACHE_FILE`, beside
  the raw CSV by default). If refresh fails, only a cache that covers the report
  period can be used; otherwise the run stops clearly rather than treating days
  as open. OPM's observed dates are used as published.
- Organization changes live in `NEXTIVA_HOLIDAY_OVERRIDES_FILE=closure_dates.csv`
  (the older `NEXTIVA_CLOSURE_DATES_FILE` name remains an alias). It must be a
  unique `date,name,status` CSV. `closed` adds or replaces a closure and `open`
  explicitly reopens an OPM closure.
- Mail defaults: `EMAIL_IMAP_SERVER=imap.gmail.com` and
  `NEXTIVA_EMAIL_SENDER=analytics@nextiva.com`.
- Security and timing defaults: `NEXTIVA_ALLOWED_HOSTS=ct.nextiva.com` and
  `NEXTIVA_REPORT_TIMEOUT_SECONDS=30`. Multiple allowed hosts are comma-separated.
- Output defaults: `NEXTIVA_OUTPUT_FILE=NextivaCallData.csv`. If
  `NEXTIVA_STATE_FILE` is omitted, `NextivaCallData.state.json` is created beside
  that CSV. `NEXTIVA_METADATA_FILE` and `NEXTIVA_ANALYSIS_FILE` similarly default
  to `NextivaCallData.metadata.sqlite3` and `NextivaCallData.analysis.csv`.
  The metadata database is a tracked import-audit file: an intentional import can
  update it, so include its change in the related Git commit rather than deleting
  it to make the working tree clean.
  `NEXTIVA_CANDIDATE_CALLS_FILE` defaults to
  `NextivaCallData.candidate-calls.csv` beside the raw export.
- Routing defaults: `NEXTIVA_MEMBERSHIP_HUNT_GROUP=Membership`,
  `NEXTIVA_CERTIFICATION_HUNT_GROUP=Certification Hunt Group`, and
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
ordered duplicate-free evidence for offered agents, recorded agent offers,
confirmed agent answers, forwarding, unknown agent statuses, system routing,
voicemail reached, and unknown ordinary-Yes answers.
They also retain maximum duration, routing mode, outcome, and conflicts. `9999`
is never an agent answer. See the versioned [20-row review template](docs/candidate-call-validation-checklist-v1.csv).

Outcomes are conservative: voicemail plus non-voicemail affirmative evidence is
`Ambiguous`; ordinary `Yes` to an agent is `Confirmed human answered`; to a
system is `Connected / unknown attribution`; unknown attribution is `Answered /
unattributed`; forwarding or system routing alone is `Forwarded / routing only`;
then `Voicemail`, `Unanswered`, or `Unknown`. Invalid and conflicting data never
becomes a human answer.

Business hours are Monday through Friday from 9:00 AM (inclusive) to 5:00 PM
(exclusive), Central Time, excluding closures from the refreshed OPM calendar
and any organization-specific overrides.

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

For the day-to-day manager routine, safe configuration changes, metric
definitions, and evidence procedures, use the [manager operating runbook](docs/operations.md).

`weekly-report` compares a seven-day Central-time period with the preceding
seven days. Its default period ends yesterday, avoiding partial current-day
data. Headline metrics, business-hours coverage, and the heatmap use the
combined Membership + Certification scope: calls to the configured
`NEXTIVA_MEMBERSHIP_HUNT_GROUP`, `NEXTIVA_CERTIFICATION_HUNT_GROUP`, or
calls offering a matching Membership or Certification agent (exact number or a
uniquely mapped final-four extension). This is a union: a cross-department call
counts once in combined totals. Every scoped call is included, including
after-hours, weekend, holiday, and voicemail-only calls.

It includes reporting-period, data-through, and generation timestamps; separate
Membership and Certification 14-day outcome charts and tables, with the prior
week first and the current week second; combined coverage, routing,
hunt-group, and heatmap views; a manager-only agent table; and a fourth-page
automated-insights section. A cross-department
call appears in each matching department's outcome detail. `Yes` means only `Confirmed human answered`; forwarded
or routing-only calls never count as `Yes`. It shows forwarded/routing-only,
voicemail, connected/unknown attribution, answered/unattributed, ambiguous,
unknown, and unanswered outcomes separately. The chart shows `No` for
unanswered calls and `Voicemail` separately, and adds `Unknown / Ambiguous` only
when present. The hunt-group comparison is the sole
cross-department exception: it uses all in-period candidates for Reception,
Membership, Certification, and Bookstore. The agent table shows every
lookup-listed agent (never hunt groups, voicemail, or `Other / Unattributed`).
It deliberately does not make outbound, speed-of-answer, wait-time, or “agent
miss” claims, and it never displays customer or agent phone numbers.

The agent lookup used when generating the report is authoritative at report time.
Attribution Coverage is `confirmed known-agent answers / connected calls`.
Connected calls include confirmed-human, connected/unknown-attribution,
answered/unattributed, and ambiguous outcomes; forwarded/routing-only calls are
excluded. The report also has a separate unique connected-call reconciliation:
Garrett, Tye, Leah, Ed, and Karla each receive a call only when they are the one
confirmed named answer; multiple confirmed named answers are grouped together;
all other connected calls are `Other`. These counts add up to connected calls and
are not routing offers.
The table's **offer answer rate** is the percentage of recorded offers answered,
not a performance score or miss rate. A recorded offer is one duplicate-free
call-agent pair with exact normalized `Yes` or `No` evidence; `Yes` also counts
as one answer. `Yes - Forwarded` counts only as forwarded away. An unfamiliar
status for an agent is shown only as a data-quality exclusion and never enters
the rate. Agents with zero recorded offers show `N/A`. Simultaneous routing can
offer one call to multiple agents, so agent offers need not equal call counts.

Only Membership and Certification agent-role destinations may appear in this
table or produce agent-level attribution data. System routing, voicemail, other
departments, and unattributed evidence remain visible only through call-level
outcomes and anomaly counts. When `weekly-report` finds an older candidate-call CSV header,
it atomically rebuilds that derived file from the corresponding analysis CSV
before creating the report.

The dashboard's attribution coverage is `confirmed known-agent answers /
connected calls`. Connected calls are confirmed human answers plus `Connected /
unknown attribution`, `Answered / unattributed`, and `Ambiguous` outcomes.
`Forwarded / routing only` is not connected. A zero denominator is displayed as
a dash.

A week is marked **PRELIMINARY** unless explicit report metadata or inferred
candidate-date coverage continuously covers the entire week. When Nextiva omits
a labelled period, the earliest and latest valid call dates provide inferred
coverage and “Data through” says so. Missing, invalid, or gapped coverage keeps
the label visible.

The automated-insights page compares only the combined-scope voicemail rate and
confirmed-human-answer rate. It calls a weekly rate change significant only when
both weeks have at least 20 scoped calls and the change is at least 10 percentage
points. If that rate threshold is reached with a smaller week, it shows the
counts and rates but explicitly makes no significance claim. If either week is
**PRELIMINARY**, it shows coverage and data-through information only and
suppresses all trend observations. If no rule is met, it says so plainly. This
page never displays phone numbers and makes no agent-miss, wait-time,
speed-of-answer, or outbound claims.

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
