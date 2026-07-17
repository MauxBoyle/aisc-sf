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
earlier information. Source submission IDs and names are retained as JSON
arrays.

When at least one revised address component is present, the output contains all
five components. Missing submitted components are filled from the Account
billing address where possible.

### CSV contract

Each row contains these groups of columns:

| Group | Columns |
|---|---|
| Source and dates | `source_submission_ids`, `source_submission_names`, `earliest_submission_date`, `latest_submission_date` |
| Account and submitter | `account_id`, `account_name`, `certification_id`, `submitter_name`, `submitter_email`, `submitter_phone` |
| Notes and review | `comments`, `personnel_notes`, `has_warnings`, `warnings` |
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
phone alone does not cause a warning.

!!! warning

    A future processor must inspect `has_warnings` and `warnings` before acting
    on a row. The `warnings` field is readable, newline-separated text; role
    warnings are also copied into the matching prefixed `warning` column.
