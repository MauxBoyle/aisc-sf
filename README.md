# aisc-salesforce

## Installation

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

## Usage

Create a read-only snapshot via the CLI entrypoint:

```bash
uv run aisc_salesforce snapshot
```

Use the development environment settings explicitly:

```bash
uv run aisc_salesforce snapshot
```

Or run it as a Python module:

```bash
uv run python -m aisc_salesforce snapshot
```

## Environment Variables

`.env.example` is the committed template. Copy it to `.env` for development:

```bash
cp .env.example .env
```

Add the existing Salesforce Connected App values for `SF_CLIENT_ID` and
`SF_CLIENT_SECRET`. The application uses the client-credentials OAuth2 flow,
matching the existing Salesforce gateway. It reads this local file
automatically. `.env` and generated snapshot files are sensitive and are
ignored by Git; never commit them.

For a specific Salesforce org or sandbox, set `SF_LOGIN_URL` to either its base
URL or its full token endpoint. For example:

```bash
SF_LOGIN_URL=https://aisc.my.salesforce.com/services/oauth2/token
```

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

The command is ready for a future scheduler because it is non-interactive and
uses exit codes. Choosing cron, Windows Task Scheduler, or GitHub Actions is a
separate operational decision.


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
