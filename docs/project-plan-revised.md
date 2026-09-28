# Project plan

This file is the development backlog for `aisc-sf`. It is meant to
answer a practical question: **when I have development time, what useful
improvement should I build next?**

Items are grouped by project rather than by technical architecture.
Within each project, work is ordered roughly from safer/read-only
foundations, through human-reviewed automation, to write-back
automation. A later item does not have to wait when it is independent
and clearly more useful, but the order should usually provide a sensible
next feature.

The overall pattern is:

1.  **Observe and validate** --- snapshots, reports, discrepancy
    detection, and previews.
2.  **Stage and review** --- identify proposed changes and put uncertain
    cases in front of a human.
3.  **Apply safely** --- make approved changes, record what happened,
    and make reruns safe.
4.  **Automate routine execution** --- overnight jobs or batch apps once
    the underlying process is trustworthy.

------------------------------------------------------------------------

## 1. Profile Updates, contacts, and portal access

**Goal:** Turn Profile Updates and related contact/account maintenance
into one reliable, reviewable workflow. This is a high-priority project
because it is frequent work and errors can affect participant access or
records.

### Development sequence

-   [x] Create/update Profile Update cases from Audit comments and
    submitted Profile Update objects.
-   [x] Build the current `profile-updates` / `process-profile-updates`
    staging and processing workflow.
-   [ ] Review the current workflow end-to-end and document exactly when
    the Contact, Case, source Profile Update, Audit, roles, and User are
    changed.
-   [ ] Add the Profile Update submitter to the Case; decide the correct
    point in the workflow and preserve the source of that attribution.
-   [ ] Update the related Audit as soon as an approved Profile Update
    requires it, rather than waiting for all later processing.
-   [ ] Finish the portal-access path for new or changed Contacts/roles,
    including clear handling of Applicant Accounts versus Certified
    Accounts.
-   [ ] Decide whether the portal-access email can safely be delayed
    (approximately one hour) after provisioning, then implement only if
    the delay is reliable and retry-safe.
-   [ ] Make Contact/User creation and update decisions easier for the
    reviewer to understand before applying them.
-   [ ] Add safeguards for Profile Updates submitted by a newly created
    or otherwise unexpected submitter.
-   [ ] Improve address-change validation so partial or implausible
    address changes do not get applied.
-   [ ] Detect employee-count changes that may affect audit days or
    pricing and surface them for follow-up.
-   [ ] Add normalization/validation for email typos, phone numbers,
    whitespace, URLs, and common name-capitalization problems.
-   [ ] Support editable capitalization exceptions such as `Mc...` and
    `Van...` rather than hard-coding every exception.
-   [ ] Review how dropped/replacement Accounts should be parented when
    a Profile Update touches the Account structure.
-   [ ] Add a clear address-change follow-up path, including the open
    question of whether an auditor-facing message should come directly
    from the automation with Certification as reply-to.
-   [ ] Notify Membership when a Profile Update changes Contact/Account
    information that also matters in iMIS.
-   [ ] Add standardized Contact notes / email-type classification where
    the workflow currently leaves inconsistent free text.
-   [ ] Add operator attribution to Chatter/audit notes created by the
    scripts.
-   [ ] Replace any remaining CSV master-sheet steps with the Python
    workflow.

------------------------------------------------------------------------

## 2. Certificate renewals and certificate production

**Goal:** Turn the recurring "Green Folder" work into a safe batch
process that can handle the ordinary cases automatically and isolate
exceptions.

### Development sequence

-   [ ] Inventory the exact steps and fields used for new certificates
    and renewal certificates.
-   [ ] Build a read-only queue showing certificates ready for
    processing and why each item qualifies.
-   [ ] Separate standard cases from exceptions before any Salesforce
    changes or document generation.
-   [ ] Add a persistent hold state for "half-processed" cases, such as
    a renewal waiting for a letter, so they do not reappear endlessly in
    each weekly batch.
-   [ ] Build preview output for the changes/files that would be
    produced for a standard renewal.
-   [ ] Automate standard renewal processing with an explicit human
    apply/approval step.
