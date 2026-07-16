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

Both commands are also available as a Python module:

```bash
uv run python -m aisc_salesforce profile-updates
```

`profile-updates` prints `created`, `reused`, `skipped`, and `failed` counts. A
successful run returns exit code `0`; missing configuration or a Salesforce
failure returns `1`.

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
