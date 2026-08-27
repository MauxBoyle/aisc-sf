"""Central inventory of Salesforce fields read by the application."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .account_roles import ACCOUNT_ROLE_DEFINITIONS

# Application snapshot
APPLICATION_CASE_FIELDS = (
    "Id",
    "AccountId",
    "CreatedDate",
    "RecordTypeId",
    "Cert_Stage__c",
    "Cert_Is_this_a_scope_change__c",
    "Cert_Expedited_Application__c",
    "Account.BillingCountry",
    "Account.Cert_Certification_Status__c",
)

APPLICATION_AUDIT_FIELDS = (
    "Id",
    "Cert_Account__c",
    "Cert_Audit_Date__c",
    "CreatedDate",
    "Cert_Audit_Status__c",
    "Cert_Audit_Type__c",
)

# Profile Update automation and staging
PROFILE_AUDIT_FIELDS = (
    "Id",
    "Name",
    "Cert_Audit_Date__c",
    "Company_Profile_Change_Form__c",
    "Explanation_for_Profile_Change_Form__c",
    "Cert_Account__c",
    "Cert_Account__r.Name",
    "Cert_Contact__c",
)

SUBMISSION_FIELDS = (
    "Id",
    "Name",
    "CreatedDate",
    "Status__c",
    "Account__c",
    "Account__r.Name",
    "Email__c",
    "Name__c",
    "Phone__c",
    "Certification_ID__c",
    "Effective_Date__c",
    "Type__c",
    "Revised_Company_Name__c",
    "Revised_Company_Owner__c",
    "Revised_Facility_Street__c",
    "Revised_Facility_City__c",
    "Revised_Facility_State__c",
    "Revised_Facility_Zip__c",
    "Revised_Facility_Country__c",
    "Did_the_Cert_contact_change__c",
    "Did_the_executive_manager_change__c",
    "Will_you_change_personnel__c",
    "Will_QMS_or_documentation_change__c",
    "Existing_equipment_moved_to_new_facility__c",
    "Will_new_equipment_be_purchased__c",
    "Will_old_equipment_be_removed__c",
    "Will_software_change__c",
    *(
        field_name
        for role in ACCOUNT_ROLE_DEFINITIONS
        for _, field_name in role.submitted_fields
    ),
    "Other_Personnel_Notes__c",
    "Comments__c",
)

PROFILE_CASE_FIELDS = (
    "Id",
    "CaseNumber",
    "Subject",
    "Status",
    "IsClosed",
    "CreatedDate",
    "AccountId",
    "ContactId",
    "Origin",
    "Label_new__c",
    "Sub_Label__c",
    "Description",
)

ACCOUNT_FIELDS = (
    "Id",
    "Name",
    "Certification_ID__c",
    "Company_Owner__c",
    "BillingStreet",
    "BillingCity",
    "BillingState",
    "BillingPostalCode",
    "BillingCountry",
    "ParentId",
    "Cert_Certification_Status__c",
    *(role.account_lookup for role in ACCOUNT_ROLE_DEFINITIONS),
)

CONTACT_FIELDS = (
    "Id",
    "AccountId",
    "FirstName",
    "LastName",
    "Title",
    "Email",
    "Phone",
)

STAGING_CASE_FIELDS = (
    "Id",
    "CaseNumber",
    "Subject",
    "Status",
    "CreatedDate",
    "AccountId",
)

# Interactive processing and one-time rename workflow
ACCOUNT_REVIEW_FIELDS = (
    "Id",
    "Name",
    "ParentId",
    "Cert_Certification_Status__c",
    "Company_Owner__c",
    "BillingStreet",
    "BillingCity",
    "BillingState",
    "BillingPostalCode",
    "BillingCountry",
    *(role.account_lookup for role in ACCOUNT_ROLE_DEFINITIONS),
)

CONTACT_REVIEW_FIELDS = CONTACT_FIELDS

ACCOUNT_HISTORY_FIELDS = (
    "Id",
    "AccountId",
    "Field",
    "OldValue",
    "NewValue",
    "CreatedDate",
)

RENAME_CASE_FIELDS = ("Id", "CaseNumber", "Subject", "CreatedDate")

CONTACT_MATCH_FIELDS = ("Id", "AccountId", "Email")


CODE_QUERIED_FIELDS: dict[str, tuple[str, ...]] = {
    "Account": (*ACCOUNT_FIELDS, *ACCOUNT_REVIEW_FIELDS),
    "AccountHistory": ACCOUNT_HISTORY_FIELDS,
    "Case": (
        *APPLICATION_CASE_FIELDS,
        *PROFILE_CASE_FIELDS,
        *STAGING_CASE_FIELDS,
        *RENAME_CASE_FIELDS,
    ),
    "Cert_Audit__c": (*APPLICATION_AUDIT_FIELDS, *PROFILE_AUDIT_FIELDS),
    "Company_Profile_Change__c": SUBMISSION_FIELDS,
    "Contact": (*CONTACT_FIELDS, *CONTACT_REVIEW_FIELDS, *CONTACT_MATCH_FIELDS),
}

# Relationship fields are inventoried on the object that owns the field.
RELATIONSHIP_OWNERS: dict[tuple[str, str], str] = {
    ("Case", "Account"): "Account",
    ("Cert_Audit__c", "Cert_Account__r"): "Account",
    ("Company_Profile_Change__c", "Account__r"): "Account",
}


class FieldInventoryError(ValueError):
    """A queried relationship field has no declared owning object."""


def build_queried_field_inventory(
    export_plan: Mapping[str, Iterable[Any]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Merge code and dictionary fields into a stable object/field inventory.

    Dictionary entries may be ``ExportField`` instances or plain API-name
    strings.  Accepting both keeps this helper simple to test.
    """
    fields_by_object: dict[str, set[str]] = {}
    for object_name, fields in CODE_QUERIED_FIELDS.items():
        for field_name in fields:
            _add_field(fields_by_object, object_name, field_name)

    for object_name, fields in (export_plan or {}).items():
        for field in fields:
            field_name = field if isinstance(field, str) else field.api_name
            _add_field(fields_by_object, object_name, field_name)

    return {
        object_name: tuple(sorted(field_names))
        for object_name, field_names in sorted(fields_by_object.items())
    }


def _add_field(
    fields_by_object: dict[str, set[str]],
    query_object: str,
    field_name: str,
) -> None:
    """Add a direct field or assign a relationship field to its owner."""
    if "." in field_name:
        relationship_name, owned_field = field_name.split(".", 1)
        owner = RELATIONSHIP_OWNERS.get((query_object, relationship_name))
        if owner is None or "." in owned_field:
            raise FieldInventoryError(
                f"Relationship field {query_object}.{field_name} has no "
                "declared owning object."
            )
        object_name = owner
        field_name = owned_field
    else:
        object_name = query_object
    fields_by_object.setdefault(object_name, set()).add(field_name)
