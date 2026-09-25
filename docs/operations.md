# Manager operating runbook

Use this guide for the weekly Membership + Certification call-report routine.
You do not need to edit Python code.

## Before you begin

Keep these local files together and backed up: raw `data/NextivaCallData.csv`,
`data/NextivaCallData.metadata.sqlite3`, the analysis and candidate CSVs, and your
agent lookup CSV. The raw CSV and metadata database are the
evidence trail; do not delete them to fix an error.

## Project-file layout and one-time migration

Tracked manager settings live in `config/`: `closure_dates.csv` and
`weekly_report_recipients.txt`. Operational files live in `data/`. Git ignores
the operational contents of `data/` except
`data/NextivaCallData.metadata.sqlite3`, which is intentionally tracked as the
import-audit history. Reports remain local in `reports/`, and `.env` remains a
local secret.

Existing root-level operational files are not moved automatically. After making
a backup, manually move only the files you have and want to keep. For a standard
installation, the safe target names are:

```text
agent_lookup.csv                         -> data/agent_lookup.csv
NextivaCallData.csv                      -> data/NextivaCallData.csv
NextivaCallData.state.json               -> data/NextivaCallData.state.json
NextivaCallData.analysis.csv             -> data/NextivaCallData.analysis.csv
NextivaCallData.candidate-calls.csv      -> data/NextivaCallData.candidate-calls.csv
NextivaCallData.opm-holidays.ics         -> data/NextivaCallData.opm-holidays.ics
NextivaCallData.holiday-refresh.json     -> data/NextivaCallData.holiday-refresh.json
app.log                                  -> data/app.log
```

Then update `.env` to use `data/agent_lookup.csv`,
`data/NextivaCallData.csv`, and `config/closure_dates.csv` (the example file
already uses these paths). Do not move, delete, or recreate the metadata
database as part of this local-file migration; the repository supplies its
tracked audit database at the new `data/` path.

The report uses Central Time. **PRELIMINARY** means neither explicit metadata
nor inferred candidate-date coverage spans a full selected week. A labelled range
or Nextiva's leading displayed daily range (for example, `9/23/26 12:00 AM —
9/24/26 11:59 PM`) confirms coverage, even for a report with no calls. When no
reliable range is available, “Data through” explicitly says coverage was inferred
from call dates. Treat a preliminary report as a snapshot, not a completed trend.

## Daily import checklist

1. Check that `.env` has the email settings and that lookup and holiday-override CSV
   paths exist. Never share or commit `.env`; it contains an app password.
2. Run `uv run --env-file .env nextiva_calls`.
3. Confirm the final log says `Import complete` and the command exits with
   status 0. Review warnings about skipped reports, overlaps, or duplicates.
4. Confirm the expected raw, analysis, candidate, state, and metadata files are
   present or updated. Keep raw files private because they can contain phone numbers.

If it fails, preserve the log and existing files. Correct the stated
configuration issue, restore a damaged file from backup where possible, then
rerun. Do not delete the state or metadata database as a first response. Gmail
authentication, Chrome availability, expired report links, and invalid CSV
headers are common causes; [Usage](usage.md) has setup details.

## Monday PDF routine

After the prior week’s daily imports are complete, run:

```bash
uv run --env-file .env nextiva_calls weekly-report
```

This writes `reports/Nextiva_Weekly_<start>_to_<end>.pdf` for the seven complete
days ending yesterday and compares it to the prior seven days. To recreate a
specific week, use `--week-start 2026-08-17`. Confirm the PDF opens, has the
expected period, and visibly review **PRELIMINARY** and “Data through” before
sharing. OPM's official federal-holiday iCalendar feed is fetched only when its
local cache does not cover the current year; from December 15, refreshes continue
until the cache covers the following year too. If a daily import cannot refresh
the calendar, it still saves raw calls, metadata, and processed-email state, then
marks analysis pending for the next nightly retry. One safe alert email goes to
`EMAIL_USERNAME` for an uninterrupted outage, and a successful refresh resets it.
Keep the local `data/NextivaCallData.opm-holidays.ics` cache and
`data/NextivaCallData.holiday-refresh.json` sidecar with the raw CSV, but do not
commit either. A weekly PDF still needs a usable calendar.

To email the completed PDF, run:

```bash
uv run --env-file .env nextiva_calls weekly-report --send
```

Before the first live send, use `uv run --env-file .env nextiva_calls weekly-report --send --test`.
It delivers only to `EMAIL_USERNAME`. Normal recipients are maintained in the
versioned `config/weekly_report_recipients.txt` file, one address per line; blank lines
and `#` comments are allowed. Keep `.env` private because it contains the Gmail
app password.

