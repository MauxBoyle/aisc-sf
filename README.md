# aisc-salesforce

## Installation

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

## Configuration

`.env.example` is the committed template. Copy it to `.env`:

```bash
cp .env.example .env
```

Set these values in `.env`:

- `SF_CLIENT_ID` and `SF_CLIENT_SECRET`: credentials for the existing
  Salesforce Connected App.
- `CERTIFICATION_QUEUE_ID`: the Case owner used by `profile-updates`.
- `PRIMARY_RESPONDER_ID`: the Case Primary Responder used by
  `profile-updates`.
- `SF_LOGIN_URL` (optional): an org URL or complete OAuth token URL. It
  defaults to Salesforce's production login service.

The application loads `.env` automatically. This file and generated snapshots
are ignored by Git because they can contain sensitive data. Never commit
`.env`.

For a specific Salesforce org or sandbox, for example:

```bash
SF_LOGIN_URL=https://aisc.my.salesforce.com/services/oauth2/token
```

## Commands

Create a read-only snapshot:

```bash
uv run aisc_salesforce snapshot
```

Process recent audit notes and New company profile submissions:

```bash
uv run aisc_salesforce profile-updates
```

Stage New company profile submissions in a read-only CSV:

```bash
uv run aisc_salesforce stage-profile-updates
```

All commands are also available as a Python module:

```bash
uv run python -m aisc_salesforce profile-updates
```

`profile-updates` prints `created`, `reused`, `skipped`, and `failed` counts. A
successful run returns exit code `0`; missing configuration or a Salesforce
failure returns `1`.

### Profile Update staging

`stage-profile-updates` reads every submission whose `Status__c` is `New`. It
does not create or update any Salesforce records. The default output is:

```text
staged_profile_updates/YYYY-MM-DDTHH-MM-SSZ/profile_updates.csv
```

Choose a different parent directory when needed:

```bash
uv run aisc_salesforce stage-profile-updates \
  --output-dir /secure/staged-profile-updates
```

Submissions are grouped by Account ID and the submitter email after trimming
and case-insensitive comparison. Later nonblank values replace earlier values.
Different emails stay separate, and every blank-email submission stays
separate and receives a warning. Each CSV row preserves all source submission
IDs and names as JSON arrays.

The CSV has shared submission and Account columns, Key Data columns, and
prefixed role columns for `certification_`, `principal_`, `accounting_`,
`quality_`, and `new_york_`. Role columns preserve submitted values and record
the proposed resolution action, Contact ID, resolution source, source
submission/role, and any role-specific warning. New York does not have a title
column. When an existing Contact is resolved, a missing title or phone is
filled from that Contact where possible. Repeating the same contact information
in several roles does not create a warning, but conflicting emails for the same
submitted name are treated as ambiguous.

Before processing a staged row, inspect both `has_warnings` and `warnings`.
`warnings` is newline-separated and identifies ambiguous contacts, incomplete
Accounts, missing role lookups, partial addresses that could not be filled, and
other cases needing human review. An unmatched submitted name uses the
`create_contact` resolution action and warns that a new Contact will need to be
created.

Each run creates an independent timestamped directory. The CSV is first written
inside a temporary directory and is published only after the complete write
succeeds, so a failed run cannot leave a partial snapshot.

### Daily scheduling

The command is non-interactive, so an external scheduler can run it once per
day. For example, a Linux cron entry can change to the repository and run it at
2:00 AM:

```cron
0 2 * * * cd /path/to/aisc-sf && uv run aisc_salesforce profile-updates
```

Windows Task Scheduler can run the same command with the repository as its
working directory. Scheduling is deliberately kept outside this project.

### Safe retries and duplicate prevention

The automation checks Cases only on the relevant Account. It reuses the newest
appropriate Expected or Received Case and checks the Case and Audit Chatter
feeds for the exact automation message before posting. Therefore, rerunning
after a partial failure fills in missing work without duplicating completed
comments. New submission names are appended with ` / ` once.

Audit Dates from 30 days ago through today are eligible. Blank explanations,
`None`, and `N/A` are ignored. If adding a Profile Update name would make a
Case Subject longer than Salesforce's 255-character limit, that record is
reported as failed instead of silently truncating its identifier.

## Snapshot schema

The schema dictionary is stored at
`src/aisc_salesforce/data/salesforce_schema_dictionary.csv` and controls the
export. Only rows where `ScriptsUsing=TRUE` (ignoring case and whitespace) are
included. To add a field safely, add its object, Salesforce API name, and unique
`Sensible_Python_Key`, then set that column to `TRUE` and run the tests.

Each run writes `snapshots/YYYY-MM-DDTHH-MM-SSZ/` containing CSV files for
Account, Contact, Case, `Cert_Audit__c`, and `Company_Profile_Change__c`, plus
`manifest.json`. Use another parent directory when needed:

```bash
uv run aisc_salesforce snapshot --output-dir /secure/snapshot-location
```

## Testing

Run the tests:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov
```

## Documentation

Preview the documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build static documentation:

```bash
uv run mkdocs build
```
