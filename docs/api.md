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

## CLI

::: aisc_salesforce.app
    options:
      show_root_heading: true
      members:
        - main
        - get_profile_update_configuration