## Windows Task Scheduler

Create a task whose Program/script runs `uv` with arguments
`run --env-file .env nextiva_calls weekly-report --send`. Set **Start in** to
the project directory (the folder containing `.env` and
`config/weekly_report_recipients.txt`). This makes the relative file paths reliable.
Do not place `.env` in a shared folder or commit it to Git.

## What the PDF means

The dashboard scope is the combined union of calls to the configured Membership
hunt group, the configured Certification hunt group, or a matching Membership
or Certification agent offer. A cross-department call counts once in combined
totals and can appear in both department-detail sections. Every scoped call,
including voicemail-only, weekend, holiday, and after-hours calls, is in the
headline denominator.

| Item | Plain-language definition and denominator |
|---|---|
| Calls / outcomes | One reconstructed inbound candidate call. `Yes` is only a confirmed ordinary `Yes` to a known agent. `No` is unanswered; voicemail, forwarded/routing-only, unknown attribution, ambiguous, and unknown remain separate. |
| Business-hours categories | Weekdays 9:00 AM inclusive to 5:00 PM exclusive CT. A configured closure is `Holiday`, not business hours; weekends and after-hours are separate. |
| Attribution coverage | Confirmed known-agent answers divided by connected calls. Connected means confirmed human answer, connected/unknown attribution, answered/unattributed, or ambiguous. Forwarded/routing-only is not connected. A zero denominator is a dash. |
| Agent offers and answer rate | A recorded offer is one duplicate-free call-agent pair with exact `Yes` or `No` evidence. Answer rate is recorded offers answered / recorded offers. `Yes - Forwarded` is forwarded away, not an answer; unknown statuses are exclusions. Zero offers is `N/A`. This is not a performance score or miss rate. |
| Department detail | Membership and Certification have separate weekday-outcome charts and reconciliation. The Reception/Membership/Certification/Bookstore comparison is the hunt-group table’s all-in-period exception. |
| Automated insights | Combined-scope voicemail and confirmed-human-answer rates only. “Significant” requires at least 20 scoped calls in both weeks and a change of at least 10 percentage points. Smaller samples show counts without that claim. If either week is preliminary, trends are suppressed. |

The PDF deliberately makes no outbound, speed-of-answer, wait-time, or “agent
miss” claim and excludes phone numbers. Simultaneous routing can create offers
for several agents from one call, so offers are not call counts.

## Safe manager configuration changes

### Add or update an agent

Edit the lookup CSV with exactly this header:

```text
phone_number,display_name,department,destination_type
555-0100,Alex Example,Membership,agent
```

All values are required. `destination_type` is `agent` or `system`; use full
numbers where possible and keep final-four extensions unambiguous. Back it up,
change one row, run the daily import, and confirm validation succeeds. The lookup
at PDF-generation time is authoritative.

### Update closures

Edit the versioned `config/closure_dates.csv` (or the file named by
`NEXTIVA_HOLIDAY_OVERRIDES_FILE`) with exactly these columns:

```csv
date,name,status
2026-12-24,Winter break,closed
2026-09-07,Office open,open
```

Use one unique `YYYY-MM-DD` date per row. `closed` adds or replaces a closure;
`open` explicitly reopens an OPM closure. Run an import or weekly report to
validate it. Because closures alter classifications, run an import to regenerate
analysis and candidate data before reporting that period.

### Change Membership routing

`NEXTIVA_MEMBERSHIP_HUNT_GROUP`, `NEXTIVA_CERTIFICATION_HUNT_GROUP`, and
`NEXTIVA_MEMBERSHIP_SIMULTANEOUS_FROM` are manager-configurable. The timestamp
needs an offset, for example `2026-08-15T00:00:00-05:00`. Change them together,
record why and when, then rerun an import. Reception and Bookstore comparison
group logic is code-managed and needs a developer change.

## Manual evidence trail for a future outbound question

The weekly PDF is not evidence of an outbound call. First preserve original
report details: report filename or message ID, import time, known date/time, and
the phone number only where policy permits. Search retained raw and analysis data
by that known date/time and number, then use metadata to identify source reports.
Record only the necessary evidence: source-report identifier, matching timestamp,
direction, and a short conclusion. Do not copy unrelated phone numbers or whole
raw rows into notes; follow your organization’s retention and access rules.
