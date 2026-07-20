# API Reference

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
metadata.

## Interactive Profile Update processing

::: aisc_salesforce.process_profile_updates
    options:
      show_root_heading: true
      members:
        - ChangeProposal
        - ReviewDecision
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
disk validation, and review in a fixed order. Its `output_fn` callback sends
startup progress through the same channel as interactive output. The smaller
processing data types and methods keep proposal construction, reviewer
decisions, Salesforce execution, response formatting, and audit writing
separate.

`InteractiveProfileUpdateProcessor` refetches a target immediately before each
decision. It writes `review_audit.jsonl` after every result and
`response_emails.txt` for successful Account changes and completed submitted
roles. Profile Update closure and the Case's final status happen only after the
entire Case batch is resolved. `format_response_emails` keeps Account field
results in their field-level format while combining each submitted Contact role
into one response line; the underlying field decisions remain separate audit
entries.

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
