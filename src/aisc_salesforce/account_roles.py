"""Shared Account-role mappings for Profile Update workflows.

This module is the sole owner of Salesforce field names for submitted Account
roles.  Quality/QC intentionally maps to ``Cert_Marketing_Contact__c``;
despite its name, that is the Account lookup field used for this role.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AccountRole(StrEnum):
    """Account roles, with values matching staged CSV column prefixes."""

    CERTIFICATION = "certification"
    PRINCIPAL = "principal"
    ACCOUNTING = "accounting"
    QUALITY_QC = "quality"
    NEW_YORK = "new_york"


@dataclass(frozen=True)
class RoleDefinition:
    """Map one submitted role to its fields and current Account lookup."""

    role: AccountRole
    label: str
    first_name_field: str
    last_name_field: str
    title_field: str | None
    email_field: str
    phone_field: str
    account_lookup: str

    @property
    def prefix(self) -> str:
        """Return the role's established staged-CSV column prefix."""
        return str(self.role)

    @property
    def submitted_fields(self) -> tuple[tuple[str, str], ...]:
        """Return CSV suffix and Salesforce submission-field pairs."""
        fields = [
            ("first_name", self.first_name_field),
            ("last_name", self.last_name_field),
        ]
        if self.title_field is not None:
            fields.append(("title", self.title_field))
        fields.extend(
            [
                ("email", self.email_field),
                ("phone", self.phone_field),
            ]
        )
        return tuple(fields)


ACCOUNT_ROLE_DEFINITIONS = (
    RoleDefinition(
        AccountRole.CERTIFICATION,
        "Certification",
        "Cert_First_Name__c",
        "Cert_Last_Name__c",
        "Cert_Title__c",
        "Cert_Email__c",
        "Cert_Phone__c",
        "Cert_Certification_Contact__c",
    ),
    RoleDefinition(
        AccountRole.PRINCIPAL,
        "Principal",
        "Principal_First_Name__c",
        "Principal_Last_Name__c",
        "Principal_Title__c",
        "Principal_Email__c",
        "Principal_Phone__c",
        "Cert_Principal_Contact__c",
    ),
    RoleDefinition(
        AccountRole.ACCOUNTING,
        "Accounting",
        "AP_First_Name__c",
        "AP_Last_Name__c",
        "AP_Title__c",
        "AP_Email__c",
        "AP_Phone__c",
        "Cert_Accounting_Contact__c",
    ),
    RoleDefinition(
        AccountRole.QUALITY_QC,
        "Quality",
        "Quality_First_Name__c",
        "Quality_Last_Name__c",
        "QC_Title__c",
        "Quality_Email__c",
        "Quality_Phone__c",
        "Cert_Marketing_Contact__c",
    ),
    RoleDefinition(
        AccountRole.NEW_YORK,
        "New York",
        "NY_First_Name__c",
        "NY_Last_Name__c",
        None,
        "NY_Email__c",
        "NY_Phone__c",
        "Cert_Safety_Contact__c",
    ),
)
"""Ordered, immutable definitions for every supported Account role."""

QUALIFYING_CERTIFICATION_STATUSES = frozenset({"Certified", "Initials"})
"""Certification statuses that qualify an Account for Profile Update work."""
