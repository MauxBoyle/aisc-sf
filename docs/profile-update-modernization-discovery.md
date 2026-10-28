# Profile Update modernization: discovery record

Working record for the existing Python Profile Update workflow. This captures
observed code behavior, the operator's stated rules, and questions that still
need real examples. It is not an implementation sequence or an issue list.

## Purpose and boundaries

**Operator rule:** Reduce administrative work so Garrett can process routine
Profile Updates safely. The operator keeps complicated decisions and owns the
Case. Salesforce remains the operational system. Historical duplicate cleanup
and general Salesforce/iMIS synchronization are outside this project. Improve
the existing Python workflow; do not replace it. Discovery must not make live
Salesforce changes.

The older [project backlog](project-plan-revised.md) covers a wider scope. Its
unchecked items are not requirements for Garrett's initial handoff. Organize
development as Phase 1 for safe routine decisions, Phase 2 for portability and
the browser handoff, and Phase 3 for later improvements. The
[proposed issue list](profile-update-modernization-issues.md) records that
split.

### Relationship to the older backlog

The older plan's workflow inventory, reviewer clarity, submitter safeguards,
address validation, and address follow-up items overlap this discovery work.
They are candidates to define against the operator rules below before writing
issues. Its request to update a related Audit promptly names no trigger, target
Audit, or field list. The operator expects most such updates to happen through
Salesforce Flows. Verify that responsibility before proposing a Python write.

Some unchecked items already have partial or documented support. Case
preparation can set `Case.ContactId` from a matched submitter when creating or
reusing the expected Case; processing also assigns a newly created submitter
Contact. Matching an existing submitter during processing alone does not
change that lookup. The current workflow documents external User provisioning,
including Applicant deferral, and submitted Contact normalization with an
editable capitalization exception list. Any issue for these areas should name
the remaining gap rather than repeat existing behavior.

The older plan also lists delayed portal email, employee-count follow-up,
parenting of dropped/replacement Accounts, Membership notification, Contact
note classification, and replacement of master-sheet steps. These remain
separate backlog candidates until their place in the initial handoff is
decided. Operator attribution now belongs to Phase 2, alongside the browser
handoff. The auditor-facing address notice has since gained operator rules
below; the old plan's open question about its sender and reply-to remains to
be confirmed before implementation.

## Current Python workflow

**Confirmed code behavior from discovery:**

- Read-only staging publishes `profile_updates.csv`. Interactive processing
  publishes `review_queue.json`, where Case batches, rows, proposed changes,
  statuses, blockers, and progress persist. `review_audit.jsonl` records
  decisions and field values. The processor sends typed events and questions
  through `ReviewUI`, which another interface could implement.
- Staging groups submissions by Account and normalized submitter email. Later
  nonblank submitted fields replace earlier ones in the combined CSV row;
  Comments and Other Personnel Notes retain all nonblank values. Detailed
  Contact review rereads the individual Salesforce submissions and resolves
  conflicting values before Contact writes.
- Current Account fields and relevant Contacts are queried during staging, but
  the CSV mainly exposes submitted values and resolution information. The
  queue's Account and role-link proposals initially have `current_value=None`.
  Detailed review fetches and displays fresh Salesforce comparisons later.
- A submitted Contact matched by email can differ from the Contact currently
  assigned to that Account role. The processor can update the matched Contact
  and then review the role link separately. One submitted person in multiple
  roles is supported and is not itself ambiguous.
- Staging can fill an omitted address component from the Account billing
  address, and an omitted role title or phone from another submitted role or a
  Salesforce Contact. `has_contact_derived_values` marks the whole CSV row,
  not the source of each displayed field. Processing only treats fresh,
  explicitly submitted Contact values as proposed writes.
- The code stores original role-holder snapshots only in memory. On restart,
  previously applied changes may become no-ops and disappear from regenerated
  response email text.
- The response generator summarizes applied changes and role responses. It
  does not generate specific pending-work text or responses to Comments. The
  current email-sent yes/no prompt couples "yes" to Case closure; "no" leaves
  the Case Pending but closes the Company Profile Change.
- A truly blank submission is flagged and shown as a note, without a required
  clarification-email checkpoint. Comments and Other Personnel Notes are shown
  and audited as context, without explicit actions or generated-email text.
- Key Update answers are shown as context, without a separate manager
  checkpoint. An accepted future-dated company name or address can currently
  be applied immediately. Account History shown during review is limited to
  the submission's local calendar day and omits who made the change.
- Parent Account routing conflicts require acknowledgement and leave the batch
  open for manual follow-up.

Code entry points: `src/aisc_salesforce/stage_profile_updates.py`,
`src/aisc_salesforce/review_queue.py`,
`src/aisc_salesforce/process_profile_updates.py`, and
`src/aisc_salesforce/review_ui.py`.

## Browser access for Garrett

