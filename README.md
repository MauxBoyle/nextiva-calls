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
  that CSV.
- `LOG_LEVEL` defaults to `INFO`; `LOG_FILE` defaults to `app.log`.

The application does not load `.env` automatically. Use `uv run --env-file .env` to load the development settings explicitly.

## Output and repeat runs

The CSV columns are exactly `Name`, `Time of Call`, `Duration`, `Direction`,
`Answered`, `From`, and `To`. Duration is stored as whole seconds. Displayed call
times and phone-number formatting are preserved.

Messages are processed oldest-first. A versioned JSON state file records their
Message-IDs (or stable IMAP UID identifiers when Message-ID is absent). Exact
duplicate CSV rows are also skipped, which makes a retry safe if state saving
failed after the CSV was written.

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
  Row duplicate detection protects a retry, but state controls which emails are
  fetched again.

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
