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

## Environment Variables

`.env.example` is the environment template. Copy it to `.env` for development:

```bash
cp .env.example .env
```

- Required: `EMAIL_USERNAME`, `EMAIL_APP_PASSWORD`, and
  `NEXTIVA_EMAIL_SUBJECT` (an exact subject match).
- Mail defaults: `EMAIL_IMAP_SERVER=imap.gmail.com` and
  `NEXTIVA_EMAIL_SENDER=analytics@nextiva.com`.
- Security and timing defaults: `NEXTIVA_ALLOWED_HOSTS=ct.nextiva.com` and
  `NEXTIVA_REPORT_TIMEOUT_SECONDS=30`. Multiple allowed hosts are comma-separated.
- Output defaults: `NEXTIVA_OUTPUT_FILE=NextivaCallData.csv`. If
  `NEXTIVA_STATE_FILE` is omitted, `NextivaCallData.state.json` is created beside
  that CSV. `NEXTIVA_METADATA_FILE` and `NEXTIVA_ANALYSIS_FILE` similarly default
  to `NextivaCallData.metadata.sqlite3` and `NextivaCallData.analysis.csv`.
- `LOG_LEVEL` defaults to `INFO`; `LOG_FILE` defaults to `app.log`.

The application does not load `.env` automatically. Use `uv run --env-file .env` to load the development settings explicitly.

## Output and repeat runs

The CSV columns are exactly `Name`, `Time of Call`, `Duration`, `Direction`,
`Answered`, `From`, and `To`. Duration is stored as whole seconds. Displayed call
times and phone-number formatting are preserved.

Messages are processed oldest-first. A versioned JSON state file records their
Message-IDs (or stable IMAP UID identifiers when Message-ID is absent). The raw
CSV is append-only: it retains the first imported copy of each call row. The
analysis CSV is generated atomically from it and removes repeated normalized rows
while keeping first-seen order.

The metadata SQLite database records report periods, import time, warnings,
source-message IDs, and the relationship between every report and its call
segments. A repeated report delivered under another message ID is recorded as an
additional source but adds no raw or analysis rows. Missing, malformed, reversed,
or overlapping labelled report periods produce a warning without discarding valid
call rows.

The command returns exit status `0` when all required reports succeed. It returns
`1` for unsafe/missing configuration, authentication, browser, parsing, CSV, or
state errors. One bad report does not prevent later messages from being tried.

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
