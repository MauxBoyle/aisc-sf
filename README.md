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

Audit recently stored Salesforce picklist values against the Python enum
catalog:

```bash
uv run aisc_salesforce audit-picklist-enums
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

`audit-picklist-enums` reads Describe metadata and ordinary records modified
during the rolling two-calendar-year window. Salesforce `*History` objects use
their history entry's `CreatedDate` because those objects do not have
`LastModifiedDate`. Missing enum values (including every observed value for an
uncataloged queried picklist) are informational and return `0`. Authentication,
metadata, or query failures return `1`. The command writes no files and makes
no Salesforce changes.

### Salesforce picklist values in code

When changing a Salesforce picklist value in Python, use the matching enum from
`aisc_salesforce.salesforce_enums` rather than repeating the quoted value.
New picklist fields must be added to both their enum and `SALESFORCE_ENUMS`,
then covered by a focused test. See the [developer enum-catalog
rule](docs/usage.md#developer-rule-use-the-salesforce-enum-catalog) for the
complete workflow.

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

Submissions are grouped by Account ID and a normalized submitter-email
comparison key. Email values are trimmed and lowercased, then dots are removed
from the local-part for comparison on every domain. Invalid basic email
structures are preserved for review and produce a warning. Later nonblank
values replace earlier values,
except that every nonblank Comments and Other Personnel Notes value is
preserved in submission order and joined with a newline. Different emails stay
separate, and every blank-email submission stays separate and receives a
warning. Each CSV row preserves all source submission IDs and names as JSON
arrays.

Submitted contact values are canonicalized before role matching and
`contact_resolutions` creation. Submitter and role emails are trimmed and
lowercased, names and titles use Proper Case with project-defined exceptions,
and recognizable North American phone numbers use `###.###.####` plus an
optional ` x#` extension. For example, `jane mcdonald`, `chief qa officer`,
and `+1 (312) 555-0100 ext. 123` become `Jane McDonald`,
`Chief QA Officer`, and `312.555.0100 x123`. Blank values stay blank.
International, malformed, and otherwise unrecognized phones are only trimmed;
for example, `+44 20 7946 0958` remains unchanged.

