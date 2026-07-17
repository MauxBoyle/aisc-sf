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
- `CERTIFICATION_QUEUE_ID`: the Case owner used by `profile-updates` and
  `process-profile-updates`.
- `PRIMARY_RESPONDER_ID`: the Case Primary Responder used by
  `profile-updates` and `process-profile-updates`.
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

Create/reuse Cases, publish a fresh staging CSV, and review it interactively:

```bash
uv run aisc_salesforce process-profile-updates
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

### Interactive Profile Update processing

`process-profile-updates` performs the complete processing workflow in this
order:

1. Run the existing Case automation.
2. Stop if any required Case operation fails.
3. Publish a fresh `profile_updates.csv`.
4. Read that published file back from disk.
5. Review rows in Account-and-Case batches.

The command prints progress before and after authentication, Case preparation,
staging, CSV publication, CSV validation, and review startup. Section
separators make workflow stages, Cases, staged rows, Contact roles, and response
emails easier to distinguish.

Use `--output-dir` the same way as the staging command:

```bash
uv run aisc_salesforce process-profile-updates \
  --output-dir /secure/staged-profile-updates
```

Before each staged CSV row, the command shows the Account, submitter, and source
Profile Update names. At the Continue/Quit checkpoint, press Enter, type `C`,
or type `Continue` to review that row. Type `Q` or `Quit` to stop safely.
Only this checkpoint has a default; change decisions always require an explicit
answer.

Each real field change accepts a shortcut or its complete decision phrase:

- `A` or `apply automatically` writes the displayed value to Salesforce.
- `M` or `make manually` pauses for the reviewer to make the change, then refetches
  Salesforce and continues only if the value matches.
- `N` or `will not be made` records the rejection without changing Salesforce.

Shortcuts and phrases are case-insensitive. Audit entries always store the
complete phrase.

Already-current values are recorded as no-ops and do not prompt. For each
submitted Contact role, the command searches all Salesforce Contacts for the
exact submitted email address. One match is fetched and each mismatched
submitted field is reviewed separately. No match leads to an explicit Contact
creation decision. In both cases, assigning the resolved Contact to the
Account role is a separate decision. Potential-contact lists are never shown.
More than one exact-email match is audited as an error; the Case stays Pending
and its source submissions remain open for a safe retry. A Contact without the
Salesforce-required Last Name cannot be created automatically.

Before individual fields are reviewed, an existing Contact's name, title,
email, and phone are shown together. The submitted values are shown together
before a new-Contact decision. Account-role proposals show current and proposed
Contact names and emails; Salesforce Contact IDs remain internal to writes and
the audit trail instead of appearing in decision prompts.

Comments, Other Personnel notes, Key Update answers, effective dates, warnings,
and same-day Account History are shown once at the beginning of each Case
batch. Reused submissions and local calendar days are fetched only once during
that Case review.

The timestamped staging folder contains:

```text
profile_updates.csv
review_audit.jsonl
response_emails.txt
```

The JSON Lines audit is flushed after every decision and Salesforce result.
The response file contains one generated paragraph per submitter email.
Account-information changes keep the `ITEM: NEW INFORMATION` and
`Replaces OLD INFORMATION` format. Each submitted Contact role is consolidated
into one contact-information line. An unchanged role ends with `- no change`;
`Replaces ...` appears only when existing role information was actually
replaced. The command prints the text but does not send email itself; after
sending it through the normal email system, the reviewer confirms `yes` or
`no`.

When all rows in a Case batch are resolved, source Profile Updates are set to
`Closed`. Answering `yes` after every generated response confirms the email was
sent and closes the Case too; otherwise the Case remains `Pending`.

A deliberate `Q` or `Quit` is audited and returns exit code `0`. The current
Case stays `Pending`, its Profile Updates stay open, no response text is
generated for its unfinished row, and later batches are not started. Earlier
completed batches and their audit records are preserved. An interruption,
failed write, or failed manual verification instead exits nonzero while leaving
unfinalized Profile Updates open and the Case Pending. On any retry, previously
applied Salesforce values are fetched again and recorded as no-ops.

> [!WARNING]
> Staging, audit, and response files contain personal and Salesforce data.
> They are ignored by Git, but they must still be stored in an
> access-controlled location and shared only through approved secure channels.

### Daily scheduling

The `profile-updates` Case-preparation command is non-interactive, so an
external scheduler can run it once per day. The
`process-profile-updates` review command remains interactive. For example, a
Linux cron entry can change to the repository and run Case preparation at
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
