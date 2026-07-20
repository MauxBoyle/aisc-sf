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
| `SF_LOGIN_URL` | No | Salesforce org URL or complete OAuth token URL |

The CLI loads `.env` without replacing environment variables that are already
set.

## Snapshot command

```bash
uv run aisc_salesforce snapshot
uv run aisc_salesforce snapshot --output-dir /secure/snapshot-location
```

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

Rows are grouped by Account ID plus a trimmed, case-insensitive submitter email.
Submissions with different Account IDs or emails stay separate. Each submission
with a blank email also stays separate and receives a warning.

Within a group, submissions are ordered by `CreatedDate` and `Id`. Later
nonblank values replace earlier values, while blank later values do not erase
earlier information. Comments and Other Personnel Notes are the exception:
every nonblank value is preserved, including repeated identical text, and
values are joined with `\n` in submission order. Source submission IDs and
names are retained as JSON arrays.

When at least one revised address component is present, the output contains all
five components. Missing submitted components are filled from the Account
billing address where possible.

### CSV contract

Each row contains these groups of columns:

| Group | Columns |
|---|---|
| Source and dates | `source_submission_ids`, `source_submission_names`, `earliest_submission_date`, `latest_submission_date` |
| Account and submitter | `account_id`, `account_name`, `certification_id`, `submitter_name`, `submitter_email`, `submitter_phone` |
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
blank role columns.

Resolution actions are `update_contact` for an exact match or a title/phone
update, `change_email` for a new email applied to the Account's current role
contact, `use_submitted_contact` when another submitted role is the first exact
match, and `create_contact` when an unmatched submitted name describes a new
Contact. A missing Contact ID or a `create_contact` action always comes with a
warning for human review. Resolution sources show whether the match came from
another submitted role, submitted data for a new Contact, an Account Contact, a
sibling Account Contact, or the Account's current role lookup.

Contact text is compared after trimming and case folding. The resolver does not
guess nicknames. Name searches stop at the first tier containing candidates:
other submitted roles, the current Account's Contacts, then Contacts belonging
to sibling Accounts with the same Parent. Multiple candidates at that first
tier are reported as ambiguous. Repeated identical contact information in
several submitted roles counts as one candidate; the same name paired with
conflicting emails remains ambiguous. When a Contact is resolved, missing title
and phone values are filled from that Contact where possible. Missing title or
phone alone does not cause a warning. `has_contact_derived_values` is `true`
only when a nonblank title or phone was actually copied from another submitted
role or a Salesforce Contact. Values submitted directly for that role do not
set the flag.

`has_no_update_content` is `true` when the grouped raw submissions contain no
Key Data fields, role fields, Comments, or Other Personnel Notes. Account,
Case, certification, and submitter metadata do not count. `Type__c` also does
not count, so an exact `"Key Data"` submission with no update fields can have
both `has_key_updates=true` and `has_no_update_content=true`. Values filled from
an Account, another role, or a Salesforce Contact cannot turn the empty-content
flag off.

!!! warning

    The interactive processor requires `has_contact_derived_values` and
    `has_no_update_content` in addition to the existing CSV columns. Older
    staged CSV files fail validation. It also inspects `has_warnings` and
    `warnings` before acting on a row. The `warnings` field is readable,
    newline-separated text; role warnings are also copied into the matching
    prefixed `warning` column.

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

Run the Case preparation, fresh staging, and interactive review as one command:

```bash
uv run aisc_salesforce process-profile-updates
uv run aisc_salesforce process-profile-updates \
  --output-dir /secure/staged-profile-updates
```

The command has no dry-run option. A staged recommendation alone never causes
a Salesforce data change. The command first runs `ProfileUpdateService`; if
Case creation or reuse fails, it stops before staging or review. It then
publishes and validates a new CSV and groups all rows with the same Account and
Case. Batches containing a Key Update strictly older than seven days are
reviewed first, followed by the remaining batches from oldest to newest.

Progress messages appear around authentication, Case preparation, staging, CSV
publication, CSV validation, and review startup. The same output channel is
used for progress and interactive review. Visual separators mark stages, Cases,
staged rows, Contact roles, and response-email sections.

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
| `M` | `make manually` | Pause, refetch, and verify the reviewer’s Salesforce change |
| `N` | `will not be made` | Record the rejection without a Salesforce data change |

Decision shortcuts and complete phrases are case-insensitive. The JSON Lines
audit always stores the complete phrase.

An already-current value is an audited no-op. It does not prompt and does not
appear in response-email text.

For every submitted Contact role with an email address, the command searches
all Salesforce Contacts using that exact email:

- With one match, it fetches that Contact and compares every submitted
  nonblank field. Each mismatch is a separate three-decision proposal.
- With no match, it asks for one explicit decision about creating a Contact
  under the Account. Automatic creation requires a Last Name; incomplete data
  must be completed manually or declined.
- With more than one match, it does not guess. The ambiguity is written as a
  failed audit entry, processing for the Case stops, the Case stays Pending,
  and the source Profile Updates stay open.

No candidate list or Contact-selection prompt is shown. After a Contact is
created or an exact match is reviewed, assigning that Contact to the Account
role is always handled as a separate proposal.

For an exact match, the Contact's current name, title, email, and phone are
displayed together before its individual fields. For a new Contact, all
submitted details are displayed together before the creation decision.
Account-role proposals use friendly Contact names and emails for the current
and proposed values. Salesforce IDs remain available internally for record
writes and audit entries, but are not exposed in decision prompts.

### Output and finalization

Each timestamped run folder contains:

| File | Purpose |
|---|---|
| `profile_updates.csv` | Exact published staging input used by the reviewer |
| `review_audit.jsonl` | One immediately flushed JSON object per decision/result |
| `response_emails.txt` | Generated response text grouped by submitter email |

Automatically applied and manually verified Account-information changes appear
in response text using `ITEM: NEW INFORMATION` followed by
`Replaces OLD INFORMATION`. Each submitted Contact role is instead summarized
as one line containing its name, title, email, and phone. A role that required
no change ends with `- no change`. A following `Replaces OLD INFORMATION` line
is included only when prior role information was actually replaced. The
Account paragraph begins:

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

On interruption, a Salesforce failure, or a manual value that does not verify,
the audit is flushed, unfinalized source Profile Updates stay open, the Case is
kept Pending, and the command exits nonzero. Retrying restages open records.
Values applied before the interruption are fetched again and recorded as
no-ops, so they are not applied or emailed twice.

`Q` or `Quit` is different from an error or keyboard interruption. It writes a
`stopped early` audit event, keeps the current Case Pending, leaves that Case's
Profile Updates open, skips response generation for the unfinished row, and
returns exit code `0`. Completed earlier batches stay completed. Later batches
remain untouched. The final CLI summary says that review stopped at the
reviewer's request and reports completed and pending batch counts.

!!! warning

    All three artifacts can contain sensitive personal and Salesforce data.
    Git ignores their default location and generated filenames. Store custom
    output directories securely, limit access, and do not attach these files
    to public issues or commits.
