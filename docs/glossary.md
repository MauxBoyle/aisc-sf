# Glossary

This glossary explains project-specific words used in the documentation and
commands. Salesforce field names appear in `code formatting` when they help
identify the exact data involved.

## A

**Account**

: A Salesforce record for an organization. This project uses Accounts as the
  main record for certification information and for links to Contacts.

**Account role**

: A named job or responsibility associated with an Account. The Profile Update
  workflow supports these roles: Certification, Principal, Accounting, Quality,
  and New York. Each role can point to a Contact through an Account lookup
  field.

**Active Account**

: For Profile Update processing, an Account whose certification status is
  `Certified` or `Initials`. Only active direct child Accounts receive Account
  field changes and Account-role links.

**Application Case**

: A Salesforce Case for a certification application. The
  `application-snapshot` command counts qualifying Application Cases by their
  application stage.

**Application snapshot**

: A read-only CSV report that counts qualifying Application Cases by stage,
  location category, and expedited status.

**Audit**

: In this project, a Salesforce certification-audit record related to an
  Account. Application snapshots use the newest valid Audit to determine some
  reported application stages. This is different from the local review audit
  file.

## C

**Case batch**

: One ordered group of staged Profile Update rows that are reviewed together
  for the same Account and Profile Update Case.

**Certification status**

: The Account value that describes its certification state. `Certified` and
  `Initials` are the statuses considered active by the Profile Update workflow.

**Child Account**

: An Account whose `ParentId` refers to another Account. In this project,
  processing considers only direct children, not grandchildren.

**Comparison key**

: An email value used only for matching Contacts. It is made by trimming and
  lowercasing the email, then removing dots from the part before `@`. The
  original normalized email is kept for display and Salesforce writes.

**Contact resolution**

: The cautious decision about what to do with submitted contact information:
  use an existing Contact, create a new Contact, flag a likely typo, or require
  a reviewer to resolve an ambiguity.

## F

**Family Accounts**

: The Accounts considered together when matching a submitted Contact: the
  target Account, its direct Parent Account, and its direct Sibling Accounts.
  A root Account (one with no `ParentId`) has a family consisting only of
  itself. Family membership helps the project prefer a relevant Contact match;
  it does not automatically change every Account in the family.

## I

**iMIS contact consolidation**

: A command that combines dated `CSContactBasic` CSV exports from iMIS into a
  current combined export while preserving important ID values as text.

## K

**Key Data**

: A Profile Update type whose Salesforce `Type__c` value is exactly `Key Data`.
  A staged group has a Key Update when at least one of its source submissions
  has that type. Merely filling in Key Data fields does not make a submission a
  Key Update.

## P

**Parent Account**

: The Account identified by a child Account's `ParentId`. When two or more
  Active Accounts have the same `ParentId`, the Account with that ID is their
  Parent Account; those Accounts are its child Accounts and are siblings of one
  another.

**Picklist**

: A Salesforce field with a controlled set of allowed values. This project
  keeps the picklist values it uses in Python enums and can audit stored values
  against that catalog.

**Profile Update**

: A submitted Salesforce record containing requested changes to Account or
  Contact information. New Profile Updates are staged before they are reviewed
  or applied.

**Profile Update Case**

: The Salesforce Case prepared to organize one or more related Profile Updates
  for review and follow-up.

## R

**Review queue**

: The `review_queue.json` file created for an interactive Profile Update
  session. It records the ordered work, proposed changes, warnings, blockers,
  and progress so a session can safely resume.

## S

**Sibling Account**

: A direct child Account that has the same `ParentId` as another child Account.
  Siblings share a Parent Account. The project does not treat Accounts at
  deeper levels of a hierarchy as siblings for this workflow.

**Snapshot**

: A timestamped, read-only export of Salesforce data or a report derived from
  it. Snapshots are written locally and do not change Salesforce records.

**Stage / staging**

: The read-only preparation step that groups new Profile Updates, normalizes
  submitted contact details, and writes a CSV for later review. Staging does
  not create or update Salesforce records.

**Staged row**

: One row in the staged Profile Updates CSV. It may represent one submission or
  several compatible submissions grouped by Account and submitter email.

