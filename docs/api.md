# API Reference

## Application snapshot

::: aisc_salesforce.application_snapshot
    options:
      show_root_heading: true
      members:
        - RECORD_TYPE_ALIASES
        - APPLICATION_RECORD_TYPE_IDS
        - APPLICATION_STAGES
        - ApplicationSnapshotError
        - ApplicationSnapshotResult
        - ApplicationSnapshotService
        - is_qualifying_case
        - is_valid_audit
        - select_latest_valid_audits
        - application_stage
        - classify_application_type
        - aggregate_application_snapshot
        - write_application_snapshot

`ApplicationSnapshotService.build()` performs the two paginated Salesforce
queries and returns a frozen `ApplicationSnapshotResult`. The transformation
helpers are separate from network and file access, which makes each business
rule directly testable. `write_application_snapshot()` publishes the CSV
atomically in a timestamped directory.

## Picklist enum audit

::: aisc_salesforce.picklist_audit
    options:
      show_root_heading: true
      members:
        - PicklistAuditError
        - PicklistAuditFinding
        - PicklistAuditResult
        - PicklistEnumAuditService
        - audit_date_field
        - two_year_cutoff

`PicklistEnumAuditService` describes inventoried Salesforce fields, reads
recently stored picklist values, and compares them with the Python enum
catalog. The audit is read-only; its `PicklistAuditResult` contains the cutoff
and any missing or uncataloged values.

## iMIS contact consolidation

::: aisc_salesforce.imis_contacts
    options:
      show_root_heading: true
      members:
        - CONTACT_BASIC_COLUMNS
        - ConsolidationResult
        - ContactConsolidationError
        - consolidate_contactbasic

`consolidate_contactbasic()` accepts a directory and optional output function.
It returns the selected input paths, published output paths, and row counts in
a frozen `ConsolidationResult`. Discovery and validation failures use the
workflow-specific `ContactConsolidationError`, which the CLI turns into exit
code `1` with a readable message.

## Profile Update service

::: aisc_salesforce.profile_updates
    options:
      show_root_heading: true
      members:
        - AutomationCounts
        - ProfileUpdateService
        - build_submission_summary
        - has_meaningful_explanation
        - is_eligible_audit
        - match_contact

## Profile Update Case subjects

::: aisc_salesforce.profile_update_subjects
    options:
      show_root_heading: true
      members:
        - ProfileUpdateReference
        - AiscProfileUpdateSubject
        - build_aisc_profile_update_subject
        - parse_aisc_profile_update_subject
        - parse_legacy_profile_update_subject
        - is_received_profile_update_subject
        - subject_has_profile_update
        - append_profile_update
        - validate_subject_length

The frozen data classes model an Account name plus ordered Profile Update/date
pairs. Both recurring automation and staging use these helpers, so identifier
matching and subject-length validation have one shared implementation.

## Required participant Profile rules

::: aisc_salesforce.required_profile_rules
    options:
      show_root_heading: true
      members:
        - NOT_ELIGIBLE_SKIP_REASON
        - AccountRoleAssignment
        - RequiredProfileDecision
        - determine_required_profile

This pure rule module accepts already-known Account-role assignments and never
reads from or writes to Salesforce. Its frozen decision preserves the ordered,
deduplicated assignments that caused the selected Profile.

## Legacy Case subject correction

::: aisc_salesforce.rename_profile_update_cases
    options:
      show_root_heading: true
      members:
        - RenameCounts
        - RenameProfileUpdateCasesService
        - correction_window

`correction_window` converts seven `America/Chicago` local dates to UTC query
bounds. `RenameProfileUpdateCasesService.run()` defaults to preview mode. Apply
mode updates only the Case `Subject`, catches individual Salesforce write
failures, and continues through the queried batch.

## Salesforce client

::: aisc_salesforce.salesforce
    options:
      show_root_heading: true
      members:
        - SalesforceClient
        - SalesforceError
        - SalesforceSession
        - get_credentials
        - get_oauth_url
        - request_access_token

