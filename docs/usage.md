# Usage

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Configuration

Copy the environment template and fill in the Salesforce values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---:|---|
| `SF_CLIENT_ID` | Yes | Salesforce Connected App client ID |
| `SF_CLIENT_SECRET` | Yes | Salesforce Connected App secret |
| `CERTIFICATION_QUEUE_ID` | For profile updates | Owner ID for Profile Update Cases |
| `PRIMARY_RESPONDER_ID` | For profile updates | Primary Responder ID for Profile Update Cases |
| `PARTICIPANT_PROFILE_ID` | For user-sync check | Participant Profile ID |
| `PARTICIPANT_PRINCIPAL_PROFILE_ID` | For user-sync check | Participant Principal Profile ID |
| `PARTICIPANT_AP_PROFILE_ID` | For user-sync check | Participant AP Profile ID |
| `PARTICIPANT_QC_PROFILE_ID` | For user-sync check | Participant QC Profile ID |
| `PARTICIPANT_RAS_PROFILE_ID` | For user-sync check | Participant RAS Profile ID |
| `SF_LOGIN_URL` | No | Salesforce org URL or complete OAuth token URL |

The CLI loads `.env` without replacing environment variables that are already
set.

## Participant-drop intake

Open the participant-drop menu from the terminal:

```bash
uv run aisc_salesforce participant-drop
```

The first menu offers **Start a new withdrawal** and **Complete an existing
withdrawal**, in that order. Complete currently displays `Complete an existing
withdrawal is not implemented yet.` and exits successfully. Start continues to
the scenario menu (Unpaid Invoice, Withdrawal Request, CRG drop, or another
participant drop). Salesforce configuration is loaded and the connection is
created only after Start is selected.

Reference values are optional. An invoice reference is matched exactly against
`Cert_Invoice__c.Name`, a Withdrawal Request reference against
`Withdrawal_Request__c.Name`, and a CRG reference against `Cert_Audit__c.Name`.
Those records supply their Account lookup. The workflow does not use
Salesforce's built-in `Invoice` object; it uses the custom `Cert_Invoice__c`
object and its `Cert_Account__c` Account lookup.

When a reference does not resolve an Account, the command tries a normalized
Certification ID and then normalized partial company-name matches. A single
match proceeds automatically; multiple matches always require an explicit
choice.

Each intake has a withdrawal reason before it is recorded. Unpaid Invoice
starts with `#NonPayment`; press Enter to keep that default or choose
`#Economy`, `#Closed`, `#ROI`, `#NewOwner`, or `#BusModel` instead. CRG drop
automatically uses `#CRG`, so no reason menu is shown. Withdrawal Request and
Other participant drop require one of the five selectable reasons.
Type `cancel`, `c`, `q`, or `quit` at any prompt, including the reason menu, to
stop. Cancellation makes no Salesforce write; cancellation from the initial
menu also avoids a Salesforce connection. A completed intake posts exactly
`Withdrawal in progress: <scenario>.` to the selected Account's Chatter feed
and appends `M/D/YY Withdrawal: <hashtag> <reference>` to its
`Cert_Notes__c` Certification Notes field. The reference portion is omitted
when blank, and the workflow preserves existing note text. The selected reason
is available in the Python intake result for later work; this command does not
create or update `Certification_Withdrawal__c` records.

## Participant user-provisioning Profile check

Run the read-only configuration check before using participant provisioning:

```bash
uv run aisc_salesforce check-user-sync-config
```

The command reads the five Salesforce `Profile` records configured above and
checks that each configured ID matches the application contract and that its
Salesforce `Name` is the expected participant Profile name. It does not create,
update, or delete Salesforce records or local files. It returns exit code `0`
when every Profile matches. Missing configuration, an incorrect ID, a missing
Profile, a name mismatch, authentication failure, or a Salesforce read failure
returns exit code `1`.

License validation is intentionally out of scope for now because the expected
external-user license value has not yet been defined.

## User reconciliation planner

Use the planner to inspect what a participant User would need for one Contact:

```bash
uv run aisc_salesforce reconcile-user CONTACT_ID
uv run aisc_salesforce reconcile-user CONTACT_ID --json
```

This is a read-only command: it authenticates and performs focused Salesforce
queries, but never creates, updates, or deletes a record. The regular output
is a short review summary. `--json` prints a stable, automation-friendly plan
with desired values, role assignments, linked Users, proposed changes, and
blockers.

The Contact is the source of the desired `ContactId`, name, email, and mailing
address values. Its trimmed, lowercased Email is also the desired `Username`.
An empty or invalid email, incomplete Contact source data, no qualifying role,
invalid participant Profile configuration, a Profile name mismatch, a username
owned by another User (active or inactive), or more than one active linked User
is a blocker. The planner describes a create when no active User is linked and
field-by-field changes when exactly one is linked, but it never carries out
either proposal. Inactive linked Users are shown as conflict-only records and
are never update targets.

## Developer rule: use the Salesforce enum catalog

When adding or changing code that uses a Salesforce **picklist** value, use
the matching `StrEnum` from `aisc_salesforce.salesforce_enums` instead of
putting the value in quotes in the script. This applies to values used in
SOQL filters, Python comparisons, and payloads sent to Salesforce.

```python
from .salesforce_enums import CaseStatus

# Good: one named, reusable definition of the Salesforce value.
where = f"Status = '{CaseStatus.PENDING}'"
payload = {"Status": CaseStatus.CLOSED}

# Avoid: a second, easy-to-mistype copy of the Salesforce value.
where = "Status = 'Pending'"
```

If Salesforce has a picklist field that is not yet cataloged:

1. Add a clearly named member to the appropriate enum in
   `src/aisc_salesforce/salesforce_enums.py`.
2. Add the `(object_name, field_name)` entry to `SALESFORCE_ENUMS` so the
   audit can check it.
3. Use that enum member in the workflow and add or update a focused test.
4. Run `uv run pytest` and, when Salesforce credentials are available,
   `uv run aisc_salesforce audit-picklist-enums`.

