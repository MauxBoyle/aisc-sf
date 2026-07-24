"""Salesforce picklist API values that the Python application understands.

This is deliberately a small catalog.  It records values used in the project's
queries, decisions, and writes; it is not intended to copy every value that an
administrator has configured in Salesforce.
"""

from __future__ import annotations

from enum import StrEnum


class AccountCertificationStatus(StrEnum):
    """Certification statuses used by the application."""

    INITIALS = "Initials"


class CaseCertificationStage(StrEnum):
    """Certification Case stages used by application-stage decisions."""

    CANCEL = "Cancel"
    NEW_APPLICATION = "New_Application"
    DOC_AUDIT = "Doc_Audit"
    PENDING_AUDIT_ASSIGNMENT = "Pending_AuditAssignment"


class ScopeChangeAnswer(StrEnum):
    """Scope-change answers used by the application filter."""

    YES = "Yes"


class AuditStatus(StrEnum):
    """Audit statuses used by application-stage decisions."""

    CANCELED = "Canceled"
    WITHDRAWN = "Withdrawn"
    PENDING_ACCEPTANCE = "Pending Acceptance"
    RESCHEDULE_IN_PROGRESS = "Reschedule in Progress"


class AuditType(StrEnum):
    """Audit types excluded from the application snapshot."""

    ADDITIONAL = "Additional"
    APPEAL = "Appeal"
    SA_NYC = "SA-NYC"
    PREASSESSMENT = "Preassessment"


class ProfileChangeStatus(StrEnum):
    """Company Profile Change statuses read or written by the workflows."""

    NEW = "New"
    CLOSED = "Closed"


class ProfileChangeType(StrEnum):
    """Company Profile Change types used in Python decisions."""

    KEY_DATA = "Key Data"


class CaseStatus(StrEnum):
    """Case statuses written by the Profile Update workflows."""

    PENDING = "Pending"
    CLOSED = "Closed"


class CaseOrigin(StrEnum):
    """Case origins written by the Profile Update automation."""

    WEB = "Web"
    PARTICIPANT_PORTAL = "Participant Portal"


class CaseLabel(StrEnum):
    """Case labels written by the Profile Update automation."""

    AUDITING = "Auditing"
    PARTICIPANT_PORTAL = "Participant Portal"


class CaseSubLabel(StrEnum):
    """Case sub-labels written by the Profile Update automation."""

    PROFILE_CHANGE = "Profile Change"


# An explicit mapping makes catalog coverage easy to review in one place.
SALESFORCE_ENUMS: dict[tuple[str, str], type[StrEnum]] = {
    ("Account", "Cert_Certification_Status__c"): AccountCertificationStatus,
    ("Case", "Cert_Stage__c"): CaseCertificationStage,
    ("Case", "Cert_Is_this_a_scope_change__c"): ScopeChangeAnswer,
    ("Case", "Status"): CaseStatus,
    ("Case", "Origin"): CaseOrigin,
    ("Case", "Label_new__c"): CaseLabel,
    ("Case", "Sub_Label__c"): CaseSubLabel,
    ("Cert_Audit__c", "Cert_Audit_Status__c"): AuditStatus,
    ("Cert_Audit__c", "Cert_Audit_Type__c"): AuditType,
    ("Company_Profile_Change__c", "Status__c"): ProfileChangeStatus,
    ("Company_Profile_Change__c", "Type__c"): ProfileChangeType,
}