`SalesforceClient` supports paginated filtered queries, record creation,
updates and retrieval, plus reading and posting record-feed Chatter messages.
Chatter posts use the Connect REST API's
[feed element format](https://developer.salesforce.com/docs/platform/connect-rest-api/guide/features_feeds_feed_elements.html)
with the target record supplied as `subjectId`.

## Profile Update staging service

::: aisc_salesforce.stage_profile_updates
    options:
      show_root_heading: true
      members:
        - StagingResult
        - ProfileUpdateStagingService
        - write_staged_profile_updates

`ProfileUpdateStagingService` only reads Salesforce data. The writer publishes
the resulting rows atomically in a timestamped directory. Rows include
blocking-safe Case match fields plus Key Update presence and earliest-date
metadata. Passing `submission_ids` to `stage()` scopes a session refresh to
those exact captured records; omitting it preserves the legacy all-New query.

## Contact resolution

::: aisc_salesforce.contact_resolution
    options:
      show_root_heading: true
      members:
        - ContactResolutionClassification
        - ContactSource
        - ContactResolution
        - normalize_email
        - family_account_ids
        - name_local_part_patterns
        - is_single_edit_or_transposition
        - resolve_contact

The Contact-resolution helpers contain the deterministic matching rules used
by both staging and interactive review. They do not access Salesforce directly,
which keeps normalization, family matching, and likely-typo behavior easy to
test.

## Interactive Profile Update processing

::: aisc_salesforce.review_queue
    options:
      show_root_heading: true
      members:
        - QueueStatus
        - QueuePhase
        - QueueWarning
        - QueueBlocker
        - SalesforceReference
        - ProposedChange
        - StagedRow
        - CaseBatch
        - ReviewQueueManifest
        - ReviewQueueStore
        - stable_queue_id
        - build_review_queue
        - recompute_manifest
        - transition_item
        - manifest_json
        - read_review_queue
        - write_review_queue
        - iter_changes

The queue module is read-only during discovery and uses frozen dataclasses for
its public model. UUID5 IDs and explicit ordering rules make identical input
serialize to identical JSON bytes. `ReviewQueueStore` atomically replaces the
published snapshot around transitions and preserves stable outcomes when setup
refreshes Account or Case references. `read_review_queue()` performs strict,
typed schema and field validation. `ReviewQueueStore.resume()` retains
completed and blocked outcomes while resetting interrupted statuses to pending.

::: aisc_salesforce.review_ui
    options:
      show_root_heading: true
      members:
        - TextFragment
        - ValueOrigin
        - ValueFragment
        - StyledFragment
        - StyledText
        - ReviewChoice
        - Heading
        - Notice
        - WarningNotice
        - ValidationFeedback
        - ContextLine
        - ScalarComparison
        - MappingComparisonRow
        - MappingComparison
        - ContactCard
        - ContactComparisonRow
        - ContactComparison
        - ConflictCandidate
        - ContactFieldConflict
        - ParentAccountChildValue
        - ParentAccountFieldConflict
        - ParentAccountConflict
        - ParentAccountNoActiveChildren
        - StagedRowSummary
        - AccountHistory
        - ResponseEmail
        - ReviewQueueSnapshot
        - ChoiceQuestion
        - FreeTextQuestion
        - AcknowledgementQuestion
        - ChoiceAnswer
        - FreeTextAnswer
        - AcknowledgementAnswer
        - ReviewEvent
        - ReviewQuestion
        - ReviewAnswer
        - ReviewUI
        - UnsupportedReviewInteractionError

The frozen review dataclasses form the supported renderer boundary.
`ValueFragment` distinguishes interpolated domain values from explanatory text,
so a visual UI can style values without parsing sentences. Its
backward-compatible `origin` field defaults to `ValueOrigin.NEUTRAL`;
processors and adapters explicitly mark submitted and script-supplemented
values with `SUBMITTED` and `SUPPLEMENTED`.
`WarningNotice` distinguishes actionable warnings from an ordinary `Notice`,
while `ValidationFeedback` remains a separate retry event. `ChoiceQuestion`
contains only its available `ReviewChoice` objects. Questions and answers have
separate choice, free-text, and acknowledgement shapes; an unknown interaction
or mismatched answer raises `UnsupportedReviewInteractionError` explicitly.

::: aisc_salesforce.cli_review_ui
    options:
      show_root_heading: true
      members:
        - ColorMode
        - CLIReviewUI

`CLIReviewUI` adapts the typed boundary to terminal `input_fn` and `output_fn`
callbacks. It preserves the command's headings, comparisons, Contact tables,
shortcuts, complete phrases, invalid-input retries, and interruption behavior.
Its single Rich theme maps submitted values to bright green, supplemented
values and notes to bright yellow, warnings to bright red, and response bodies
to bright blue. Headings, current Salesforce values, progress, and counts stay
neutral, retaining their existing non-color labels. `ColorMode.AUTO` uses Rich
terminal detection and honors `NO_COLOR`; `NEVER` backs `--no-color`, and
`ALWAYS` supports explicit renderer tests. Custom callbacks default to plain
text. Values are assembled as literal Rich `Text` fragments rather than parsed
markup. See the Rich [Console](https://rich.readthedocs.io/en/stable/console.html)
and [theme style](https://rich.readthedocs.io/en/stable/style.html)
documentation. The adapter contains no Salesforce or audit logic, and saved
`response_emails.txt` content never passes through it or contains ANSI terminal
codes.

::: aisc_salesforce.process_profile_updates
    options:
      show_root_heading: true
      members:
        - ProcessingError
        - ProcessingResult
        - StagingSession
        - ProfileUpdateProcessingWorkflow
        - publish_staging_session
        - load_staging_session
        - ChangeProposal
        - ReviewDecision

`ProfileUpdateProcessingWorkflow.stage()`, `.prepare()`, and `.review()` expose
the three session operations. `run()` composes them in `stage → prepare →
review` order. `publish_staging_session()` makes the CSV and queue visible with
one directory rename; `load_staging_session()` accepts only a generated direct
child ID and checks both artifacts agree before write-capable phases begin.
During `review`, a captured submission with a blank Account is repaired and
verified through a scoped refresh before its ID is included in Case
preparation. The normal staging refresh and interactive review then continue.

`ProfileUpdateService.run(submission_ids=...)` prepares only captured
submissions with Accounts and does not process audit work. Calling `run()`
without IDs retains the legacy eligible-audit plus all-New-submission behavior.
        - ActionResult
        - ActionStatus
        - CaseBatch
        - ProcessingResult
        - ProcessingError
        - ProcessingInterrupted
        - ProcessingStoppedEarly
        - ProfileUpdateProcessingWorkflow
        - InteractiveProfileUpdateProcessor
        - read_staged_profile_updates
        - build_case_batches
        - format_response_emails

`ProfileUpdateProcessingWorkflow` keeps Case preparation, staging publication,
disk validation, and review in a fixed order. Its `output_fn` callback is only
for CLI orchestration progress; review interactions go through the injected
`ReviewUI`. The smaller processing data types and methods keep proposal
construction, reviewer decisions, Salesforce execution, response formatting,
and audit writing separate.

`InteractiveProfileUpdateProcessor` accepts a `ReviewUI` and refetches a target
immediately before each decision. It never renders terminal text or reads
terminal input directly. Submitted Contacts with valid emails are resolved and
reconciled by email identity before Account role assignment; a current role
Contact ID is only a fallback for a partial proposal without an email. Different
emails stay separate even when submitted for the same role, unless identity
review resolves them to the same Salesforce Contact. Reviewer-facing Contact
comparisons use labeled field lines, while the JSON audit retains structured
dictionaries. Parent Account preflight refetches only direct children, filters
them to exact `Certified` and `Initials` statuses, and emits renderer-neutral
conflict or no-active-child events before any batch write. Safe Account and
role-link proposals target each active child; Contact work remains shared and a
new Contact stays owned by the submitted Parent Account. Acknowledged unsafe
batches use the `DEFERRED_MANUAL` action status, a blocked queue state, and leave
the source records and Case open for manual follow-up and retry. The
processor writes `review_audit.jsonl` after every result and
`response_emails.txt` for successful Account changes and completed submitted
roles. Profile Update closure and the Case's final status happen only after the
entire Case batch is resolved. `format_response_emails` keeps Account field
results in their field-level format while combining each submitted Contact role
into one response line; the underlying field decisions remain separate audit
entries.

`ProcessingResult.queue_path` identifies the published `review_queue.json`.
`ProcessingResult.stopped_early` distinguishes a deliberate `Q`/`Quit` from a
failure or keyboard interruption. A deliberate stop is handled inside
`review()`: it writes a `stopped early` batch event, keeps the current Case
Pending, and returns normally so the CLI can use exit code `0`.

Single-record reads and writes continue to use the Salesforce REST sObject Rows
API style, while Contact matching and Account History lookup use SOQL. See the
[Salesforce API overview](https://developer.salesforce.com/blogs/2024/04/accessing-object-data-with-salesforce-platform-apis)
and [record-update guidance](https://developer.salesforce.com/docs/marketing/marketing-cloud-growth/guide/mc-manage-objects-update-rest.html).

## CLI

::: aisc_salesforce.app
    options:
      show_root_heading: true
      members:
        - main
        - get_profile_update_configuration