This rule is for Salesforce picklist values, such as `"Pending"` or
`"Participant Portal"`. Field API names (such as `"Status"`) and ordinary
display text are not picklist values, so they do not belong in this catalog.

## Picklist enum audit command

Run the manual, read-only audit with the standard Salesforce credentials:

```bash
uv run aisc_salesforce audit-picklist-enums
```

No additional configuration is required. The command combines fields selected
by the schema dictionary with fields read by the application, Profile Update,
staging, processing, and Case-rename workflows. It uses Salesforce REST
Describe metadata to keep only fields whose type is `picklist` or
`multipicklist`. Relationship fields are checked on the object that owns them;
for example, `Account.Cert_Certification_Status__c` is audited as an Account
field.

For each matching field, the command reads ordinary records whose
`LastModifiedDate` is at or after the current UTC instant moved back two
calendar years. Salesforce field-history objects, such as `AccountHistory` and
custom `__History` objects, do not have `LastModifiedDate`, so their immutable
entries use `CreatedDate` instead. The month, day, and time are preserved. A
February 29 cutoff becomes February 28 when the target year is not a leap year.
Multi-select values are split on Salesforce's semicolon separator, and all
values are compared to Python enum values exactly, including capitalization.

Example findings:

```text
Picklist enum audit (audit window starts 2024-07-24T15:30:00Z):
Case:
  Status:
    - Unexpected Status
  Custom_Field__c [no enum catalog]:
    - Existing Value
Audit complete. Missing and uncataloged values are informational; no Salesforce data was changed.
```

A normal field heading means the listed values are missing from that field's
Python enum. `[no enum catalog]` means the project has no enum for the field, so
all nonempty values observed in the two-year window are listed. Null and empty
values are ignored. A clean audit prints a clear `no missing values found`
message.

