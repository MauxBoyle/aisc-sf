"""Regression coverage for shared Account-role business rules."""

from dataclasses import FrozenInstanceError

import pytest

from aisc_salesforce import (
    account_roles,
    process_profile_updates,
    review_queue,
    stage_profile_updates,
)
from aisc_salesforce.account_roles import (
    ACCOUNT_ROLE_DEFINITIONS,
    QUALIFYING_CERTIFICATION_STATUSES,
    AccountRole,
    RoleDefinition,
)


def test_account_roles_preserve_staged_column_prefixes():
    assert list(AccountRole) == [
        AccountRole.CERTIFICATION,
        AccountRole.PRINCIPAL,
        AccountRole.ACCOUNTING,
        AccountRole.QUALITY_QC,
        AccountRole.NEW_YORK,
    ]
    assert [role.value for role in AccountRole] == [
        "certification",
        "principal",
        "accounting",
        "quality",
        "new_york",
    ]


def test_shared_role_definitions_preserve_all_salesforce_mappings():
    assert isinstance(ACCOUNT_ROLE_DEFINITIONS, tuple)
    assert len(ACCOUNT_ROLE_DEFINITIONS) == 5
    assert all(
        isinstance(definition, RoleDefinition)
        for definition in ACCOUNT_ROLE_DEFINITIONS
    )
    assert [
        (
            definition.role,
            definition.label,
            definition.submitted_fields,
            definition.account_lookup,
        )
        for definition in ACCOUNT_ROLE_DEFINITIONS
    ] == [
        (
            AccountRole.CERTIFICATION,
            "Certification",
            (
                ("first_name", "Cert_First_Name__c"),
                ("last_name", "Cert_Last_Name__c"),
                ("title", "Cert_Title__c"),
                ("email", "Cert_Email__c"),
                ("phone", "Cert_Phone__c"),
            ),
            "Cert_Certification_Contact__c",
        ),
        (
            AccountRole.PRINCIPAL,
            "Principal",
            (
                ("first_name", "Principal_First_Name__c"),
                ("last_name", "Principal_Last_Name__c"),
                ("title", "Principal_Title__c"),
                ("email", "Principal_Email__c"),
                ("phone", "Principal_Phone__c"),
            ),
            "Cert_Principal_Contact__c",
        ),
        (
            AccountRole.ACCOUNTING,
            "Accounting",
            (
                ("first_name", "AP_First_Name__c"),
                ("last_name", "AP_Last_Name__c"),
                ("title", "AP_Title__c"),
                ("email", "AP_Email__c"),
                ("phone", "AP_Phone__c"),
            ),
            "Cert_Accounting_Contact__c",
        ),
        (
            AccountRole.QUALITY_QC,
            "Quality",
            (
                ("first_name", "Quality_First_Name__c"),
                ("last_name", "Quality_Last_Name__c"),
                ("title", "QC_Title__c"),
                ("email", "Quality_Email__c"),
                ("phone", "Quality_Phone__c"),
            ),
            "Cert_Marketing_Contact__c",
        ),
        (
            AccountRole.NEW_YORK,
            "New York",
            (
                ("first_name", "NY_First_Name__c"),
                ("last_name", "NY_Last_Name__c"),
                ("email", "NY_Email__c"),
                ("phone", "NY_Phone__c"),
            ),
            "Cert_Safety_Contact__c",
        ),
    ]


def test_shared_definitions_are_immutable():
    assert isinstance(QUALIFYING_CERTIFICATION_STATUSES, frozenset)
    assert QUALIFYING_CERTIFICATION_STATUSES == frozenset({"Certified", "Initials"})
    with pytest.raises(FrozenInstanceError):
        ACCOUNT_ROLE_DEFINITIONS[0].label = "Changed"
    with pytest.raises(AttributeError):
        ACCOUNT_ROLE_DEFINITIONS.append(ACCOUNT_ROLE_DEFINITIONS[0])


def test_staging_processing_and_review_queue_use_shared_definitions():
    assert stage_profile_updates.ACCOUNT_ROLE_DEFINITIONS is ACCOUNT_ROLE_DEFINITIONS
    assert process_profile_updates.ACCOUNT_ROLE_DEFINITIONS is ACCOUNT_ROLE_DEFINITIONS
    assert review_queue.ACCOUNT_ROLE_DEFINITIONS is ACCOUNT_ROLE_DEFINITIONS
    assert (
        stage_profile_updates.QUALIFYING_CERTIFICATION_STATUSES
        is QUALIFYING_CERTIFICATION_STATUSES
    )
    assert (
        process_profile_updates.QUALIFYING_CERTIFICATION_STATUSES
        is QUALIFYING_CERTIFICATION_STATUSES
    )
    assert account_roles.RoleDefinition is stage_profile_updates.RoleDefinition