-   [ ] Add new-certificate processing to the same batch app where the
    workflow is genuinely shared.
-   [ ] Normalize certificate-specific display text, including removal
    of `CO` / `LTD` suffixes from Chinese company names when generating
    certificate files.
-   [ ] Add exception reporting and a durable processing/audit history.
-   [ ] Once the reviewed batch process is stable, evaluate which
    standard cases can run with less manual intervention.

------------------------------------------------------------------------

## 3. Data cleanup and Salesforce integrity

**Goal:** Replace scattered cleanup work and Tableau Prep checks with a
repeatable pipeline: detect overnight, review in the morning, and apply
only approved corrections.

### Overnight/read-only detection

-   [ ] Stage records with likely wrong Certification Status.
-   [ ] Stage Applications that appear ready to be closed.
-   [ ] Flag improbable or mismatched email addresses.
-   [ ] Flag Accounts and Contacts with unnecessary capitalization.
-   [ ] Flag Applicants with no Certification Status.
-   [ ] Flag multiple Accounts sharing an email domain that are not
    parented as expected.
-   [ ] Detect orphaned Contacts imported from iMIS or other lists.
-   [ ] Detect empty/orphaned Cases that failed to attach to the correct
    Account.
-   [ ] Detect Cases owned by former employees/chatbots that should be
    routed to a department queue.
-   [ ] Detect stale Account Contact Relationships and obsolete portal
    Users.
-   [ ] Detect inactive record owners that need reassignment.
-   [ ] Detect address/country normalization problems.
-   [ ] Detect duplicate Contacts and multi-account Contacts homed on a
    sibling rather than the Parent Account.
-   [ ] Detect New York contacts attached to Accounts that are not
    actually in the NY Specialty program.
-   [ ] Detect non-certification Accounts carrying certification-only
    requirements such as employee count or continent.

### Human review / cleanup app

-   [ ] Build a morning TUI that combines the cleanup findings into one
    review queue.
-   [ ] Show the reason each item was flagged and the proposed
    correction.
-   [ ] Allow "approve," "skip," and "needs investigation" without
    changing Salesforce on initial versions.
-   [ ] Add preview/export of approved changes.
-   [ ] Add an explicit apply step with an audit trail and safe reruns.

### Larger cleanup projects

-   [ ] **Omega Account cleanup:** standardize dropped-account naming
    with `Ω` plus drop year/quarter.
-   [ ] Ensure dropped/Omega Accounts retain required location
    information.
-   [ ] Reparent returning companies under replacement Accounts
    according to the agreed Parent/Child logic.
-   [ ] **Orphaned & empty Cases:** resolve routing when external
    auditors or shared email addresses prevent normal matching.
-   [ ] **Orphaned Contacts:** link imported Contacts to the correct
    Account.
-   [ ] **Data dictionary:** inventory Salesforce fields, identify what
    is actually used, and identify obsolete statuses, dummy Accounts,
    and unused iMIS fields before deletion.
-   [ ] Migrate remaining recurring cleanup steps from Tableau Prep into
    Python.

------------------------------------------------------------------------

## 4. Audit Review outcomes and appeals

The detailed design for this project is retained below because it is
already far enough along to serve as both backlog and implementation
documentation.

### Goal

Keep a permanent, searchable Account-level record of how each Audit
Review ended. Later, connect that history to appeals, which currently
happen outside Salesforce.

For this project, a completely favorable result means a Certificate,
Conditional Certification, or Part-B jobsite audit. The other outcomes
to distinguish are Drop, Additional Audit Needed, and Cautionary Letter.

### Completed export work

The `audit-review-outcomes` command creates a read-only CSV containing
Audit Reviews created during the rolling two-year window. It includes:

-   Audit Review display name (for example, `AR-000123`) and created
    date
-   Account name and Salesforce Account ID
-   CRG Outcome and CRG Comments
-   Six mutually exclusive review columns: `standard`, `drop`,
    `additional`, `cautionary_letter`, `scope`, and
    `needs_manual_review`

### Agreed matching rules

The exporter searches both CRG fields without caring about
capitalization.