This applies only to submitted contact data. Existing Salesforce Contact
values and non-contact submission fields are not reformatted, and staging
remains query-only. The interactive processor applies the same rules when it
reloads fresh submissions before comparisons. See the
[detailed normalization rules and exception-list maintenance](docs/usage.md#submitted-contact-normalization).

The CSV has shared submission and Account columns, Key Data columns, and
prefixed role columns for `certification_`, `principal_`, `accounting_`,
`quality_`, and `new_york_`. Role columns preserve submitted values and record
the proposed resolution action, Contact ID, resolution source, source
submission/role, and any role-specific warning. These readable columns remain
for compatibility. The authoritative `contact_resolutions` column is a JSON
list with one entry per distinct comparison key. Each entry records its
`classification` (`use_existing`, `create_new`, `likely_typo`, or `ambiguous`),
normalized email, comparison key, submitter/role sources, candidate and
selected Contacts, reason, confidence, warnings, and submitted details.
New York does not have a title column. When an existing Contact is resolved, a
missing title or phone is
filled from that Contact where possible. Repeating the same contact information
in several roles does not create a warning, but conflicting emails for the same
submitted name are treated as ambiguous.

Contact searches prefer the Account's **family accounts**: the target Account,
its parent Account, and its **sibling accounts** that have the same parent. A
root Account with no parent has only itself in its family. Exact normalized and
unique dot-insensitive email matches are safe. Name-derived local-parts use
normalized first/last names and initials. Differing domains, generic mailboxes,
multiple candidates, external Contacts, and other weak evidence require an
operator choice. Only one insertion, deletion, substitution, or adjacent
transposition is presented as a likely typo, and it still requires
confirmation. A trailing square-bracket suffix on a Salesforce Contact name is
ignored only while comparing; the original value remains visible and audited.

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

`process-profile-updates` keeps the complete convenience workflow and now
composes three independently callable session phases in this order:

1. Read-only stage every New Profile Update.
2. Atomically publish `profile_updates.csv` and `review_queue.json` in one
   timestamp-named session folder.
3. Prepare Cases for captured submissions that already have Accounts.
4. During review, repair blank Submission Accounts, prepare their newly
   possible Cases, refresh the same captured data set, and review changes in
   deterministic order.

Run the phases separately when a person or TUI needs to inspect the stable
artifacts before any Salesforce write or before interactive review:

```bash
uv run aisc_salesforce process-profile-updates stage \
  --output-dir /secure/staged-profile-updates
# Example printed ID: 2026-08-04T15-30-00Z

uv run aisc_salesforce process-profile-updates prepare \
  2026-08-04T15-30-00Z \
  --output-dir /secure/staged-profile-updates

uv run aisc_salesforce process-profile-updates review \
  2026-08-04T15-30-00Z \
  --output-dir /secure/staged-profile-updates
```

The combined form remains:

```bash
uv run aisc_salesforce process-profile-updates \
  --output-dir /secure/staged-profile-updates
```

On a supported interactive terminal, review output uses a small semantic color
palette while keeping every existing label: submitted values are bright green,
script-supplemented values and `Note` lines are bright yellow, warnings and
validation feedback are bright red, and response-email bodies are bright blue.
Headings, current Salesforce values, progress messages, and record counts stay
uncolored, so color is never the only way meaning is communicated.

Disable colors with `--no-color`. For a nested command, the option may appear
before or after the operation:

```bash
uv run aisc_salesforce process-profile-updates --no-color review SESSION_ID
uv run aisc_salesforce process-profile-updates review SESSION_ID --no-color
```

The standard `NO_COLOR` environment variable also disables colors. Redirected
output and custom `output_fn` callbacks use plain text automatically. Rich
provides the terminal detection and rendering fallback; see its
[Console documentation](https://rich.readthedocs.io/en/stable/console.html) and
[style/theme documentation](https://rich.readthedocs.io/en/stable/style.html).
Generated artifacts, including `response_emails.txt`, are always plain text and
never contain terminal escape codes.

`stage` prints the generated session ID and both artifact paths. If two stages
start in the same UTC second, the final atomic rename claims each ID; a
collision retries with `-01`, `-02`, and so on without replacing an existing
published session. `prepare`
and `review` accept only that exact folder name as a direct child of
`--output-dir`; absolute paths, separators, `..`, symlinks, missing files,
unsupported queue schemas, malformed CSV data, and CSV/queue submission-ID
mismatches are rejected before Salesforce writes.

The initial queue therefore exists before the first reviewer question or
Salesforce write. Account repair and Case preparation appear as setup changes,
so missing records and ambiguous matches remain visible instead of preventing
the preflight artifact from being created. Queue snapshots are atomically
replaced before and after lifecycle transitions and Salesforce mutations.

Every platform flushes staged CSV and review-queue file data with `flush()` and
`os.fsync()` before publication. POSIX systems also sync the containing
directories so rename metadata is durable. Windows safely skips directory
syncing because it does not support opening directories this way; file flushing
and atomic publication still occur. This relies on the temporary and output
directories being on the same filesystem; POSIX refuses a non-empty destination
directory and Windows refuses any existing destination.

For a New Profile Update with a blank Account, the command looks up Accounts
using the submitted Certification ID. If exactly one Account matches, it is
linked automatically. Ambiguous matches are presented as a structured choice;
enter the displayed number, or enter `P` to look up a different Certification
ID. If the submitted ID is missing or has no match, review requests a
Certification ID specifically to find the Salesforce Account. The Account is
saved on the Profile Update before Case preparation begins.
After a scoped Salesforce refresh verifies the repaired submission now has an
Account, `review` includes that submission in Case preparation before the final
staging refresh and interactive review.

Batches containing a Key Update strictly older than seven days are reviewed
first. The remaining batches are reviewed from oldest to newest.

The session stores an append-only `review_audit.jsonl` and deduplicated
`response_emails.txt`. Explicitly resuming `prepare` or `review` keeps completed
and blocked work, resets interrupted, failed, `in_progress`, and
`stopped_early` work to pending, skips completed Case batches, and refetches
Salesforce before retrying unfinished batches. A fully completed session is a
successful no-op with a clear message. Submissions arriving after `stage` are
excluded from every later phase of that session.

The commands print progress around authentication, publication, Account
resolution, Case preparation, refresh, and review. Section separators make
workflow stages, Cases, staged rows, Contact reviews, and response emails
easier to distinguish.

The review processor is renderer-neutral: it exchanges frozen Python
dataclasses with a `ReviewUI` implementation instead of calling `print()` or
`input()`. `CLIReviewUI` turns those typed events and questions into the same
terminal output, shortcuts, and complete phrases documented below, so the
command and reviewer decisions are unchanged. Interpolated Salesforce values
are carried as `ValueFragment` objects, and each choice question contains only
the actions that are actually available. This boundary is the extension point
for a future TUI; Salesforce writes, validation, auditing, and status changes
remain in `InteractiveProfileUpdateProcessor`.

`review_queue.json` starts at schema version `1`. It contains ordered Case
batches, staged rows, and field changes; UUID5-based item IDs; readable labels;
explicit Salesforce object, record, and field references; current and proposed
values; warnings and blockers; statuses; prior Case activity; and
`default_next_item_id`. Labels are presentation only and are never used as
identity keys. The CLI prints a short summary from each complete queue snapshot,
while another `ReviewUI` implementation can use the full model for navigation.

Parent Accounts are detected from fresh, direct-child Account records at the
start of each Case batch. Traversal stops after that one level; grandchildren
are never targets. Only direct children whose certification status is exactly
`Certified` or `Initials` receive Account field changes or Account-role links.
The staging CSV records this affected-Account context, and the queue expands
child-specific Account and role-link entries to those child IDs while keeping
Contact work shared.

Before any Salesforce write in the batch, the processor compares the active
children only for Account fields and role lookups actually submitted. Displayed
values are compared after outer whitespace is trimmed, and null equals blank;
remaining text is case-sensitive. When all relevant child values agree,
Contacts are reconciled once, newly created Contacts remain owned by the Parent
Account, and each active child follows the normal Account and role review path.
Inactive children are not updated.

If active child values conflict, the CLI shows every conflicting field, its
requested value, and every active child's current name, ID, and value. If the
Parent has direct children but none are active, it shows their statuses instead.
Either condition requires acknowledgement, records a `deferred manual
follow-up` audit outcome, marks the whole Case batch `blocked` in the queue,
leaves every source Profile Update and the Case open, and continues to the next
Case. No Contact, Account, role, Case, or submission write occurs for that
blocked batch. A later retry refetches Salesforce, so processing can continue
normally after manual reconciliation. Rerun `review SESSION_ID`; the refresh
stays limited to that captured session.

Before each staged CSV row, the command shows the Account, submitter, and source
Profile Update names. It also notes when contact details were supplemented or
when the combined update has no submitted update content. At the Continue/Quit
checkpoint, press Enter, type `C`, or type `Continue` to review that row. Type
`Q` or `Quit` to stop safely. Only this checkpoint has a default; change
decisions always require an explicit answer. Published CSV files must include
the complete current contract, including `contact_resolutions`; older staged
files fail validation.

Each real field change accepts a shortcut or its complete decision phrase:

- `A` or `apply automatically` writes the displayed value to Salesforce.
- `M` or `make manually` defers the field to the manual follow-up section, then
  refetches Salesforce to verify the result.
- `N` or `will not be made` records the rejection without changing Salesforce.

Shortcuts and phrases are case-insensitive. Audit entries always store the
complete phrase.

All staged-row checkpoints are completed before Salesforce changes begin. The
review then prints four explicit sections in order: `Contact Updates`, `Manual
Contact Follow-up`, `Account Updates`, and `Role Links`.

The Contact section reloads every source Profile Update from Salesforce and
collects only nonblank Contact values that were explicitly submitted for the
submitter or a role. Staging fallbacks and old Salesforce values can help
identify a Contact, but they are never treated as proposed changes. Salesforce
Contacts with a valid submitted email are identified by that email before role
assignment is considered. The processor queries fresh Contacts and applies the
conservative family-aware matching rules to decide whether an exact Contact can
be used, a new Contact can be created, or an operator choice is required. It
does not assume that the Contact currently holding a role is the submitted
person. For example, if Tim's email is submitted for the principal role while
Ray currently holds that role, Tim's Contact is reviewed and updated; Ray's
Contact is not used merely because of the role. A partial proposal with no
email may still use its staged or current Account-role Contact ID as a safe
fallback.

All values for the same email identity are reconciled together. Repeated
normalized values collapse into one proposal with all of their sources.
Different email identities remain separate Contact reviews even when they
appeared in the same role. If identity review later resolves two entries to the
same Salesforce Contact ID, their proposals are combined at that point.
Distinct nonblank values for one resolved Contact are a conflict. The reviewer
sees every candidate value and its submission/role sources side by side, then
must choose a candidate or `current` for each conflicting field. Choosing
`current` removes that field from the eventual Salesforce write. Every conflict
is resolved before the first Contact write.

After reconciliation, the heading identifies the person and email rather than
leading with a role. One Contact-level table shows each current value,
reconciled value, and source. Each changed First Name, Last Name, Title, Email,
and Phone field then receives its own `A`, `M`, or `N` decision. Already-current
fields are audited no-ops and do not prompt.

```text
Contact Title: Alex Smith <alex@example.com>
Current Salesforce value: Old Title
Proposed value: Owner
```

Approved fields are grouped into at most one automatic Contact update or create;
rejected and manual fields are omitted from that payload. A new Contact requires
an approved Last Name for automatic creation. After all automatic Contact work
finishes, `Manual Contact Follow-up` processes manual fields one at a time. If a
fresh Salesforce value differs from the submission, the reviewer sees both
values and may accept Salesforce's value; Enter defaults to `yes`. Declining
fails verification, leaves the Case and submissions open, and permits a later
retry. Source-to-Contact mappings are preserved so role links and response text
use a final refreshed Contact.

Non-role Account changes run after every Contact is complete. The `Role Links`
section runs last and may update only Account lookup fields; it never creates
or updates a Contact. Several rows or roles that describe the same Contact
reuse its one result. This separation means a Contact is first brought up to
date based on its identity; only afterward does the reviewer decide which role
should point to it. A partial role without an email uses its current
Account-role Contact when available and cannot create a new Contact
automatically without safe identity evidence.

When a submitter email is also used by a role, the richer role details are used
for Contact work. Otherwise the submitter name is split at its final space.
Creating a submitter Contact still requires the normal explicit decision. A
Contact created by that decision is assigned to `Case.ContactId` and both
writes are audited separately. Merely matching an existing submitter Contact
does not change `Case.ContactId`.

Account-role proposals show current and proposed Contact names and emails;
Salesforce Contact IDs remain internal to writes and the audit trail instead of
appearing in decision prompts.

If a Contact create is blocked by Salesforce with `DUPLICATES_DETECTED`, the
structured error is retained and the reviewer can create manually, update an
existing Contact manually, use a Contact with another email, or ignore that
entry. Manual recovery is verified by querying the normalized submitted email;
the alternate-email choice queries the entered email. Multiple results always
require an explicit candidate selection. Unrelated Salesforce errors remain
fatal and leave the Case retryable.

Comments, Other Personnel notes, Key Update answers, effective dates, warnings,
and same-day Account History are shown once at the beginning of each Case
batch. Reused submissions and local calendar days are fetched only once during
that Case review.

The timestamped staging folder contains:

```text
profile_updates.csv
review_queue.json
review_audit.jsonl
response_emails.txt
```

The queue is deterministic for unchanged staged input and contains no run
timestamp. Its ordered work phases are setup, Contact, Account, and role link.
Statuses are `pending`, `in_progress`, `blocked`, `completed`, `failed`, and
`stopped_early`; a completed change also keeps its audit outcome, such as
`applied`, `verified manually`, `rejected`, or `no-op`. Parent work deferred to
manual processing keeps the `deferred manual follow-up` outcome on blocked queue
work. The default-next pointer
is the first unblocked pending change and becomes `null` when none remains.

The JSON Lines audit is flushed after every decision and Salesforce result.
Contact events include classification, comparison key, candidates, selected
Contact, reason, confidence, and warnings. Each conflict choice is an explicit
event containing its candidate values and sources. Each Contact field has its
own result with the submitted `proposed_value` and authoritative `final_value`.
Those values differ when the reviewer accepts a manual Salesforce override.
Operator identity selections, duplicate recovery, ignored entries, Case Contact
assignment, and role assignments remain separate events.
The response file contains one generated paragraph per submitter email.
Account-information changes keep the `ITEM: NEW INFORMATION` and
`Replaces OLD INFORMATION` format. Each submitted Contact role is consolidated
into one contact-information line. An unchanged role ends with `- no change`;
`Replaces ...` appears only when existing role information was actually
replaced. The previous name, title, email, and phone come from an in-memory
snapshot taken at the beginning of the Case batch, before any Contact or role
write. This keeps replacement details accurate when an earlier update in the
same batch changes the previous role holder. The snapshot lasts only for the
current processing attempt and is not added to CSV, queue, audit, or Salesforce
schemas. The command prints the text but does not send email itself; after
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
> Staging, queue, audit, and response files contain personal and Salesforce data.
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
