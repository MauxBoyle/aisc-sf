# Profile Update write and Flow map (#115)

Checked 2026-10-27 against the Python source, focused mocked-client tests, and
read-only production Salesforce Tooling and data queries. Record details below
are anonymized. This map covers the `process-profile-updates` workflow, including
its Case-preparation service. `review_audit.jsonl` is a local decision log; it is
not a Salesforce `Cert_Audit__c` write.

## Python writes and retries

Writes are separate Salesforce API requests, not one transaction. A successful
earlier request remains saved if a later request fails. The queue and local
decision log record progress, but retry still rereads Salesforce values.

| Record | Write point and fields | Failure and retry outcome |
| --- | --- | --- |
| Company Profile Change (`Company_Profile_Change__c`) | Before Case preparation, a New submission with no Account can get `Account__c` after the reviewer chooses a Certification ID match. At the end of review, each source gets `Status__c = Closed` after the response prompt and, when all responses were sent, any required Portal User step. A reported unsent response still closes the source. | Account repair failure stops setup; refresh checks the exact captured submissions. A failed close leaves that source New and the Case Pending, so a retry can select it. Earlier Account/Contact writes remain. Sources close one by one, so a later close can leave earlier sources Closed. |
| Case | Case preparation creates a Case or updates/reuses one, including `Subject`, `AccountId`, `ContactId`, owner and routing fields in the prepared payload; an expected Case may be reopened Pending. Review first sets `Status = Pending`, may set `ContactId` when it creates a submitter Contact, and finally sets `Status = Closed` if responses were sent or `Pending` otherwise. | Case setup failure stops before detailed review. A review failure attempts to keep the Case Pending; that attempt can fail too and is logged. A failed `ContactId` write stops review. A failed final Case close occurs **after** source submissions close. Those Closed submissions are omitted from the normal New-only staging query, so the operator must inspect and resolve that Case explicitly; rerunning normal staging is not a guaranteed recovery. |
| Contact | After fresh submission and Contact reconciliation, approved `FirstName`, `LastName`, `Title`, `Email`, and `Phone` changes are grouped into one update per existing Contact or one create with `AccountId`. A new Contact requires an approved last name. A newly created submitter can then be linked to the Case. | Rejected and already-current fields make no write. A Contact error logs failed fields and stops before source closure; retry rechecks current values and can turn a successful earlier write into a no-op. A duplicate-rule create can enter the manual/existing-Contact recovery choices. Other partial writes stay saved. |
| Account fields | After Contact work, each approved company name, owner, or billing-address field is written separately to the selected Account. Parent routing can target active direct children; a routing conflict blocks the batch before its first review write. | A failed field write stops review, logs failure, and attempts Case Pending. Retry fetches the field and skips an already-current value. Earlier Contact or Account fields remain saved. |
| Account role lookups | After Contact reconciliation and Account field review, each approved role writes its lookup on the target Account: Certification, Principal, Accounting, Marketing, or Safety. Contact field writes precede role links. | A failed lookup stops review and leaves source New if not yet finalized. Retry refetches each role lookup; an already-current link is a no-op. Rejected/manual decisions do not make a Python role write; manual changes must be verified from Salesforce. |
| Portal User (`User`) | Only after all response emails are confirmed sent, the provisioning service checks role, Account, profile, license, capacity, and existing active User, then creates a missing active User linked to its Contact. Applicant-only access can be acknowledged and deferred. | Missing configuration, failed preflight, or failed create logs a provisioning failure, leaves sources New, and attempts Case Pending. Retry rechecks for an active linked User and reuses one found, including a User created during a prior partial run. No User update or delete is part of this workflow. |
| Salesforce Audit (`Cert_Audit__c`) | **No Python field write in this workflow.** The separate Case-preparation service reads eligible Audits, may create/reopen a Case, and may post a Chatter message to an Audit. | A Chatter post is not an Audit field update. A failed Case/Chatter operation is counted in setup; the Case service checks for existing Case subjects and feed messages on retry. |