-   `CRG Withdrawal` is always `drop`.
-   `Certificate Recommended`, `Certification Recommended`, and
    `Certificate   Processed` are `standard` unless comments explicitly
    describe a scope change or mention a Cautionary Letter.
-   `CRG Follow Up Needed` is always non-standard. A `drop` or
    `withdraw` term is `drop` unless a certification code appears, in
    which case it is `scope`.
-   `Closed Without Certificate` is `additional` for an Additional
    Audit, and `standard` for Conditional Certification, Part B, or a
    jobsite audit. It needs manual review when there is no clarifying
    text.
-   `additional` matches `additional`, `add'l`, and `addt'l`. The phrase
    `additional audit`, when no jobsite is mentioned, takes precedence
    over a certification code; `full scope additional audit` is not a
    scope change.
-   `cautionary_letter` requires `cautionary`; `letter` alone is
    unclear, and `conditional` prevents a Cautionary Letter match.
-   `scope` recognizes the certification codes in the glossary with
    scope language (`add`, `remove`, `upgrade`, or `scope`).
    `grant <code> only` and an explicit comment that the scope is
    changing also match.
-   A Drop plus Additional result, or a Scope plus Cautionary Letter
    result, requires manual review. Drop or Additional takes precedence
    over a potential Cautionary Letter result.
-   Familiar favorable phrases such as `renew certification`,
    `recommend   renewal`, and `grant certification` are left unflagged.
    Empty CRG text is also unflagged. Other meaningful text is flagged
    as `needs_manual_review`.

The six exported columns are derived from one classification, so no row
has more than one true outcome flag. These are first-pass rules for
manual review, not final outcome decisions.

Run it with:

``` bash
uv run aisc_salesforce audit-review-outcomes
```

By default it creates `audit_review_outcomes.csv`. Choose another
location with `--output PATH`.

### Decisions still needed

-   Confirm whether the two-year window should use Audit Review
    `CreatedDate`, `LastModifiedDate`, a completion date, or the parent
    Audit date.
-   Review false positives and false negatives in the agreed matching
    rules.
-   Agree on stable Account-post wording and hashtag(s). The post should
    include enough information to trace it to the source Audit Review
    without exposing unnecessary detail.
-   Decide how reruns avoid duplicate Account posts and whether a
    correction creates a new post rather than replacing history.

### Later phases

1.  Add an interactive command that guides a reviewer through Audit
    Reviews flagged as `needs_manual_review`. It is documented here only
    and is not part of the current export work.
2.  Use Audit Review field history to help clarify unclear reviews.
3.  Filter out Audit Reviews attached to Audits with New York scope.
4.  Add a preview-only command that shows the exact Account posts it
    would make.
5.  Add an explicit apply step that posts approved, standardized
    language and records an audit trail.
6.  Design appeal intake and status tracking around the Account-level
    outcome history.
7.  Add reporting that connects outcomes, appeals, and final appeal
    results.

No Salesforce posting or appeal automation is part of the current step.

------------------------------------------------------------------------

## 5. Applications, audits, and operational exceptions

**Goal:** Automate recurring exception checks around applications and
audits so staff spend time on real exceptions rather than rebuilding
lists.

### Development sequence

-   [ ] Move the **9-Month Overdue for Audit** report from Tableau Prep
    to Python.
-   [ ] Include `AISC comments` in the overdue logic so known/legitimate
    situations are not chased as false positives.
-   [ ] Fix the bug where Initial/New applicants needing immediate
    modifications appear on two processing lists.
-   [ ] Build an automated acknowledgement/receipt email for participant
    withdrawal requests, with a record that it was sent.
-   [ ] Add an exception report for active companies missing audit
    packages.
-   [ ] Track audits canceled for non-payment but later allowed to pay,
    including invoice transitions such as sent → canceled → sent.
-   [ ] Review whether these exception checks belong in the overnight
    staging process and morning TUI once their rules are stable.

------------------------------------------------------------------------

## 6. Salesforce ↔ iMIS reconciliation and membership data

**Goal:** Make cross-system differences visible and deliberate without
relying on a fragile direct synchronization.

### Development sequence

