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
        - ProfileUpdateProcessingWorkflow
        - InteractiveProfileUpdateProcessor
        - read_staged_profile_updates
        - build_case_batches
        - format_response_emails

`ProfileUpdateProcessingWorkflow` keeps Case preparation, staging publication,
disk validation, and review in a fixed order. The smaller processing data types
keep proposal construction, decisions, execution, response formatting, and
audit writing separate.

`InteractiveProfileUpdateProcessor` refetches a target immediately before each
decision. It writes `review_audit.jsonl` after every result and
`response_emails.txt` for applied or manually verified changes. Profile Update
closure and the Case's final status happen only after the entire Case batch is
resolved.

## CLI

::: aisc_salesforce.app
    options:
      show_root_heading: true
      members:
        - main
        - get_profile_update_configuration