**Handoff requirement, 2026-10-22:** Garrett needs to stage, review, and
complete routine Profile Updates in a browser without running commands. A
Streamlit interface is a plausible way to provide this while retaining the
existing Python processing rules. The UI should show the full read-only
comparison, ask for each decision, display the proposed effect before a write,
and make operator-only handoffs and prepared messages easy to find.

**Implementation boundary:** `ReviewUI` already separates typed display events
and questions from the terminal renderer, and `review_queue.json` records
progress. However, `ReviewUI.ask()` currently waits for an answer inside one
long-running processor call. A browser interaction returns control between
answers, so the processor needs a durable, resumable decision boundary before
the existing review can be safely driven from Streamlit. Keep business rules
and Salesforce writes in the shared Python workflow rather than duplicating
them in page code. Refresh Salesforce values before applying each decision,
and preserve audit events and retry behavior across refreshes or restarts.

The deployed app also needs staff-only access, operator/Garrett permissions,
secrets outside the repository, and durable access-controlled storage for the
queue, audit, and response artifacts. Browser session memory alone is not a
handoff record. Hosting and identity provider are still to be selected; a
locally running browser page would still require someone to start and maintain
the process. Streamlit Community Cloud local storage is not guaranteed to
persist, so it is not an assumed production home for these artifacts.