The field-specific Account and role decision path is `_review_proposal` in
`src/aisc_salesforce/process_profile_updates.py`. That file also contains
Contact grouping in `_execute_automatic_contact_work` and final status ordering
in `_finish_batch`. Case preparation is in
`src/aisc_salesforce/profile_updates.py`, and Portal User creation is in
`src/aisc_salesforce/participant_user_provisioning.py`. Tests in
`tests/test_process_profile_updates.py` cover these paths, including
source-close and Case-close failures.

## Active Salesforce Flow configuration

The read-only Tooling API query selected `Flow` records with `Status = Active`,
then read each active version's `Metadata.start`, lookups, and updates. This is
an API counterpart to checking object, trigger, timing, and active version in
[Salesforce Flow Trigger Explorer](https://help.salesforce.com/s/articleView?id=platform.flow_trigger_explorer.htm&language=en_US&type=5).

| Active Flow version | Trigger and configured target | Example evidence |
| --- | --- | --- |
| `Update_Audit_Contact_1` v1 | Account create/update, after save, when `Cert_Certification_Contact__c` changes. Its update element targets `Cert_Contact__c` on the Account's `Audits__r` collection, with no date or Audit-type filter in metadata. | No field-history or transaction trace proves this collection update executed for the inspected example. |
| `Cert_or_Principal_Contact_Change` v4 | Account update, after save, when any of Certification, Principal, Accounting, or Marketing role lookups changes. It looks up related Audits whose date is in the future, **or** SA-NYC Audits with a blank date (`1 AND (2 OR (3 AND 4))`). For each returned Audit, its update element assigns `Cert_Contact__c`, `Principal_Contact__c`, `AP_Contact__c`, and `QC_Contact__c` from Account roles. Its separate auditor decision can send a notification. Safety is not a trigger field or target Audit field in this version. | The future Audit example below has the new AP/QC lookup IDs and a matching modification timestamp, but no AP/QC field-history entries. Execution attribution remains unverified. |

Two other active Account after-save versions were inspected:
`AP_Contact_Change` v1 and `Updating_QC_AP_Contact_Changes` v2 target Invoice
work, not Audit fields. `New_Profile_Change_Submitted` v1 runs after Profile
Update creation and has no direct Audit update element. These are not evidence
for an Audit contact write. Active screen, scheduled, and Audit-triggered Flows
with other purposes were excluded from this Account-role map.

## Anonymized production example and limits

An existing Profile Update created **2026-10-21 16:56 UTC** was Closed on its
related Account. Account History records Marketing role change at **2026-10-26
21:39:54 UTC** and Accounting role change at **21:46:20 UTC**; the ID-valued
history rows show different old and new Contacts. A related **Initial** Audit
dated **2026-11-19** now has `QC_Contact__c` and `AP_Contact__c` equal to
those new Account role IDs, not the old ones. Its `LastModifiedDate` is
**2026-10-26 21:46:20 UTC**, the later Account change time. The active v4
Flow's date and Account filters include this Audit, and its target assignments
include both fields. This is a strong, consistent example, but the available
`Cert_Audit__History` rows contain no AP/QC contact-field changes. Current
values and matching timestamps alone cannot attribute the writes to the Flow
or establish which transaction changed each Audit field.

A second Closed Profile Update, created **2026-10-21 18:10 UTC**, had a
Certification role change on **2026-10-26 21:55 UTC**. Its related Renewal
Audit was dated **2026-09-29** and still showed the former Certification
Contact. That past-date Audit is outside the v4 future-date selection. The
v1 collection update has no date filter in metadata, but the available Audit
history does not prove that it ran or changed the past Audit. This observation
does not establish that past Audit contacts are intended to be synchronized.

**Unresolved verification:** obtain read-only Audit field history for
`Cert_Contact__c`, `Principal_Contact__c`, `AP_Contact__c`, and
`QC_Contact__c`, or a Salesforce Flow/debug transaction trace for the example
Account changes. Confirm the intended scope for historical Audits and the
`Audits__r` collection Flow. Until then, the table above describes active
Flow configuration and the example is corroboration, not proof of Flow
execution. No Python Audit-write issue is opened: the evidence does not yet
demonstrate a required target Audit field that both Flows fail to update.