-   [ ] Document the fields/business rules that legitimately exist in
    both Salesforce and iMIS.
-   [ ] Build a weekly discrepancy report comparing those fields across
    the two systems.
-   [ ] Where possible, identify which system has the newer value rather
    than assuming one system always wins.
-   [ ] Add exception handling for known sync problems such as job
    history, leading-zero IDs, and deletions.
-   [ ] Add Membership notification/output for Contact and Account
    changes that need to be reflected in iMIS.
-   [ ] Automate the Salesforce Membership Discount flag when a
    participant becomes a full member in iMIS.
-   [ ] Automate removal of that flag when the membership is purged,
    with preview/reconciliation before write-back.
-   [ ] Build/maintain an iMIS data dictionary sufficient to support
    reliable queries and reconciliation.
-   [ ] Evaluate direct iMIS API/database access only after the
    read-only reconciliation workflow is trustworthy.

------------------------------------------------------------------------

## 7. Board reports, maps, and recurring reporting

**Goal:** Replace manual data assembly with reproducible report
pipelines that pull the source data, preserve the business rules, and
produce reviewable tables/charts/documents.

### Board report pipeline

-   [ ] Capture the current quarterly Board Report inputs, calculations,
    narrative sections, and manual follow-ups as explicit requirements.
-   [ ] Automate the Salesforce data pull.
-   [ ] Automate the iMIS data pull using currently available access.
-   [ ] Standardize intermediate tables so each quarter uses the same
    structure.
-   [ ] Add validation/reconciliation checks before report generation.
-   [ ] Generate the report tables and charts from the validated data.
-   [ ] Generate cleaner report-ready output from Markdown rather than
    assembling it manually in Google Sheets.
-   [ ] Keep the report generator review-first: source data and
    generated narrative/tables should be inspectable before publication.

### Certification Search Map

-   [ ] Rebuild the data preparation outside Tableau.
-   [ ] Use Salesforce `sort order` to display certifications in the
    intended sequence.
-   [ ] Replace the CCE endorsement representation with CCC
    certification where required.
-   [ ] Add facility addresses.
-   [ ] Clean geographic edge cases such as U.S. Virgin Islands so
    mapping is reliable.
-   [ ] Rebuild/publish the map from the cleaned data.

### Other recurring reports/dashboards

-   [ ] **Applications dashboard:** track the application lifecycle,
    expedited applications, membership start dates, and data needed to
    evaluate whether pre-assessments shorten timelines.
-   [ ] **Case Progression dashboard:** track case volume, duration,
    handlers, and topics.
-   [ ] **Exception Tracking dashboards:** surface operational
    exceptions that need management attention.
-   [ ] Automate the iMIS pull for Janet Cummings' annual Board of
    Directors map.
-   [ ] Add Domestic vs. International new-applicant counts to a
    permanent report/dashboard rather than answering the request
    manually each time.

------------------------------------------------------------------------

## 8. Case and follow-up utilities

**Goal:** Remove small recurring case-management chores that consume
attention and are easy to automate safely.

### Development sequence

-   [ ] Prepend dates to new Case subjects so similar Cases are easier
    to identify and merge.
-   [ ] Review the existing legacy case-subject renaming command and
    extend/reuse it rather than creating overlapping logic.
-   [ ] Build an automatic weekly export of Salesforce feed items
    created by me for follow-up review.
-   [ ] Add routing checks for Cases owned by inactive Users or
    inappropriate queues where that is not already handled by the
    data-integrity project.
-   [ ] Standardize script-created Case/Contact notes and include
    operator attribution.
-   [ ] Audit the existing `aisc-sf` commands for duplicated business
    rules that should be shared utilities.

------------------------------------------------------------------------

## 9. Snapshots and reusable data foundation

**Goal:** Make recurring automation and reporting depend on consistent,
testable data access rather than one-off extracts.

### Development sequence

-   [ ] Inventory the snapshots currently created manually or by
    existing scripts.
-   [ ] Decide which snapshots are durable historical records versus
    rebuildable derived data.
