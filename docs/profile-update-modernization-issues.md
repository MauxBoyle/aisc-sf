# Profile Update modernization: proposed issues

This is a reviewable list of proposed GitHub issues, not an implementation
plan or a record of issues already created. The [discovery record](profile-update-modernization-discovery.md)
contains the operator rules and current-code findings behind these proposals.
The [older project plan](project-plan-revised.md#1-profile-updates-contacts-and-portal-access)
contains the wider backlog. Phase 1 makes routine decisions safe in the
existing workflow. Phase 2 makes that workflow portable, adds a visible work
trail, and completes the Garrett browser handoff. Phase 3 keeps useful work
visible after that handoff. Phase 4 is a place to capture new problems and
ideas while the workflow is in use.

## Phase 1: safe routine processing

The order below starts with read-only evidence and then adds reviewed actions.
Each issue should include focused tests and updated operator instructions when
implemented. The browser handoff follows in Phase 2.

1. **Map the current workflow and verify Salesforce Flow responsibilities.**
   Record when the Python workflow changes the Account, Contact, Account role,
   Case, Company Profile Change, Audit, and Portal User, including failures and
   retries. Check which related Audit fields Salesforce Flows update and when.
   Create a Python Audit-write issue only for a demonstrated gap with a named
   trigger, Audit, and field. Use a real or sandbox example to check the map.
2. **Show a read-only, role-by-role comparison before detailed review.** Show
   every submitted role beside its current Salesforce role holder, even when
   one person fills several roles. Show the matched Contact separately when it
   differs from the role holder. Include submitted Account, Key Data, Comments,
   and Other Personnel Notes. Mark each displayed value as submitted,
   supplemented, or current Salesforce data at field level; do not turn a
   supplemented value into a proposed write.
3. **Surface early warnings from every source submission.** Detect incomplete
   identities, two submitted names using one email, email-domain changes,
   unexpected or newly created submitters, and conflicts hidden by the combined
   CSV row. Explain the source submission and reason for each warning before
   the reviewer reaches a write decision. Require explicit verification of a
   new or unexpected submitter before linking a Contact or Case.
4. **Separate Garrett's routine decisions from operator handoffs.** Let Garrett
   decide clear Contact and role changes. Require an explicit operator handoff
   for company name, facility address, ownership, missing last name or email,
   conflicting submitted names on one email, email-domain changes, and Portal
   User provisioning failures. Preserve safe work already completed on a
   complicated Case and record the handoff reason. Before applying a Contact
   or role change, show its proposed effects and whether Portal User access
   will be created, reused, or deferred, subject to a fresh final check.
5. **Set the Case Contact from the most recent submitter and alert the email
   reviewer.** Choose the latest source submission by creation time, with a
   deterministic tie break, and store which submission supplied the Case
   Contact. When a Case has multiple submitters, show their distinct names and
   email addresses at the participant-email step and tell the reviewer to
   include all parties. The script still prepares email text for a person to
   send.
6. **Track every Comment and Other Personnel Note to an outcome.** Show each
   nonblank source item as a separate request. Let the reviewer mark it
   addressed or handed off, with a brief recorded result. Do not close the
   Company Profile Change while one is unaccounted for. Include relevant
   results and pending requests in participant wording.
7. **Handle employee departures and shared mailboxes safely.** Search for a
   departing Contact's roles on every Salesforce Account before removal and
   route remaining-role cases to the operator. For a generic mailbox, retain
   the Contact and Portal User and allow Garrett to rename the Contact when all
   roles stay within one company family. Use the supplied replacement name or
   the documented company-name/mailbox-purpose fallback. Escalate conflicting
   submitted names or roles outside the family. Explain replacements clearly
   in the participant response.
8. **Give blank submissions a clarification path.** Check Account History for
   a related self-service change attributed to Susan Thomas within about six
   hours of submission, including across midnight. If found, acknowledge the
   change; otherwise present the operator's clarification text. Record the
   selected outcome and email confirmation.
9. **Make response drafts and record closure safe on retries.** Persist the
   original role holders and applied changes needed for response wording so a
   restart does not lose them when fresh Salesforce values become no-ops. State
   completed changes and specific pending requests. Track participant email,
   Portal User provisioning, Company Profile Change closure, and Case closure
   separately: the Profile Change can close with an open Case, and the Case
   closes after the final participant answer. A choice not to send a partial
   email must not silently decide the other statuses.
10. **Protect company-name and facility-address changes.** Keep these changes
    with the operator, validate partial or implausible addresses, and prevent
    a future-dated change from being applied early. Surface combined moves with
    company-name or Certification/Principal Contact changes for closer review.
    Record the decision and effective date.
11. **Prepare a useful manager handoff summary.** Include completed changes,
    pending decisions, Key Update answers, move context and driving time when
    relevant, and the Audit timeline defined in discovery. Label canceled or
    withdrawn future Audits as context, omit `SA-NYC` from the future list, and
    include the last completed Audit under the documented conditions. Suggest
    a next step only when straightforward. Leave the Case under the operator's
    ownership after handoff.
12. **Record future-change and temporary-notation follow-up.** Prompt the
    operator to create a Notion task linked to the Case for a future effective
    date. Record when `f.k.a.` and `(pending address change)` were used. Remove
    `f.k.a.` after the first subsequent Audit Review moves from In Progress to
    Completed; remind the operator to remove `(pending address change)` shortly
    after the scheduled auditor visit. The Case can close after the participant
    gets the plan even while the separate cleanup task remains.

## Phase 2: portability and browser handoff

Complete the work below before asking Garrett to process live routine Cases
through a browser. Keep the CLI usable while moving review decisions into
shared Python services.

1. **Make each review decision resumable outside the CLI.** Expose one pending
   question, its context, allowed answers, and the next state without waiting
   for terminal input. Persist the question and outcome with the existing queue
   and audit so refreshes, restarts, and duplicate submissions cannot skip a
   decision or repeat a Salesforce write. Prevent two reviewers from applying
   decisions to the same Case at once. Keep the same validations, fresh
   Salesforce checks, and retry behavior for CLI and browser users.
2. **Set up portable hosting, storage, and reviewer identity.** Select a
   staff-only host, identity provider, secrets store, and durable
   access-controlled home for session, queue, audit, and response artifacts.
   Define Garrett and operator permissions before building the browser pages.
   Decide whether Salesforce writes can use each reviewer's own Salesforce
   authorization; otherwise use a dedicated, limited integration user and
   record the verified human actor separately. Define how CLI actions get a
   trustworthy actor too; mark actions with no verified human as such. Do not
   use the owner's personal Salesforce identity as an unexplained shared actor.
   Verify this design in a sandbox, including how Salesforce shows the native
   author of a write.
3. **Create a clear Salesforce Notes and Chatter work trail.** Define which
   Case events need a durable Note and which need a brief Chatter update:
   review claimed, resumed, transferred, or released; important decisions and
   completed changes; pending work; and closure. Show the current reviewer
   separately from the Case owner, which stays with the operator. Record the
   verified actor, time, outcome, and relevant Case or Profile Update
   reference, including work done through the CLI. Make posts retry-safe and
   avoid duplicate or misleading messages; replace the existing hard-coded
   `-MB` Chatter signature. When a shared Salesforce account writes a post,
   state the verified human actor in its content and audit, and check that the
   displayed Salesforce author still reflects the run-as account. When a
   Contact's name, email, or phone number changes, create a Salesforce `Note`
   record attached to that Contact; ignore trivial phone formatting changes.
   The Note title must identify what changed. Its content must include the old
   and new information, the source submission's submitted date rather than the
   processing date, and the name of the person processing the update.
4. **Build and deploy a browser review workflow for Garrett.** Evaluate
   Streamlit as the first UI option. Give Garrett a URL where he can start or
   resume a staging session, inspect the role-by-role comparison and source
   warnings, make permitted decisions, see a fresh write preview, read
   prepared participant text, and hand complicated work to the operator.
   Reuse the shared Python review rules, queue, audit, identity, and work
   trail. Verify the full workflow through the browser, including interrupted
   and retried sessions. Document how Garrett opens and uses it without a
   command line.
5. **Run the Garrett handoff against realistic examples.** Walk through a
   routine submission, a person in two roles, multiple submitters on one
   Case, a blank submission, a generic mailbox, a departure, and an
   operator-only change. Confirm that warnings, prepared messages, handoffs,
   persisted decisions, Notes and Chatter attribution, and closure match the
   discovery rules in the browser. Confirm Garrett can complete routine work
   without using commands. Capture a tricky anonymized example to test
   assumptions before declaring the handoff ready.

## Phase 3: improvements retained for later

1. **Send an auditor-facing move notice.** After the operator confirms a real
   move, prepare or send the documented early notice to auditors already
   attached to relevant upcoming Audits. Include the old/new addresses,
   effective-date guidance, Audit date and number, and approval status. Settle
   sender and Certification reply-to behavior before implementation.
2. **Evaluate a delayed Portal-access email.** Determine whether an email about
   an hour after provisioning can be reliable and retry-safe; implement only
   if the answer is yes.
3. **Flag employee-count changes that affect audit days or pricing.** Show the
   changed count and required follow-up without guessing an outcome.
4. **Finish remaining normalization and validation gaps.** Compare current
   email, phone, whitespace, and name handling with the older plan; identify
   remaining typo, URL, and capitalization-exception needs before changing
   existing rules.
5. **Review dropped/replacement Account parenting.** Define and validate the
   desired family relationship when a Profile Update changes Account
   structure.
6. **Notify Membership of relevant changes.** Define which Contact or Account
   fields matter to iMIS and how staff should receive a reliable notice. This
   is a notification task, not general Salesforce/iMIS synchronization.
7. **Standardize Contact notes and email-type classification.** Replace the
   inconsistent free-text cases identified in the older backlog with defined
   values and review behavior.
8. **Replace remaining master-sheet steps.** Inventory any manual CSV
   master-sheet work still required and move it into the reviewed Python
   workflow where useful. The session CSV can remain as an inspection artifact.
9. **Revisit the User deactivation schedule.** Monthly deactivation is enough
    for the initial handoff; consider the proposed weekly script later.

## Phase 4: problems and ideas to investigate

Use this section to capture issues noticed while processing Profile Updates,
including cases where the right automated response is not yet clear. For each
entry, record the source submission or update, what happened, and what needs
investigation. Add a proposed issue to an earlier phase or the older-plan
coverage only after its scope is understood.

1. **Investigate conflicting contact details across submitted roles.** In one
   processed update, the contact input for two roles had these details:
   `Brett Venzke, brett.venzke@doralcorp.net` and `Bretty Venzke,
   bret.venzke@doralcorp.net`. Both the name and email differed by a small
   amount. Capture how the submissions were identified and linked to Contacts,
   then determine what evidence could flag this safely for review without
   automatically merging or changing either Contact. The automated response
   is not yet defined.

2. **Only change to less formal names in role submissions.** Example: If the 
   role is currently Deborah, and the name submitted for the role is Deb, 
   make the change. However, if the submitter enters her name as Deb, that is
   not an official change, so don't change from Deborah to Deb based on it.

3. **Consolidate email response to multiple submitters** If two different 
   people submit updates and they can be reconciled and applied, create one 
   combined email response and alert the user to copy both submitters on one
   email.

4. **Maintain a list of acceptable capitalization variants** When a submission
   comes through with non-standard capitalization, we should have the option
   to correct it but also the option to add it to the list of allowed 
   exceptions. IRONFAB, LLC, for example, uses all caps in their branding.

5. **Show address changes as one change, not individual changes** Currently, the
   email response shows the street change, city change, state, zip all as separate
   updates. It should show a full address replacing a full address, filling in the
   unchanged parts as needed.

6. **Always include automatically applied changes in response email** When the
   submission is empty, the script should check Account History for automatically
   applied changes. It should also check even when there are changes that the 
   user has to approve. Those should be included in the response email.

7. **Fine tune partial submissions** New example. A full contact was submitted for
   two of the roles and that First and Last Name were submitted for another role.
   The script interpreted that to update the non-generic contact in that role, but
   I take that to mean that the new contact should also be in that role. If the
   intention is not clear, ask the user which assumption to make.  (In case I need 
   to refresh my memory, this is Case 361475.)

8. **Check for Sole Contact after update** If the profile submission leads to
   there being the same person in all filled roles, the response email should
   include an admonition referencing section 1.1.2 of the Governing Requirements.

9. **Email typo check needs more information** in a12Qj000005TjGb, the script flagged
   an email typo: Likely email typo: dalesley@steel-works.com was compared as 
   dalesley@steel-works.com. The suggested correction is dlesley@steel-works.com 
   (dlesley@steel-works.com). I thought this made sense, but now I see that 
   dlesley@ is donnie lesley and dalesley@ is daniel lesley and a second letter
   of the first name was needed to distinguish the two. I wouldn't expect the
   script to necessarily catch that, but the user needs to be given more
   information.

Add future entries with the submission/update context, observed problem or
idea, and the question that needs investigation.

## Older-plan coverage

| Older backlog item | Proposed location |
| --- | --- |
| End-to-end workflow inventory | Phase 1, issue 1 |
| Garrett-facing browser workflow | Phase 2, issues 1, 2, 4, and 5 |
| Case submitter attribution | Phase 1, issue 5 |
| Prompt related Audit update | Phase 1, issue 1 verifies Salesforce Flows first |
| Portal-access path for Applicant and Certified Accounts | Current documented behavior; verify in Phase 1, issue 1, and route failures in issue 4 |
| Delayed portal-access email | Phase 3, issue 2 |
| Clear Contact/User decisions | Phase 1, issues 2–4 |
| Unexpected submitters | Phase 1, issue 3 |
| Address validation | Phase 1, issue 10 |
| Employee-count follow-up | Phase 3, issue 3 |
| Normalization and capitalization exceptions | Current partial support; Phase 3, issue 4, for remaining gaps |
| Dropped/replacement Account parenting | Phase 3, issue 5 |
| Address follow-up and auditor notice | Phase 1, issues 10 and 12; Phase 3, issue 1 |
| Membership notification | Phase 3, issue 6 |
| Contact notes and email type | Phase 3, issue 7 |
| Operator attribution and Salesforce work trail | Phase 2, issues 2 and 3 |
| Replace CSV master-sheet steps | Phase 3, issue 8 |

The discovery record also calls for a tricky anonymized example. Audit Flow
responsibility, auditor-message sender, Phase 2 hosting, and Salesforce actor
identity still need decisions; they do not block drafting the other issues.
