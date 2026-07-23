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

Create a read-only application-stage count:

```bash
uv run aisc_salesforce application-snapshot
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

Preview the one-time correction of recent legacy Case subjects:

```bash
uv run aisc_salesforce rename-profile-update-cases
```

Consolidate the newest dated iMIS contact export:

```bash
uv run aisc_salesforce consolidate-imis-contacts \
  --directory imis_contactbasic
```

All commands are also available as a Python module:

```bash
uv run python -m aisc_salesforce profile-updates
```

`profile-updates` prints `created`, `reused`, `skipped`, and `failed` counts. A
successful run returns exit code `0`; missing configuration or a Salesforce
failure returns `1`.

### Application snapshot

`application-snapshot` reads Salesforce but does not create or update Salesforce
records:

```bash
uv run aisc_salesforce application-snapshot
uv run aisc_salesforce application-snapshot \
  --output-dir /secure/application-reports
```

The default output is:

```text
application_snapshots/YYYY-MM-DDTHH-MM-SSZ/application_snapshot.csv
```

If that UTC second already has a report, the new folder receives a suffix such
as `-01`. The CSV always contains the six standard rows in this order:
`Initial Review`, `Eligibility Review`, `Doc Audit`,
`Awaiting Audit Assignment`, `Awaiting Audit`, and
`Awaiting CRG Decision`. Its count columns are `domestic_regular`,
`domestic_expedited`, and `international_regular`; empty counts are `0`.

A Case qualifies when its Account certification status is exactly `Initials`,
its stage is not `Cancel`, Scope Change is not `Yes`, and its record type is
Fabricator Application, Erector Application, or International Application.
Null Case stage and Scope Change values remain eligible. Each qualifying Case
is counted once.

For each qualifying Case, the report uses only the newest valid Audit for its
Account that was created on or after the Case. Canceled or Withdrawn Audits and
Additional, Appeal, SA-NYC, or Preassessment Audit types are excluded; null
status and type values remain valid. `Cert_Audit_Date__c` sets an Audit's
effective date when present, otherwise the date portion of `CreatedDate` is
used. Ties use the full `CreatedDate` and then the Audit ID.

United States Accounts are Domestic. Only a Boolean Salesforce value of `true`
is Expedited; other Domestic Cases are Regular. Every other country, including
a missing country, is International Regular.

A `Doc_Audit` Case with a related Audit date is overridden by that date: today
or later becomes `Awaiting Audit`, while a past date becomes `Awaiting CRG
Decision`. A null or `New Application` Case stage becomes `Initial Review`, and
the Audit status Pending Acceptance becomes `Awaiting Audit Assignment`.
Ordinary Case stages have underscores changed to spaces. For
`Pending_AuditAssignment`, no related Audit, Reschedule in Progress, or a
missing audit date becomes `Awaiting Audit Assignment`; an audit dated today or
later becomes `Awaiting Audit`; and a past date becomes `Awaiting CRG Decision`.
“Today” is the `America/Chicago` calendar date.

Unexpected stage labels are appended alphabetically instead of being combined.
The command prints one warning with every unexpected label and count, followed
by the output path and qualifying Case count. Authentication, Salesforce,
invalid-date, or file failures return exit code `1` and do not publish a
partial report.

> [!WARNING]
> Application snapshots may contain sensitive operational counts. Their default
> directory is ignored by Git. When using `--output-dir`, choose an
> access-controlled location and do not commit or share reports through
> unapproved channels.

### iMIS contact consolidation

Place downloaded exports in `imis_contactbasic/` by default, or select another
folder with `--directory`. The command discovers dates from filenames rather
than file modification times:

- Fresh exports use `Full_CSContactBasic_YYMMDD.csv`; `YY` means `20YY`.
- Combined tables use `Combined_CSContactBasic_YYYYMMDD.csv`.

On the first run, the newest full export creates only a dated combined table.
On later runs, the newest full export must be newer than the newest combined
table. The command then publishes a new combined table plus
`Changed_CSContactBasic_YYYYMMDD.csv` and
`New_CSContactBasic_YYYYMMDD.csv`. Empty reports still contain the standard
headers.

Rows match by exact `iMIS Id`. Existing rows keep their position, newer
matching rows replace them, older-only contacts remain, and new contacts are
appended in fresh-export order. All 21 fields are compared as exact text, so
case, whitespace, and blank-value differences count. Identifiers such as
`iMIS Id`, `Company ID`, and `Major Key` remain strings, preserving leading
zeroes.

Files may arrange the 21 required headers in any order, but missing or extra
headers stop the run before output is published. Blank IDs are skipped with
their CSV row numbers. If one selected file repeats a nonblank ID, every row
with that ID is omitted from that file and a warning identifies the filename
and ID. A duplicated ID in the fresh export therefore cannot replace a valid
older row. Existing outputs are never overwritten, and failed writes clean up
temporary and partially published files.

> [!WARNING]
> iMIS contact exports and all three output types contain personal data. Their
> standard filename patterns are ignored by Git even in a custom directory.
> Keep them uncommitted, access-controlled, and shared only through approved
> secure channels.

### Profile Update Case subjects

New received Cases use this grammar:

```text
AISC Profile Update for {Account Name} - {Profile Update} {YY-MM-DD}
```

For example:

```text
AISC Profile Update for Acme Steel - PU-100 26-07-20
```

When another submission is reused on the same AISC Case, its complete
identifier and own received date are appended:

```text
AISC Profile Update for Acme Steel - PU-099 26-07-01 / PU-100 26-07-15
```

Identifiers are compared after trimming and without letter-case sensitivity,
but only complete identifiers match: `PU-10` does not match `PU-100`. If an
Account-scoped AISC Case already contains an identifier, automation skips that
submission without reading or posting Chatter.

Existing `Profile Update Received` subjects remain recognized by Case
automation and staging. Recurring automation leaves those legacy subjects in
their old format, which keeps retries compatible and prevents out-of-window
renames.

### One-time legacy subject correction

`rename-profile-update-cases` is preview-only by default:

```bash
uv run aisc_salesforce rename-profile-update-cases
```

Review every printed `old subject -> new subject` proposal. Apply those same
Subject-only changes explicitly:

```bash
uv run aisc_salesforce rename-profile-update-cases --apply
```

The command checks today and the preceding six `America/Chicago` calendar
dates. Those local midnight boundaries are converted to UTC for Salesforce
`CreatedDate`, which Salesforce stores in GMT. It accepts dated legacy prefixes
in the forms `YYYY-MM-DD:`, `YYYY-MM-DD -`, and `YY-MM-DD -`. The date embedded
in the legacy subject—not the Case creation date—is assigned to every Profile
Update identifier in that subject.

Subjects without a trustworthy embedded date and corrected subjects over 255
characters are skipped with an explanation. Apply mode PATCHes only `Subject`,
continues after an individual failure, and exits nonzero if any write fails.
Both modes print per-Case results and totals for `matched`, `updated` or
`would update`, `skipped`, and `failed`. A safe rerun ignores already-corrected
AISC subjects.

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
and case-insensitive comparison. Later nonblank values replace earlier values,
except that every nonblank Comments and Other Personnel Notes value is
preserved in submission order and joined with a newline. Different emails stay
separate, and every blank-email submission stays separate and receives a
warning. Each CSV row preserves all source submission IDs and names as JSON
arrays.

The CSV has shared submission and Account columns, Key Data columns, and
prefixed role columns for `certification_`, `principal_`, `accounting_`,
`quality_`, and `new_york_`. Role columns preserve submitted values and record
the proposed resolution action, Contact ID, resolution source, source
submission/role, and any role-specific warning. New York does not have a title
column. When an existing Contact is resolved, a missing title or phone is
filled from that Contact where possible. Repeating the same contact information
in several roles does not create a warning, but conflicting emails for the same
submitted name are treated as ambiguous.

`has_key_updates` is `true` only when at least one source has the exact
Salesforce `Type__c` value `"Key Data"`. Populated Key Data fields remain
visible, but do not set this classification by themselves. The required
`has_contact_derived_values` column is `true` when a nonblank role title or
phone was copied from another submitted role or a Salesforce Contact. The
required `has_no_update_content` column is `true` when the group has no
submitted Key Data fields, role fields, Comments, or Other Personnel Notes.
Submitter, Account, and Case metadata, `Type__c`, and fallback-derived values do
not count as submitted update content.

Before processing a staged row, inspect `has_contact_derived_values`,
`has_no_update_content`, `has_warnings`, and `warnings`.
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
Profile Update names. It also notes when contact details were supplemented or
when the combined update has no submitted update content. At the Continue/Quit
checkpoint, press Enter, type `C`, or type `Continue` to review that row. Type
`Q` or `Quit` to stop safely. Only this checkpoint has a default; change
decisions always require an explicit answer. Published CSV files must include
both new metadata columns; older staged files fail validation.

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
comments. AISC subjects store every appended submission as an exact
Profile Update/date pair. Legacy subjects remain unchanged except when the
dedicated correction command is explicitly run with `--apply`.

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