Missing or uncataloged values are informational, so the command returns exit
code `0`. Authentication, Describe metadata, and Salesforce query failures
print `Picklist enum audit failed: ...` and return exit code `1`. The command
does not create a report file and never creates or updates Salesforce records.
REST Describe is used because this audit checks values actually stored in
recent records, rather than all record-type-specific choices an administrator
may currently offer. Salesforce describes the distinction in its
[Object Metadata reference](https://developer.salesforce.com/docs/platform/graphql/guide/query-objectinfo.html).

## Snapshot command

```bash
uv run aisc_salesforce snapshot
uv run aisc_salesforce snapshot --output-dir /secure/snapshot-location
```

## Application snapshot command

Generate an on-demand, read-only cross-tab of qualifying Application Cases:

```bash
uv run aisc_salesforce application-snapshot
uv run aisc_salesforce application-snapshot \
  --output-dir /secure/application-reports
```

The command uses Salesforce's
[REST Query resource](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/resources_query.htm)
and [SOQL relationship fields](https://developer.salesforce.com/docs/atlas.en-us.soql_sosl.meta/soql_sosl/sforce_api_calls_soql_relationships.htm)
to read Case and parent Account values. It separately reads the minimal Audit
fields and follows every Salesforce query page. It does not write to
Salesforce.

### Output

The default CSV path is:

```text
application_snapshots/YYYY-MM-DDTHH-MM-SSZ/application_snapshot.csv
```

A second run in the same UTC second receives a numeric folder suffix, starting
with `-01`. Publication is atomic: the complete CSV is written in a temporary
directory before the report folder appears. A failed run removes the temporary
data.

The CSV columns are:

```text
application_stage,domestic_regular,domestic_expedited,international_regular
```

These six rows always appear first and in order, even when every count is zero:

1. Initial Review
2. Eligibility Review
3. Doc Audit
4. Awaiting Audit Assignment
5. Awaiting Audit
6. Awaiting CRG Decision

Unexpected stages remain separate, are appended alphabetically, and produce
one non-fatal console warning listing their labels and counts.

### Filters and classifications

A Case is counted once when all of these rules pass:

- The parent Account's `Cert_Certification_Status__c` is exactly `Initials`.
- `Cert_Stage__c` is not `Cancel`; null remains eligible.
- `Cert_Is_this_a_scope_change__c` is not `Yes`; null remains eligible.
- The Case record type is Fabricator Application, Erector Application, or
  International Application.

For each qualifying Case, Canceled or Withdrawn Audits are invalid. An Audit
must also have been created on or after its Case's `CreatedDate`. Additional,
Appeal, SA-NYC, and Preassessment Audit types are also invalid. Null Audit
status and type values remain valid. The newest matching valid Audit is selected
by `Cert_Audit_Date__c`, falling back to the date portion of `CreatedDate`. A
tie is resolved by the full `CreatedDate`, then Salesforce Audit ID.

`BillingCountry == "United States"` means Domestic. Only the Boolean value
`true` makes a Domestic Case Expedited; other Domestic Cases are Regular.
Every other or missing country is International Regular.

Stage decisions use this order:

1. A Doc Audit Case with a related Audit date becomes `Awaiting Audit` when the
   date is today or in the future, or `Awaiting CRG Decision` when it is past.
2. A null or New Application Case stage becomes `Initial Review`.
3. Audit status Pending Acceptance becomes `Awaiting Audit Assignment`.
4. An ordinary Case stage has underscores replaced with spaces.
5. For Pending AuditAssignment, no related Audit, Reschedule in Progress, or a
   null audit date becomes `Awaiting Audit Assignment`.
6. A Pending AuditAssignment Audit date today or in the future becomes
   `Awaiting Audit`.
7. A past Pending AuditAssignment Audit date becomes `Awaiting CRG Decision`.

Today is evaluated as the current `America/Chicago` calendar date. A malformed
Salesforce date stops the report with a readable error rather than assigning
the wrong stage.

Successful runs print the output path and qualifying Case count. Authentication,
Salesforce, data-validation, and file errors print
`Application snapshot failed: ...` and return exit code `1`.

!!! warning

    Application snapshots may contain sensitive operational counts. The
    default directory is ignored by Git. Keep custom output locations
    access-controlled, and do not commit or share these reports through
    unapproved channels.

## Profile Update command

Run one daily processing pass:

```bash
uv run aisc_salesforce profile-updates
```

The command evaluates eligible audits and submissions whose `Status__c` is
`New`. It creates or reuses Account-scoped Profile Update Cases, matches a
submission Contact by email when the match is unambiguous, and posts missing
Chatter messages.

Example output:

```text
Profile updates complete:
created: 2
reused: 1
skipped: 4
failed: 0
```

Exit code `0` means the run completed without failures. Exit code `1` means
configuration, Salesforce communication, or one or more records failed.

## Consolidate iMIS contacts command

Put the downloaded CSV files in `imis_contactbasic/` and run:

```bash
uv run aisc_salesforce consolidate-imis-contacts \
  --directory imis_contactbasic
```

`--directory` defaults to `imis_contactbasic`, so it may be omitted. A custom
secure directory is also supported.

### Dated files and outputs

Dates come from filenames, not modification times. Fresh exports must be named
`Full_CSContactBasic_YYMMDD.csv`, where `YY` means `20YY`. Previous combined
tables must be named `Combined_CSContactBasic_YYYYMMDD.csv`.

An initial run writes only the combined table. Once a combined table exists,
the fresh export date must be later, and the command writes all three files:

```text
Combined_CSContactBasic_YYYYMMDD.csv
Changed_CSContactBasic_YYYYMMDD.csv
New_CSContactBasic_YYYYMMDD.csv
```

The changed and new files always receive headers, even when they contain no
rows. An existing target stops the run instead of being overwritten. A failed
write removes temporary files and any output started by that run.

### CSV and merge rules

Every selected input must contain exactly these columns, in any order:

```text
City, Company, Full Name, iMIS Id, Member Type, State Province,
Company ID, Company Member Type, Date Added, Email, Is Company, Is Member,
Join Date, Major Key, Member Status, Status, Category, Last Updated,
Full Address, Country, Website
```

CSV values are kept as text. Leading zeroes in `iMIS Id`, `Company ID`, and
`Major Key` are not removed. IDs and other fields use exact comparison;
capitalization, spaces, and blank values are meaningful differences.

The combined file keeps the previous row order. A matching fresh row replaces
the previous row in place, contacts found only in the previous table remain,
and new contacts are appended in fresh-export order. Changed and new reports
also follow fresh-export order and contain complete fresh rows.

Blank or whitespace-only IDs are skipped, and the warning shows the filename
and CSV row number. When a selected input repeats a nonblank ID, every row with
that ID is omitted from that input. A duplicated fresh ID leaves a valid row
from the previous combined table unchanged.

!!! warning

    iMIS input and output files contain personal data. The usual directory and
    filename patterns are ignored by Git, including in custom directories.
    Keep these files uncommitted and in an access-controlled location, and
    share them only through approved secure channels.

### Case subject grammar and duplicate handling

New received Cases and Expected Cases converted after a submission use:

```text
AISC Profile Update for {Account Name} - {Profile Update} {YY-MM-DD}
```

Example:

```text
AISC Profile Update for Acme Steel - PU-100 26-07-20
```

Every combined Case stores an ordered Profile Update/date pair for each
submission:

```text
AISC Profile Update for Acme Steel - PU-099 26-07-01 / PU-100 26-07-15
```

The date beside each identifier is that submission's received date. Matching
trims whitespace, ignores letter case, and compares the complete identifier.
For example, `pu-100` matches `PU-100`, but `PU-10` and `PU-1000` do not.

The daily automation and staging workflow recognize both the AISC grammar and
legacy subjects containing `Profile Update Received`. When an Account-scoped
AISC Case already contains the exact identifier, the daily automation
immediately reports the submission as skipped. It does not query or post
Chatter for that duplicate.

When recurring automation reuses a legacy received Case, it keeps the old
subject format. A missing Chatter summary can therefore still be added on a
retry without silently normalizing legacy Cases outside the correction window.

## Scheduling and retries

Use cron, Windows Task Scheduler, or another external scheduler to call the
command daily. For example:

```cron
0 2 * * * cd /path/to/aisc-sf && uv run aisc_salesforce profile-updates
```

Retries are safe: the service uses the Account, Case Subject, Profile Update
name, and exact Chatter text to recognize completed work. If a run stops after
one Chatter post, the next run posts only the missing message.

The project does not install or manage a scheduler itself.

## Rename Profile Update Cases command

Use the one-time command to correct recently created legacy Case subjects. The
safe default is preview mode:

```bash
uv run aisc_salesforce rename-profile-update-cases
```

Preview mode authenticates and queries Salesforce but does not update a Case.
It prints each proposal in this form:

```text
00012345: would update: 2026-07-15: Profile Update Received for Acme Steel - PU-100 -> AISC Profile Update for Acme Steel - PU-100 26-07-15
```

After reviewing the preview, `--apply` is the only option that enables writes:

```bash
uv run aisc_salesforce rename-profile-update-cases --apply
```

Apply mode sends a PATCH containing only `Subject`; omitted Case fields are not
changed. See Salesforce's
[record-update guidance](https://developer.salesforce.com/docs/marketing/marketing-cloud-growth/guide/mc-manage-objects-update-rest.html).

### Correction window and parsing rules

The query covers seven `America/Chicago` calendar dates: today plus the six
preceding dates. The lower boundary is local midnight six dates ago, and the
exclusive upper boundary is the local midnight after today. Both boundaries
are converted to UTC for the SOQL `CreatedDate` comparison because Salesforce
stores date/time values in GMT. See Salesforce's
[date/time guidance](https://developer.salesforce.com/docs/atlas.en-us.formula_date_time_tipsheet.meta/formula_date_time_tipsheet).

These dated legacy prefixes are supported:

- `YYYY-MM-DD: Profile Update Received ...`
- `YYYY-MM-DD - Profile Update Received ...`
- `YY-MM-DD - Profile Update Received ...`

The embedded received date is required. It is used instead of the Case
`CreatedDate`, and a subject with several identifiers gives that same embedded
date to every identifier:

```text
26-07-15 - Profile Update Received for Acme Steel - PU-100 / PU-101
```

becomes:

```text
AISC Profile Update for Acme Steel - PU-100 26-07-15 / PU-101 26-07-15
```

An unparseable date, a missing date, or a corrected subject over Salesforce's
255-character Subject limit is skipped with a reason. The command does not
guess from `CreatedDate`.

### Output, failures, and reruns

Every Case receives an individual `would update`, `updated`, `skipped`, or
`failed` line. The final totals are:

```text
Rename profile update Cases complete:
matched: 3
would update: 2
skipped: 1
failed: 0
```

Apply mode prints `updated` instead of `would update`. It continues processing
after an individual Salesforce update fails so the remaining safe corrections
can finish. The command exits with code `1` if any update failed and `0`
otherwise.

Reruns are safe. The Salesforce query targets legacy `Profile Update Received`
subjects, while a successfully corrected subject begins with `AISC Profile
Update`; it will not be changed again. Legacy Cases outside the seven-date
window remain unchanged.

## Stage Profile Updates command

Create a read-only CSV of every New profile-change submission:

```bash
uv run aisc_salesforce stage-profile-updates
uv run aisc_salesforce stage-profile-updates \
  --output-dir /secure/staged-profile-updates
```

The default file location is:

```text
staged_profile_updates/YYYY-MM-DDTHH-MM-SSZ/profile_updates.csv
```

If two runs finish during the same UTC second, the later directory receives a
numeric suffix such as `-01`. Every run is independent and repeatable. The
command only queries Salesforce; it never creates or updates a record.

Example output:

```text
Staged profile updates complete: staged_profile_updates/2026-07-17T12-30-00Z/profile_updates.csv
staged rows: 7
warnings: 2
```

The warning count is the number of newline-separated warnings across all rows.
Exit code `0` means Salesforce was queried and the complete CSV was published.
Exit code `1` means configuration, Salesforce communication, or file writing
failed. A failed write leaves no partially published snapshot.

### Merge rules

Rows are grouped by Account ID plus a submitter-email comparison key. Email
values are trimmed and lowercased, and dots are removed from the local-part for
comparison on every domain. For example, `a.smith@example.com` and
`asmith@example.com` share a key. Submissions with different Account IDs or
comparison keys stay separate. Each submission with a blank email also stays
separate and receives a warning. A nonblank value that fails the basic email
structure check is preserved and warned about instead of being silently fixed.

Within a group, submissions are ordered by `CreatedDate` and `Id`. Later
nonblank values replace earlier values, while blank later values do not erase
earlier information. Comments and Other Personnel Notes are the exception:
every nonblank value is preserved, including repeated identical text, and
values are joined with `\n` in submission order. Source submission IDs and
names are retained as JSON arrays.

When at least one revised address component is present, the output contains all
five components. Missing submitted components are filled from the Account
billing address where possible.

### Shared Account-role mappings

`src/aisc_salesforce/account_roles.py` owns the submitted Salesforce fields and
Account lookup mappings shared by staging, interactive processing, and the
review queue. The Quality/QC role deliberately uses the
`Cert_Marketing_Contact__c` Account lookup field. The field's marketing name is
part of the existing Salesforce schema and does not mean the role should use a
different lookup.

### Submitted contact normalization

Staging canonicalizes only values submitted for the submitter and the five
Contact roles. It does this before role resolution, CSV projection, and
`contact_resolutions` JSON construction:

- Every email is trimmed and lowercased, including an invalid address that must
  remain available for human review.
- Submitter names, role first and last names, and role titles are trimmed and
  changed to Proper Case. Punctuation is preserved.
- Case-insensitive whole-token exceptions preserve these spellings: `CEO`,
  `CFO`, `COO`, `CTO`, `VP`, `HR`, `QA`, `QC`, `QMS`, `AISC`, `IT`, `ISO`, `AWS`,
  `API`, `AI`, `PhD`, `MBA`, `iOS`, `macOS`, `McDonald`, `MacKenzie`, and
  `O'Connor`.
- A recognizable ten-digit North American phone, optionally prefixed by `1` or
  `+1`, becomes `###.###.####`.
- An extension introduced by `x`, `ext`, `ext.`, `extension`, or `#` becomes
  ` x#`. Every extension digit is kept, including leading zeroes.
- Blank phones stay blank. International, malformed, partial, and otherwise
  unrecognized phones keep their submitted text after outer whitespace is
  trimmed.

Examples:

| Submitted | Staged |
|---|---|
| `ALEX.SMITH@Example.COM` | `alex.smith@example.com` |
| `jane mcdonald` | `Jane McDonald` |
| `chief qa officer` | `Chief QA Officer` |
| `+1 (312) 555-0100 ext. 123` | `312.555.0100 x123` |
| `+44 20 7946 0958` | `+44 20 7946 0958` |

Existing Salesforce Contact values are not normalized. Values copied from an
existing Contact to provide review context retain their Salesforce spelling
and formatting, and non-contact submission fields are unchanged. The staging
command is still query-only. During interactive processing, fresh submission
records are loaded as before and these same rules are applied to their contact
values before comparisons or proposals.

To add another capitalization exception, edit
`CONTACT_CASE_EXCEPTIONS` in
`src/aisc_salesforce/contact_normalization.py`. Add the desired canonical
spelling to that tuple and add a parameterized test case if the existing
all-exceptions test does not already cover the scenario. Do not add another
branch to the Proper Case algorithm: the lookup is automatically
case-insensitive and whole-token-only. Then run the focused normalization
tests and the full verification commands.

### CSV contract

Each row contains these groups of columns:

| Group | Columns |
|---|---|
| Source and dates | `source_submission_ids`, `source_submission_names`, `earliest_submission_date`, `latest_submission_date` |
| Account and submitter | `account_id`, `account_name`, `certification_id`, `submitter_name`, `submitter_email`, `submitter_phone` |
| Case context | `case_id`, `case_number`, `case_status`, `case_match_status`, `prior_activity_references` JSON list |
| Contact resolution | `contact_resolutions` JSON list |
| Notes and review | `comments`, `personnel_notes`, `has_contact_derived_values`, `has_no_update_content`, `has_warnings`, `warnings` |
| Key Data | `effective_date`, revised company name/owner, five revised address columns, and `key_answers` |
| Contact roles | Columns prefixed with `certification_`, `principal_`, `accounting_`, `quality_`, and `new_york_` |

`key_answers` contains eight labeled lines when a group has Key Data. Each role
contains its submitted name, title, email, and phone fields, followed by:

- `resolution_action`
- `salesforce_contact_id`
- `resolution_source`
- `source_submission_id`
- `source_role`
- `warning`

New York has no submitted title field. Completely blank roles have completely
blank role columns. The prefixed resolution columns are readable projections
kept for compatibility; `contact_resolutions` is the authoritative contract.

### Required participant Profile rules

`aisc_salesforce.required_profile_rules` is a pure Python rule engine for
selecting a participant Profile from Account-role assignments. It accepts an
`AccountRole`, Salesforce Account ID, and certification status for each
assignment, then returns a Profile decision and its ordered, deduplicated
causes. It performs no Salesforce reads or writes. A later orchestration layer
is responsible for obtaining assignments and using the result. Only
`Certified` and `Initials` assignments are eligible. That layer can also pass
the IDs of Accounts in multi-account Families (Accounts with a parent, child,
or sibling). A qualifying assignment on one of those IDs has first priority and
selects Participant RAS. A standalone root Account does not qualify for this
override. When no qualifying assignment is in the supplied ID set, the usual
single-role, New York, and multiple-role rules apply unchanged.

Each JSON entry represents one distinct comparison key shared by any submitter
and role occurrences. It contains:

- `classification`: `use_existing`, `create_new`, `likely_typo`, or
  `ambiguous`
- `normalized_email` and `comparison_key`
- `sources`, identifying submitter and role occurrences
- `candidates` and `selected_contact`
- `reason`, `confidence`, and `warnings`
- `submitted`, containing the best available Contact details

Resolution actions are `update_contact` for an exact match or a title/phone
update, `change_email` for a new email applied to the Account's current role
contact, `use_submitted_contact` when another submitted role is the first exact
match, and `create_contact` when an unmatched submitted name describes a new
Contact. A missing Contact ID or a `create_contact` action always comes with a
warning for human review. Resolution sources show whether the match came from
another submitted role, submitted data for a new Contact, an Account Contact, a
sibling Account Contact, or the Account's current role lookup.

These prefixed actions explain the read-only staging recommendation; they do
not override fresh identity resolution during processing. In particular,
`change_email` does not cause a valid submitted email to be written onto the
current role Contact automatically. The processing command resolves that email
as its own Contact identity first.

The target Account's **family accounts** are the target itself, its parent, and
its **sibling accounts** with the same parent. A root Account without a parent
has a family containing only itself. Contacts from this family are preferred.
One exact normalized or dot-insensitive family match can resolve directly.
Ties, matches mixed with external Accounts, generic/shared mailbox names,
differing domains, and weaker name-only evidence require operator review.

Name-derived local-parts use normalized first and last names and initials. A
trailing square-bracket suffix on a Salesforce Contact name is removed only
for comparison; the original Salesforce text stays in displays and audit
events. The only likely-typo rule is one insertion, deletion, substitution, or
adjacent transposition. Even that classification requires confirmation; the
resolver does not guess nicknames or use a broad fuzzy-score threshold.
Repeated occurrences with the same comparison key resolve once. When a Contact
is resolved, missing title and phone values are filled where possible.
`has_contact_derived_values` is `true` only when a nonblank title or phone was
actually copied from another submitted role or a Salesforce Contact.

`has_no_update_content` is `true` when the grouped raw submissions contain no
Key Data fields, role fields, Comments, or Other Personnel Notes. Account,
Case, certification, and submitter metadata do not count. `Type__c` also does
not count, so an exact `"Key Data"` submission with no update fields can have
both `has_key_updates=true` and `has_no_update_content=true`. Values filled from
an Account, another role, or a Salesforce Contact cannot turn the empty-content
flag off.

!!! warning

    The interactive processor validates the complete current CSV contract,
    including `contact_resolutions`, `has_contact_derived_values`, and
    `has_no_update_content`. It also inspects `has_warnings` and `warnings`
    before acting on a row. Generate a fresh CSV for each processing run rather
    than migrating an older staged file.

Case preparation adds `case_id`, `case_number`, `case_status`, and
`case_match_status`. A row is processable only when its match status is
`matched`. Missing and ambiguous Case matches are blocking warnings and are
never guessed. Case subjects are parsed with the same exact identifier rules as
daily automation, so the `YY-MM-DD` values in AISC subjects are not mistaken
for part of a Profile Update identifier.

Key Update metadata is explicit:

- `has_key_updates` is `true` only when at least one source has the exact
  Salesforce `Type__c` value `"Key Data"`. Case differences, surrounding
  spaces, `None`, and populated Key Data fields alone do not set it.
- `earliest_key_update_date` is the oldest such source `CreatedDate`.

Populated Key Data fields remain visible in their normal CSV columns and in
`key_answers` even when they do not set `has_key_updates`.

## Process Profile Updates command

Run staging, Case preparation, and interactive review as one convenience
command:

```bash
uv run aisc_salesforce process-profile-updates
uv run aisc_salesforce process-profile-updates \
  --output-dir /secure/staged-profile-updates
```

The same phases can be run separately:

```bash
uv run aisc_salesforce process-profile-updates stage \
  --output-dir /secure/staged-profile-updates
uv run aisc_salesforce process-profile-updates prepare \
  2026-08-04T15-30-00Z \
  --output-dir /secure/staged-profile-updates
uv run aisc_salesforce process-profile-updates review \
  2026-08-04T15-30-00Z \
  --output-dir /secure/staged-profile-updates
```

### Accessible terminal colors

On supported interactive terminals, only review semantics are colored:

| Meaning | Terminal color | Non-color label or context |
|---|---|---|
| Submitted values | Bright green | `Proposed value`, submission context, and existing field labels |
| Script-supplemented values or notes | Bright yellow | `Note` |
| Warnings and validation feedback | Bright red | `Warnings` and the complete feedback text |
| Response-email body | Bright blue | `Response email for ...` heading |

Headings, current Salesforce values, progress messages, and record counts are
neutral. The labels remain the primary meaning, so the workflow is readable
without color.

Use `--no-color` on the combined command or before/after any nested operation:

```bash
uv run aisc_salesforce process-profile-updates --no-color stage
uv run aisc_salesforce process-profile-updates prepare SESSION_ID --no-color
uv run aisc_salesforce process-profile-updates --no-color review SESSION_ID
```

Setting the standard `NO_COLOR` environment variable has the same effect.
Redirection, unsupported terminals, and custom `output_fn` callbacks fall back
to plain text automatically. The adapter uses Rich's
[Console](https://rich.readthedocs.io/en/stable/console.html) and
[theme styles](https://rich.readthedocs.io/en/stable/style.html) without parsing
markup, so brackets and other characters in submitted values stay literal.
Terminal styling is never written to `profile_updates.csv`,
`review_audit.jsonl`, `review_queue.json`, or `response_emails.txt`.

`stage` reads New submissions and publishes both `profile_updates.csv` and
`review_queue.json` through one temporary directory and one final rename. The
printed UTC timestamp folder name is the session ID. The final rename claims an
ID, so same-second collisions retry with `-01`, `-02`, and later suffixes
without replacing an existing published session. Publication finishes before
any Salesforce write, allowing a reviewer or TUI to inspect a stable queue.

File contents are flushed with `flush()` and `os.fsync()` on every supported
platform before they are published. On POSIX, the temporary and output
directories are also synced to make the rename metadata durable. Windows skips
that unsupported directory-handle operation while keeping the file flushes and
atomic rename. This assumes the temporary and output directories share a
filesystem: POSIX refuses to replace a non-empty destination directory, and
Windows refuses an existing destination.

`prepare SESSION_ID` loads and validates that saved session, then creates or
reuses Cases only for captured submissions that already have Accounts. It does
not repair blank Accounts. `review SESSION_ID` loads the saved CSV and queue,
repairs blank Accounts only among captured submissions, prepares newly possible
Cases, and refreshes the same session before interactive review. The Case step
includes a repaired submission only after a scoped Salesforce refresh confirms
that it now has an Account. A submission that arrives after `stage` is never
silently added.

The session ID must be the exact direct-child folder name printed by `stage`.
Absolute paths, separators, `..`, missing sessions, symlinked sessions or
artifacts, malformed CSVs, unsupported queue schemas, and mismatched CSV/queue
submission IDs fail before writes.

For blank Submission Accounts, review looks up Accounts using the submitted
Certification ID. One matching Account is linked automatically. Multiple
matches present typed choices with the Account Name and Certification ID; enter
the displayed number, or enter `P` for a different Certification ID. When the
submitted ID is missing or has no match, review requests a Certification ID to
find the Salesforce Account. Setup outcomes and source-submission references
survive the following refresh. A required setup failure marks the affected queue
item `failed` and stops before change review.

Sessions are safely resumable. Completed and blocked work stays durable;
interrupted, failed, `in_progress`, and `stopped_early` work resets to pending
only on explicit `prepare` or `review`. Completed Case batches are skipped,
unfinished batches begin with fresh Salesforce reads, audit entries append to
`review_audit.jsonl`, and an identical response-email block is not appended
twice. Reviewing a fully completed session succeeds as a no-op.

Progress messages appear around authentication, read-only preflight, queue and
CSV publication, Submission Account resolution, Case preparation, staging
refresh, and review startup.
Progress output belongs to the CLI workflow, while reviewer interactions cross
a separate typed `ReviewUI` boundary. The Account selection, including its
first-choice default, is represented by the same `ChoiceQuestion` used by other
review actions. The CLI adapter sends both kinds of output to the configured
terminal output function. Visual separators mark stages, Cases, staged rows,
individual Contact reviews, and response-email sections.

For another front end, implement `ReviewUI.display(event)` and
`ReviewUI.ask(question)`, then pass that object to
`InteractiveProfileUpdateProcessor`. Events include headings, notices, scalar
and mapping comparisons, Contact cards, validation feedback, and generated
response emails. A `ReviewQueueSnapshot` event carries the complete immutable
manifest; the CLI adapter turns it into a concise batch/pending/next summary.
Questions are deliberately split into choice, free-text, and
acknowledgement shapes, with matching answer dataclasses. A choice question
lists only allowed `ReviewChoice` values; for example, an incomplete Contact
does not include automatic creation. The processor still owns email and Contact
ID validation, Salesforce refreshes and writes, audit entries, and safe-stop or
interruption handling.

### Stable review queue contract

The JSON contract starts with `schema_version: 1` and has this hierarchy:

```text
manifest
└── batches[]
    └── rows[]
        └── changes[]
```

Every batch, row, and change includes an `id`, readable `label`, `status`,
`warnings`, and `blockers`. Changes also include their phase, source submission
and row IDs, Salesforce object/record/field reference, current value, proposed
value, and optional completed `outcome`. Batch and row references retain the
source Profile Updates, target Account and Case, affected direct child Accounts,
and related prior Case activity.
Only submissions captured as New by `stage` become work. Later refreshes query
those exact IDs so completed items remain auditable and newer submissions do not
enter the session. Older related records are context references, not queue
items.

IDs are deterministic UUID5 values. Their canonical input uses the item type,
Salesforce object context, sorted source submission IDs, target record or stable
target context, and field. Display labels are deliberately excluded. The JSON
also omits a run timestamp because the timestamped parent folder already records
run time. Rebuilding from unchanged input therefore produces the same IDs and
bytes.

Ordering uses these tie-breakers:

1. Batches with a Key Update strictly older than seven days come first.
2. Batches then use earliest submission, Account ID, Case ID, and stable ID.
3. Rows use earliest submission, sorted source submission IDs, and stable ID.
4. Changes use setup, Contact, Account, then role-link phase, followed by target
   identity, field, and stable ID.

The allowed statuses are `pending`, `in_progress`, `blocked`, `completed`,
`failed`, and `stopped_early`. Warnings are informational. A blocker prevents a
change from being selected automatically. Parent row and batch statuses are
recomputed after every child transition. `default_next_item_id` is always the
first unblocked change whose status is `pending`; it advances after completion
or becomes `null` when no reviewable pending change remains. Empty queues also
use `null`.

The file is replaced atomically, not edited in place. A snapshot is persisted
before and after each queue transition or Salesforce mutation and immediately
before each reviewer question. On a failure, interruption, or deliberate safe
stop, the last valid JSON remains readable with the affected item marked
`failed` or `stopped_early`. Consumers should accept fields added in later
schema-compatible releases and reject unsupported `schema_version` values.

### What the reviewer sees

At the beginning of each Case batch, the command fetches current Profile Update
data and displays Comments, Other Personnel notes, Key Update answers,
effective dates, and warnings once. Account History is limited to each source
submission day in `America/Chicago`; a local calendar day shared by several
rows is queried only once. Each history field, previous value, new value, and
timestamp is displayed before proposals begin. The fetched submissions are
then reused throughout that Case review.

Account name, company owner, and each billing-address component are separate
proposals. Effective date and the Key Update answers remain context unless
they map to one of those real Account fields.

Before every staged CSV row, a heading shows its Account, submitter, and source
Profile Update names. When the matching CSV flag is `true`, the heading also
shows one or both of these lines:

- `Note: contact details were supplemented from available contact information.`
- `Note: this combined profile update has no submitted update content.`

The next prompt is a checkpoint:

| Checkpoint answer | Result |
|---|---|
| Enter, `C`, or `Continue` | Review the row |
| `Q` or `Quit` | Stop successfully before reviewing the row |

Checkpoint answers are case-insensitive. Continue is the default only at this
checkpoint. For a real change, choose an explicit decision:

| Shortcut | Complete phrase | Result |
|---|---|---|
| `A` | `apply automatically` | Update Salesforce with the proposed value |
| `M` | `make manually` | Defer the field, then refetch and verify it during manual follow-up |
| `N` | `will not be made` | Record the rejection without a Salesforce data change |

Decision shortcuts and complete phrases are case-insensitive. The JSON Lines
audit always stores the complete phrase.

An already-current value is an audited no-op. It does not prompt and does not
appear in response-email text.

All row checkpoints are shown before the first Salesforce write. Processing
then prints and completes these ordered phases:

1. `Contact Updates`
2. `Manual Contact Follow-up`
3. `Account Updates`
4. `Role Links`

### Contact Updates

The processor reloads every source Profile Update and collects each nonblank
Contact value explicitly submitted for the submitter and every role. The
flattened staging `submitted` dictionary, staging fallbacks, and existing
Salesforce field values are not proposals. Blank values neither erase a field
nor participate in a conflict.

When a proposal contains a valid submitted email, that email is the starting
identity and the Contact's role does not choose the record. The processor
queries fresh Salesforce Contacts, then applies the exact, ambiguous,
likely-typo, and new-Contact family-aware rules described above. It does not
automatically attach the proposal to the Contact currently holding the role.
For example, submitted details for `tim@wsfabrication.com` are compared with
Tim's Contact even if Ray is the current principal. A partial proposal with no
email can still use a staged Contact ID or the fresh current Account-role
lookup. Ambiguous candidates are shown together; the reviewer may select one,
create a Contact, or ignore that unresolved entry.

Proposals sharing one email comparison key are grouped across rows, roles, and
submissions. Different emails remain separate Contact work items even when they
were submitted for the same role. If identity review later selects the same
Salesforce Contact ID for two items, they are merged then. Identical normalized
values are stored once with all their sources. Distinct values for one resolved
Contact conflict. For each conflict, the command displays numbered candidate
values beside their submission/role sources and prompts:

```text
Choose reconciled Title [1-2/current]:
```

The reviewer must choose one candidate or `current`. Choosing `current` keeps
the current Salesforce value and removes that field from the final write. All
identity and field-conflict choices finish before any Contact is written.

A final heading identifies each Contact by person and email, independent of
role. Its table shows `Field`, `Current Salesforce`, `Reconciled`, and
`Sources`. The processor then presents First Name, Last Name, Title, Email, and
Phone as independent decisions. An already-current submitted field is an
audited no-op and does not prompt. A changed field displays a readable scalar
comparison:

```text
Contact Title: Alex Smith <alex@example.com>
Current Salesforce value: Old Title
Proposed value: Owner
```

A reviewer can therefore approve, defer, or reject different fields on the same
Contact:

- `A` adds that field to one grouped automatic Contact payload.
- `M` omits that field from the automatic payload and defers it to manual
  follow-up.
- `N` omits that field from writes and records the rejection.

All decisions are collected before the first Contact write. Approved fields are
then combined, so each resolved Contact has at most one automatic create or
update call in a run. Rejected and manual fields are absent from that payload.
A new Contact requires an approved Last Name for automatic creation. If Last
Name is manual, the reviewer uses the manual-creation path and the processor
verifies the other approved values; if safe creation is impossible, processing
fails without creating a partial Contact. If every new-Contact field is
rejected, no Contact or role link is created. When a new submitter Contact is
created, its ID is assigned to `Case.ContactId`; matching an existing submitter
alone does not change that Case lookup.

After every automatic Contact has finished, a visibly separated `Manual Contact
Follow-up` heading reminds the reviewer that external Salesforce edits begin.
Manual fields are processed one at a time. After each acknowledgement, the
processor refetches that field. A matching value is verified immediately. When
Salesforce contains a different value, the processor displays both the
submitted proposal and fresh Salesforce value, then asks whether the Salesforce
value should be accepted. Enter defaults to `yes`; acceptance makes the fresh
Salesforce value authoritative. Declining records a failed verification, keeps
the Case and source submissions open, and leaves the batch retryable. An
interruption or Salesforce read failure has the same safe open-batch behavior.
Every completed Contact is refreshed again before role-link decisions and
response generation, so downstream names, titles, emails, and phones reflect
the actual final Salesforce record.

### Account Updates and Role Links

Ordinary company-name, owner, and billing-address changes run only after the
Contact phase is complete. The final role-link phase uses the preserved mapping
from each submission/role source to its resolved Contact ID, so it still works
when phase one changed the Contact email or created a Contact shared by several
roles. This phase may update Account role lookup fields only. It performs no
Contact create or update. Contact identity and field updates therefore finish
before role assignment can influence an Account lookup.

At the start of every Case batch, the submitted Account and its direct children
are fetched again. An Account with at least one direct child is treated as a
Parent Account. Only that first child level is inspected, so grandchildren are
never selected. Direct children are active only when
`Cert_Certification_Status__c` is exactly `Certified` or `Initials`; `Dropped`,
`Suspended`, blank, and all other statuses are excluded.

Before the Case, Contacts, Accounts, role links, or source submissions can be
changed, active children are compared for the ordinary Account fields and
Account-role lookups actually submitted in this Case batch. Outer whitespace is
ignored, null and blank are equal, and other text remains case-sensitive.
Differences in unsubmitted fields and values on inactive children do not block
processing. If relevant values agree, Contact reconciliation runs once. A newly
created Contact keeps the Parent Account as `AccountId`, while the ordinary
Account proposal and role-link review runs once for each active child.

A relevant conflict produces a typed summary with the field label, requested
value, and every active child's current name, ID, and value. A Parent with no
active direct children produces a typed notice listing the direct children's
statuses. The reviewer acknowledges either message; the processor then writes a
`deferred manual follow-up` audit event, marks the complete Case batch blocked,
leaves every included Profile Update and the Case open, performs no Salesforce
write for that batch, and advances to the next Case. An interruption during the
acknowledgement also occurs before any Salesforce write. On a later run, staging
and preflight refetch the hierarchy and values, so manually reconciled work can
be retried without carrying stale child routing.

Account-role proposals use friendly Contact names and emails for current and
proposed values. Salesforce IDs remain available internally for record writes
and audit entries, but are not exposed in decision prompts.

### Duplicate-create recovery

Salesforce [duplicate rules](https://help.salesforce.com/s/articleView?id=sales.duplicate_rules_overview.htm&language=en_US&type=5)
can block an API create with `DUPLICATES_DETECTED`. The error code and
Salesforce message are retained.
Instead of ending the Case immediately, the reviewer chooses one of four
actions:

1. Create the Contact manually.
2. Update an existing Contact manually.
3. Use a Contact with another email.
4. Ignore this entry.

Manual create and update choices are verified by querying the normalized
submitted email. The alternate-email choice queries the entered email.
Multiple matches show candidates and require an explicit selection. The
verified Contact can continue to later role assignment. Ignore affects only
the shared email key. Other Salesforce errors are still fatal and leave the
Case and source submissions retryable.

### Output and finalization

Each timestamped run folder contains:

| File | Purpose |
|---|---|
| `profile_updates.csv` | Exact published staging input used by the reviewer |
| `review_queue.json` | Atomic, versioned navigation and status snapshot |
| `review_audit.jsonl` | One immediately flushed JSON object per decision/result |
| `response_emails.txt` | Generated response text grouped by submitter email |

Automatically applied and manually verified Account-information changes appear
in response text using `ITEM: NEW INFORMATION` followed by
`Replaces OLD INFORMATION`. Each submitted Contact role is instead summarized
as one line containing its name, title, email, and phone. A role that required
no change ends with `- no change`. A following `Replaces OLD INFORMATION` line
is included only when prior role information was actually replaced. The prior
role Contact's name, title, email, and phone are copied into memory at the
beginning of the Case batch, after routing and row checkpoints but before the
first Salesforce write. Response-email replacement details therefore remain
the values from the beginning of the batch even if that Contact is updated
earlier in the same batch. Parent-routed Accounts each use their own role
Contact snapshot. These temporary snapshots do not survive a process restart
and are not written to staging CSVs, review queues, audit events, or Salesforce.
The Account paragraph begins:

> Thank you for updating your information with AISC. The changes are summarized
> below. An updated Participant Portal login will be sent by a separate email,
> if needed. Unless otherwise noted, previous contacts will remain in the
> {ACCOUNT NAME} contact list.

The command generates and prints email text; it does not send email. The
reviewer sends it through the approved email system and confirms whether it was
sent.

After a resolved batch, source Profile Updates are set to `Closed`. Answering
`yes` to every generated response-email confirmation means the email was sent,
so the Case is also set to `Closed`. If a response is not sent, the source
records are closed and the Case stays `Pending`.

Contact audit objects include classification, comparison key, candidates,
selected Contact, reason, confidence, and warnings. Every field-conflict
selection is an explicit event that records the candidate values and their
sources. The final Contact outcome is stored per Salesforce field, even though
approved fields share one automatic write. Each field result records its full
decision phrase, submitted `proposed_value`, and authoritative `final_value`.
For ordinary applied, rejected, and no-op results this remains backward
compatible with the proposal or original value; for an accepted manual
override, `final_value` preserves the fresh Salesforce value separately from
the submission. Contact queue entries transition independently while queue
schema version `1` and its stable field IDs remain unchanged. Operator identity
selections, duplicate recovery, ignored entries, Case Contact assignment, and
later Account-role assignments are separate, immediately flushed events.

On interruption, an unrelated Salesforce failure, a failed manual read, or a
differing manual value that the reviewer declines, the audit is flushed,
unfinalized source Profile Updates stay open, the Case is kept Pending, and the
command exits nonzero. Retry with `review SESSION_ID` to reload the saved
artifacts and refresh only the captured records. Values applied before the
interruption are fetched again and recorded as no-ops, so they are not applied
or emailed twice.

`Q` or `Quit` is different from an error or keyboard interruption. It writes a
`stopped early` audit event, keeps the current Case Pending, leaves that Case's
Profile Updates open, skips response generation for the unfinished row, and
returns exit code `0`. Completed earlier batches stay completed. Later batches
remain untouched. The final CLI summary says that review stopped at the
reviewer's request and reports completed and pending batch counts.

!!! warning

    All four artifacts can contain sensitive personal and Salesforce data.
    Git ignores their default location and generated filenames. Store custom
    output directories securely, limit access, and do not attach these files
    to public issues or commits.