-   [ ] Standardize snapshot naming, dates, storage, and schemas.
-   [ ] Add validation so a bad/partial pull cannot silently replace a
    good snapshot.
-   [ ] Add the remaining useful recurring snapshots to overnight
    execution.
-   [ ] Reuse shared query/data-access functions across cleanup, Profile
    Updates, reports, and ad-hoc tools.
-   [ ] Keep enum/picklist catalogs and focused tests synchronized with
    Salesforce configuration changes.

------------------------------------------------------------------------

## 10. Ad-hoc data request tool

**Goal:** Make one-off information requests faster without allowing
natural-language ambiguity to turn directly into unsafe or inaccurate
Salesforce queries.

### Development sequence

-   [ ] Identify the Salesforce/iMIS tables and relationships most
    commonly needed for ad-hoc requests.
-   [ ] Build a reusable linked data pull that loads only the relevant
    tables/fields.
-   [ ] Add a small library of validated query patterns for common
    questions.
-   [ ] Add natural-language-to-query assistance that shows the
    interpreted filters, joins, and fields before execution.
-   [ ] Return a clean table as the default output.
-   [ ] Add charts only when the request benefits from them.
-   [ ] Preserve the generated query/logic with the output so results
    can be reproduced.
-   [ ] Add guardrails separating read-only analysis from any tool that
    can write to Salesforce.

------------------------------------------------------------------------

## 11. Nextiva call tracking

**Goal:** Replace the slow Google Sheets parsing workflow with a
maintainable Python process.

### Development sequence

-   [ ] Finish translating the existing daily Nextiva parsing steps into
    Python.
-   [ ] Validate Python output against the current Sheets process for a
    representative period.
-   [ ] Automate the recurring input/output file handling.
-   [ ] Add clear error reporting for malformed or missing data.
-   [ ] Once the base process is stable, add the useful graphs directly
    to the output.

------------------------------------------------------------------------

## 12. Architecture, safety, and maintainability improvements

These are cross-project improvements. Prefer adding them while touching
the relevant feature rather than turning all of them into a separate
refactor project.

-   [ ] Every Salesforce-writing workflow should have a
    read/preview/review path before apply where practical.
-   [ ] Make write operations idempotent or otherwise safe to rerun.
-   [ ] Record enough audit information to explain what changed, when,
    why, and under whose operation.
-   [ ] Centralize shared Account/Contact/Case matching and
    normalization rules instead of duplicating them across commands.
-   [ ] Keep tests around business rules that can cause
    participant-facing or record-integrity problems.
-   [ ] Prefer exception queues over silently guessing when matching is
    ambiguous.
-   [ ] Audit scripts for opportunities to add standardized operator
    attribution and consistent notes.
-   [ ] Keep durable source data separate from rebuildable derived
    outputs.
-   [ ] Use atomic writes and clear success/failure logging for
    generated local files.
-   [ ] Add automated tests in GitHub Actions when the local test suite
    is stable enough to make CI useful.

------------------------------------------------------------------------

## Someday / maybe

These are intentionally outside the normal "pick the next development
item" path.

-   [ ] Access Dodge Construction Services data through its API.
-   [ ] Expand direct iMIS API/database access beyond the specific
    read-only needs above.
-   [ ] Build a report identifying possible consultants being used as
    Certification contacts and mismatched email addresses.
-   [ ] Explore replacing selected Salesforce functionality with a
    standalone application; security design is a prerequisite.
-   [ ] Explore replacing selected iMIS functionality only after the
    Salesforce replacement question is mature.
-   [ ] Expand the ad-hoc data tool into a broader internal data
    application if the smaller version proves useful.

------------------------------------------------------------------------

## How to use this file

When development time opens up:

1.  Continue an **in-progress** project before starting another one when
    possible.
2.  Within that project, choose the first unchecked item whose
    prerequisites are already satisfied.
3.  Turn that item into a GitHub issue with acceptance criteria, tests,
    and explicit Salesforce write/no-write behavior.
4.  If implementation reveals a new dependency or business-rule
    question, add it here in the correct place instead of letting it
    disappear into chat or an issue comment.
5.  Mark completed items here so the next useful feature remains
    visible.