**Work attribution requirement, 2026-10-22:** Staff need Salesforce Notes and
Chatter that explain who is working on a Case, what was completed, and what is
waiting for someone else. The current reviewer must be visible separately from
the Case owner, which remains the operator during Garrett's routine work. The
current Salesforce client uses the OAuth client credentials flow.
[Salesforce documents](https://developer.salesforce.com/blogs/2024/02/invoke-rest-apis-with-the-salesforce-integration-user-and-oauth-client-credentials)
that all actions made with such a token run as its configured user. If the app
uses the owner's Salesforce account, Salesforce's native author on posts and
direct API writes will appear to be the owner even when Garrett made the
decision. The account that hosts Streamlit and the Salesforce API run-as user
are separate settings; hosting under the owner's account alone does not set
Salesforce authorship.

The current Case automation also uses a hard-coded `-MB` Chatter signature.
Choose individual Salesforce authorization if native authorship must match the
reviewer; otherwise use a dedicated integration account, authenticate each
human reviewer, and put that verified actor in the Note or Chatter content and
local audit. Text attribution does not change Salesforce's native author.
Decide which Case milestones deserve a durable Note or a brief Chatter post,
and avoid duplicate entries on retry. The same attribution rule should apply
to CLI work.

## Overview before detailed decisions

**Real example shared during discovery, 2026-10-19:** The operator's spreadsheet
lists one submission vertically. Yellow role cells are the only cells with
submitted role information; unhighlighted cells that show the next field label
represent blanks. In this straightforward example, the same submitted person
appears in Certification and Quality, separate people appear in Principal and
Accounting, and New York has no submitted role values. This describes the
shape of the example without copying participant details into the repository.
The `Is Cert or Principal?` field shown in the sheet is not useful to the
operator and does not need to drive this overview.

**Operator need:** Scan everything submitted together before detailed CLI
decisions, with submitted values beside the original/current Salesforce
information. Early cues include incomplete entries, two different submitted
names using one email, and a Contact email-domain change.

**Confirmed code implications:**

- The CSV has columns for five roles, Key Data, Comments, notes, and warnings,
  but its wide rows do not form this compact comparison. The CLI's row
  checkpoint shows Account, submitter, and Profile Update names; it does not
  show all role comparisons together.
- A Contact selected as a match for the submitted email is not necessarily the
  Contact currently assigned to the submitted role. Those must be distinct
  labels in any comparison. Existing staging queries already read Account role
  lookup IDs and their Contacts, though the current CSV does not expose every
  current role-holder detail as a readable column.
- A displayed value needs its origin identified as submitted, supplemented, or
  current Salesforce. In the real spreadsheet, yellow means submitted. In the
  staged CSV, a blank phone or address component may already have been filled,
  and the row-wide `has_contact_derived_values` flag is not field-level proof.
- The combined CSV can hide earlier different submitted names on one email
  because later nonblank values replace earlier values. Detailed review still
  rereads each submission, but that is later than the operator's desired early
  warning.

**Operator decision, 2026-10-22:** Show each submitted role beside its current
Salesforce role holder. Repeat a person who fills two roles so every role has a
consistent comparison. Keep the matched Contact distinct from the current role
holder when they differ.

## Garrett's routine scope and handoffs

**Operator rules:** Garrett may independently decide clear Contact field and
Account role changes and send routine participant acknowledgments. He may
handle simple requests in Comments and employee departures when the script
calls out each request and records what happened. Every nonblank Comment or
Other Personnel Note must be marked addressed or handed off before the Company
Profile Change closes.

Keep company name, facility address, and ownership changes with the operator,
including requests made only in Comments. Also keep Contact email-domain
changes, missing last name or email, two different submitted names using one
email, and Portal User provisioning failures with the operator. A monthly User
deactivation process is sufficient after an employee removal; a weekly script
is a later improvement.

For a departure, check whether the Contact still holds a role on **any**
Salesforce Account, including overlooked relationships. A remaining role
generally needs the operator. Account and other Contact details can distinguish
same-name Contacts when the departure request gives only a name.

**Generic mailbox exception:** A shared address such as `ap@` or `quality@`
may remain the company's contact point. Keep its Contact and Portal User, and
rename the Contact instead of removing it. Use a supplied replacement person's
name; otherwise use the company name as First Name and the mailbox purpose
(for example `Quality`) as Last Name. Garrett can decide whether an address is
generic for now. He may rename its Contact when all its roles are within one
company family; a role outside that family goes to the operator. Two different
names **submitted** for one generic mailbox go to the operator for
clarification. One submitted replacement name that differs from Salesforce's
existing name is routine. Project documentation should explain this exception.

For a named replacement, participant wording should identify who replaces
whom at the shared address. Without a replacement name, wording should say
that the former person has been removed from that email address.

## Case, manager, and participant communication

**Operator rules:** The operator may make safe changes before handing off a
complicated Case and may use `f.k.a.` or `(pending address change)` in a
Salesforce field. The operator tags the manager for approval, investigation,
or coordination as needed. Once contacted, that Case leaves automated
processing; the application need not track the manager's decision. The
operator keeps Case ownership and monitors open Cases in a Salesforce list
view.

When multiple people submit updates for one Case, attach the most recent
submitter to `Case.ContactId`. At the participant-email step, alert the reviewer
to include all submitters in the message. The application prepares text but
does not send that email itself.

A useful manager summary includes what changed, driving time between old and
new facilities when relevant, Audit timing, and the participant's Key Update
answers. Suggest a next step when straightforward; otherwise leave the
decision to the manager.

The Company Profile Change can close while the Case stays open. The operator
usually sends a partial acknowledgment that states completed changes and the
specific requests still under review; sometimes no email is sent. The Profile
Change can close in either case so it leaves that queue. The Case closes after
the participant receives the final answer, even if a separate task to remove
a temporary notation remains.

For a truly blank submission, the operator sends this clarification:

> You recently submitted a profile update through the AISC Participant Portal,
> but there were no selections made. If you wish to update information about
> your AISC Certification account, please resubmit, or respond to this message
> with the changes you wish to make. Thank you.

If a related self-service Account change occurred, acknowledge that change
instead. The operator recognizes it through Account History attributed to
Susan Thomas and within about six hours of submission, including across
midnight. The present code's local-calendar-day Account History query and
field list cannot establish both conditions.

## Audits, moves, future dates, and temporary notations

**Operator rules for manager summaries:** List every future-dated Audit except
New York type `SA-NYC`; `Scheduled - Approved` status is not required. Include
canceled or withdrawn future Audits as clearly labeled context, never as the
next planned Audit. If no future Audit exists, include the last completed
non-New-York Audit. If future Audits exist, also include the last completed
Audit when it was within about five weeks.

Driving time measures move distance. A move combined with a company name
change or Certification/Principal Contact change calls for closer review, while
safe changes may still proceed before handoff. After the operator confirms a
real move, an automatic notice to each auditor already attached to a relevant
upcoming Audit would be acceptable around the time of the participant email.
No attached auditor means no notice. The notice needs company name, old and
new addresses, Audit date and number, address approval status, and replies to
Certification. It is early communication; Case closure does not wait for a
later post-approval notice. For a future effective date, tell the auditor to
use the old address for a visit before that date and the new one afterward.

Do not apply a company name or facility address change in Salesforce before
its future effective date. The operator creates a Notion task linking the Case;
a Salesforce Task may become a backup later. The Case can close once the
participant receives the plan. Remove a substantial name change's `f.k.a.`
notation after the first subsequent Audit Review changes from In Progress to
Completed. Remove `(pending address change)` shortly after the scheduled
auditor visit; the scheduled visit date is the reminder, without a completion
status check.

**Implemented in the terminal review:** before a future-dated name or address
handoff continues, the operator confirms that a Case-linked Notion task exists.
The review saves that confirmation and each temporary-notation choice in the
session queue so an interrupted session can resume safely. It does not create
or verify Notion tasks. Used notations add their cleanup trigger to the handoff
audit. Before closure, the operator confirms the participant received the plan;
that confirmation is saved for retry. The cleanup reminders alone do not keep
the Case open after the participant plan is sent.

## Discovery state

The real example supports the overview section and shows a routine pattern of
one person in two submitted roles. It does not reveal the current Salesforce
role holders, so no actual change decision can be inferred from the image
alone. No tricky real example has been supplied. No live Salesforce change was
made during discovery.
