"""Business rules for turning profile updates into Salesforce Cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .profile_update_subjects import (
    ProfileUpdateReference,
    append_profile_update,
    build_aisc_profile_update_subject,
    is_received_profile_update_subject,
    parse_aisc_profile_update_subject,
    subject_has_profile_update,
    validate_subject_length,
)
from .salesforce import SalesforceClient, SalesforceError

AUDIT_FIELDS = [
    "Id",
    "Name",
    "Cert_Audit_Date__c",
    "Company_Profile_Change_Form__c",
    "Explanation_for_Profile_Change_Form__c",
    "Cert_Account__c",
    "Cert_Account__r.Name",
    "Cert_Contact__c",
]

SUBMISSION_FIELDS = [
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
    "AP_First_Name__c",
    "AP_Last_Name__c",
    "AP_Title__c",
    "AP_Email__c",
    "AP_Phone__c",
    "Cert_First_Name__c",
    "Cert_Last_Name__c",
    "Cert_Title__c",
    "Cert_Email__c",
    "Cert_Phone__c",
    "Principal_First_Name__c",
    "Principal_Last_Name__c",
    "Principal_Title__c",
    "Principal_Email__c",
    "Principal_Phone__c",
    "Quality_First_Name__c",
    "Quality_Last_Name__c",
    "QC_Title__c",
    "Quality_Email__c",
    "Quality_Phone__c",
    "NY_First_Name__c",
    "NY_Last_Name__c",
    "NY_Email__c",
    "NY_Phone__c",
    "Other_Personnel_Notes__c",
    "Comments__c",
]

CASE_FIELDS = [
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
]

SUMMARY_FIELDS = [
    ("Name__c", "Submitted By"),
    ("Email__c", "Submitter Email"),
    ("Phone__c", "Submitter Phone"),
    ("Certification_ID__c", "Certification ID"),
    ("Effective_Date__c", "Effective Date"),
    ("Type__c", "Type"),
    ("Revised_Company_Name__c", "Revised Company Name"),
    ("Revised_Company_Owner__c", "Revised Company Owner"),
    ("Revised_Facility_Street__c", "Revised Facility Street"),
    ("Revised_Facility_City__c", "Revised Facility City"),
    ("Revised_Facility_State__c", "Revised Facility State"),
    ("Revised_Facility_Zip__c", "Revised Facility ZIP"),
    ("Revised_Facility_Country__c", "Revised Facility Country"),
    ("Did_the_Cert_contact_change__c", "Certification Contact Changed"),
    ("Did_the_executive_manager_change__c", "Executive Manager Changed"),
    ("Will_you_change_personnel__c", "Personnel Will Change"),
    ("Will_QMS_or_documentation_change__c", "QMS or Documentation Will Change"),
    (
        "Existing_equipment_moved_to_new_facility__c",
        "Existing Equipment Moved to New Facility",
    ),
    ("Will_new_equipment_be_purchased__c", "New Equipment Will Be Purchased"),
    ("Will_old_equipment_be_removed__c", "Old Equipment Will Be Removed"),
    ("Will_software_change__c", "Software Will Change"),
    ("AP_First_Name__c", "Accounting Contact First Name"),
    ("AP_Last_Name__c", "Accounting Contact Last Name"),
    ("AP_Title__c", "Accounting Contact Title"),
    ("AP_Email__c", "Accounting Contact Email"),
    ("AP_Phone__c", "Accounting Contact Phone"),
    ("Cert_First_Name__c", "Certification Contact First Name"),
    ("Cert_Last_Name__c", "Certification Contact Last Name"),
    ("Cert_Title__c", "Certification Contact Title"),
    ("Cert_Email__c", "Certification Contact Email"),
    ("Cert_Phone__c", "Certification Contact Phone"),
    ("Principal_First_Name__c", "Principal Contact First Name"),
    ("Principal_Last_Name__c", "Principal Contact Last Name"),
    ("Principal_Title__c", "Principal Contact Title"),
    ("Principal_Email__c", "Principal Contact Email"),
    ("Principal_Phone__c", "Principal Contact Phone"),
    ("Quality_First_Name__c", "Quality Contact First Name"),
    ("Quality_Last_Name__c", "Quality Contact Last Name"),
    ("QC_Title__c", "Quality Contact Title"),
    ("Quality_Email__c", "Quality Contact Email"),
    ("Quality_Phone__c", "Quality Contact Phone"),
    ("NY_First_Name__c", "New York Contact First Name"),
    ("NY_Last_Name__c", "New York Contact Last Name"),
    ("NY_Email__c", "New York Contact Email"),
    ("NY_Phone__c", "New York Contact Phone"),
    ("Other_Personnel_Notes__c", "Other Personnel Notes"),
    ("Comments__c", "Comments"),
]


@dataclass
class AutomationCounts:
    """Outcome totals printed by the daily command."""

    created: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0


def has_meaningful_explanation(value: Any) -> bool:
    """Return whether an audit explanation contains useful text."""
    if not isinstance(value, str):
        return False
    return value.strip().casefold() not in {"", "none", "n/a"}


def is_eligible_audit(record: dict[str, Any], today: date) -> bool:
    """Apply the inclusive 30-day audit eligibility rules."""
    audit_date = _salesforce_date(record.get("Cert_Audit_Date__c"))
    if audit_date is None or not today - timedelta(days=30) <= audit_date <= today:
        return False
    return bool(record.get("Company_Profile_Change_Form__c")) and (
        has_meaningful_explanation(record.get("Explanation_for_Profile_Change_Form__c"))
    )


def match_contact(
    contacts: list[dict[str, Any]], email: str | None, account_id: str | None
) -> str | None:
    """Match email without case sensitivity, preferring one unambiguous Account match."""
    if not isinstance(email, str) or not email.strip():
        return None
    normalized = email.strip().casefold()
    matches = [
        contact
        for contact in contacts
        if isinstance(contact.get("Email"), str)
        and contact["Email"].strip().casefold() == normalized
    ]
    account_matches = [
        contact for contact in matches if contact.get("AccountId") == account_id
    ]
    preferred = account_matches or matches
    if len(preferred) != 1:
        return None
    return preferred[0].get("Id")


def build_submission_summary(record: dict[str, Any]) -> str:
    """Build stable, readable Chatter text from nonblank submission fields."""
    name = _clean_text(record.get("Name")) or "(unnamed)"
    lines = [f"Profile Update {name}", "", "Submission details:"]
    for api_name, label in SUMMARY_FIELDS:
        value = record.get(api_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, bool):
            display_value = "Yes" if value else "No"
        else:
            display_value = str(value).strip()
        lines.append(f"{label}: {display_value}")
    return "\n".join(lines)


class ProfileUpdateService:
    """Coordinate idempotent audit and submission processing."""

    def __init__(
        self,
        client: SalesforceClient,
        certification_queue_id: str,
        primary_responder_id: str,
        *,
        today: date | None = None,
    ):
        self.client = client
        self.certification_queue_id = certification_queue_id
        self.primary_responder_id = primary_responder_id
        self.today = today or date.today()
        self.errors: list[str] = []
        self._case_cache: dict[str, list[dict[str, Any]]] = {}
        self._feed_cache: dict[str, list[str]] = {}

    def run(self) -> AutomationCounts:
        """Process eligible audits and New submissions and return outcome counts."""
        self.errors.clear()
        self._case_cache.clear()
        self._feed_cache.clear()
        start = self.today - timedelta(days=30)
        audits = self.client.query_records(
            "Cert_Audit__c",
            AUDIT_FIELDS,
            where=(
                f"Cert_Audit_Date__c >= {start.isoformat()} AND "
                f"Cert_Audit_Date__c <= {self.today.isoformat()} AND "
                "Company_Profile_Change_Form__c = TRUE"
            ),
            order_by="Cert_Audit_Date__c ASC, Id ASC",
        )
        submissions = self.client.query_records(
            "Company_Profile_Change__c",
            SUBMISSION_FIELDS,
            where="Status__c = 'New'",
            order_by="CreatedDate ASC, Id ASC",
        )

        counts = AutomationCounts()
        for record in audits:
            if not is_eligible_audit(record, self.today):
                counts.skipped += 1
                continue
            self._count_record(counts, self._process_audit, record)
        for record in submissions:
            self._count_record(counts, self._process_submission, record)
        return counts

    def _count_record(
        self, counts: AutomationCounts, operation: Any, record: Any
    ) -> None:
        try:
            outcome = operation(record)
        except (SalesforceError, ValueError, KeyError, TypeError) as error:
            counts.failed += 1
            record_id = record.get("Id", "unknown")
            self.errors.append(f"{record_id}: {error}")
            return
        setattr(counts, outcome, getattr(counts, outcome) + 1)

    def _process_audit(self, audit: dict[str, Any]) -> str:
        account_id = _required_text(audit, "Cert_Account__c", "Audit Account")
        account_name = _relationship_name(audit, "Cert_Account__r")
        audit_name = _required_text(audit, "Name", "Audit Name")
        audit_date = _salesforce_date(audit.get("Cert_Audit_Date__c"))
        if audit_date is None:
            raise ValueError("Audit Date is missing or invalid.")
        explanation = _required_text(
            audit,
            "Explanation_for_Profile_Change_Form__c",
            "Audit explanation",
        )
        subject = (
            f"{audit_date.isoformat()}: Profile Update Expected for {account_name} "
            f"based on {audit_name}"
        )
        validate_subject_length(subject)
        cases = self._profile_cases(account_id)

        matching = self._newest(
            case for case in cases if _clean_text(case.get("Subject")) == subject
        )
        if matching is not None:
            worked = self._reconcile_audit_messages(
                matching, audit, explanation, account_name
            )
            return "reused" if worked else "skipped"

        received = self._newest(
            case
            for case in cases
            if is_received_profile_update_subject(case.get("Subject"))
        )
        if received is not None:
            worked = False
            if received.get("IsClosed"):
                self.client.update_record(
                    "Case",
                    _required_text(received, "Id", "Case ID"),
                    {"Status": "Pending"},
                )
                received["Status"] = "Pending"
                received["IsClosed"] = False
                worked = True
            return (
                "reused"
                if self._reconcile_audit_messages(
                    received, audit, explanation, account_name
                )
                or worked
                else "skipped"
            )

        if any(_record_date(case.get("CreatedDate")) >= audit_date for case in cases):
            return "skipped"

        payload = self._case_payload(
            subject=subject,
            account_id=account_id,
            contact_id=audit.get("Cert_Contact__c"),
            origin="Web",
            label="Auditing",
        )
        case_id = self.client.create_record("Case", payload)
        created = self.client.get_record("Case", case_id, ["Id", "CaseNumber"])
        created.update(payload)
        created.setdefault("CreatedDate", datetime.now().isoformat())
        created["IsClosed"] = False
        cases.append(created)
        self._reconcile_audit_messages(created, audit, explanation, account_name)
        return "created"

    def _reconcile_audit_messages(
        self,
        case: dict[str, Any],
        audit: dict[str, Any],
        explanation: str,
        account_name: str,
    ) -> bool:
        case_id = _required_text(case, "Id", "Case ID")
        case_number = case.get("CaseNumber")
        if not case_number:
            case_number = self.client.get_record("Case", case_id, ["CaseNumber"]).get(
                "CaseNumber"
            )
        if not case_number:
            raise SalesforceError(f"Case {case_id} does not have a CaseNumber.")
        audit_message = (
            "Profile Change need noted. A pending case "
            f"({case_number}) has been made on the {account_name} account. -MB"
        )
        worked = self._post_if_missing(case_id, explanation)
        return (
            self._post_if_missing(
                _required_text(audit, "Id", "Audit ID"), audit_message
            )
            or worked
        )

    def _process_submission(self, submission: dict[str, Any]) -> str:
        account_id = _required_text(submission, "Account__c", "Submission Account")
        account_name = _relationship_name(submission, "Account__r")
        update_name = _required_text(submission, "Name", "Profile Update Name")
        created_date = _salesforce_date(submission.get("CreatedDate"))
        if created_date is None:
            raise ValueError("Submission Created Date is missing or invalid.")
        summary = build_submission_summary(submission)
        cases = self._profile_cases(account_id)

        if any(
            parse_aisc_profile_update_subject(case.get("Subject")) is not None
            and subject_has_profile_update(case.get("Subject"), update_name)
            for case in cases
        ):
            return "skipped"

        same_name_cases = [
            case
            for case in cases
            if subject_has_profile_update(case.get("Subject"), update_name)
        ]
        for case in sorted(same_name_cases, key=_case_sort_key, reverse=True):
            case_id = _required_text(case, "Id", "Case ID")
            if summary in self._feed_messages(case_id):
                return "skipped"
        if same_name_cases:
            case_id = _required_text(
                self._newest(iter(same_name_cases)) or {}, "Id", "Case ID"
            )
            self._post_if_missing(case_id, summary)
            return "reused"

        received = self._newest(
            case
            for case in cases
            if is_received_profile_update_subject(case.get("Subject"))
        )
        if received is not None:
            case_id = _required_text(received, "Id", "Case ID")
            old_subject = _required_text(received, "Subject", "Case Subject")
            subject = append_profile_update(old_subject, update_name, created_date)
            self.client.update_record("Case", case_id, {"Subject": subject})
            received["Subject"] = subject
            self._post_if_missing(case_id, summary)
            return "reused"

        contact_id = self._submission_contact(submission, account_id)
        subject = build_aisc_profile_update_subject(
            account_name,
            [ProfileUpdateReference(update_name, created_date)],
        )
        payload = self._case_payload(
            subject=subject,
            account_id=account_id,
            contact_id=contact_id,
            origin="Participant Portal",
            label="Participant Portal",
        )
        expected = self._newest(
            case
            for case in cases
            if "profile update expected" in _clean_text(case.get("Subject")).casefold()
        )
        if expected is not None:
            case_id = _required_text(expected, "Id", "Case ID")
            self.client.update_record("Case", case_id, payload)
            expected.update(payload)
            self._post_if_missing(case_id, summary)
            return "reused"

        case_id = self.client.create_record("Case", payload)
        cases.append(
            {
                "Id": case_id,
                **payload,
                "IsClosed": False,
                "CreatedDate": datetime.now().isoformat(),
            }
        )
        self._post_if_missing(case_id, summary)
        return "created"

    def _case_payload(
        self,
        *,
        subject: str,
        account_id: str,
        contact_id: Any,
        origin: str,
        label: str,
    ) -> dict[str, Any]:
        return {
            "Subject": subject,
            "OwnerId": self.certification_queue_id,
            "Primary_Responder__c": self.primary_responder_id,
            "ContactId": contact_id,
            "AccountId": account_id,
            "Status": "Pending",
            "Origin": origin,
            "Label_new__c": label,
            "Sub_Label__c": "Profile Change",
            "Description": "",
        }

    def _profile_cases(self, account_id: str) -> list[dict[str, Any]]:
        if account_id not in self._case_cache:
            escaped_id = escape_soql_string(account_id)
            records = self.client.query_records(
                "Case",
                CASE_FIELDS,
                where=(
                    f"AccountId = '{escaped_id}' AND Subject LIKE '%Profile Update%'"
                ),
                order_by="CreatedDate DESC, Id DESC",
            )
            self._case_cache[account_id] = records
        return self._case_cache[account_id]

    def _submission_contact(
        self, submission: dict[str, Any], account_id: str
    ) -> str | None:
        email = _clean_text(submission.get("Email__c"))
        if not email:
            return None
        contacts = self.client.query_records(
            "Contact",
            ["Id", "AccountId", "Email"],
            where=f"Email = '{escape_soql_string(email)}'",
            order_by="Id ASC",
        )
        return match_contact(contacts, email, account_id)

    def _feed_messages(self, record_id: str) -> list[str]:
        if record_id not in self._feed_cache:
            self._feed_cache[record_id] = self.client.get_feed_messages(record_id)
        return self._feed_cache[record_id]

    def _post_if_missing(self, record_id: str, message: str) -> bool:
        feed = self._feed_messages(record_id)
        if message in feed:
            return False
        self.client.post_feed_message(record_id, message)
        feed.append(message)
        return True

    @staticmethod
    def _newest(records: Any) -> dict[str, Any] | None:
        return max(records, key=_case_sort_key, default=None)


def escape_soql_string(value: str) -> str:
    """Escape slash and quote characters in a SOQL string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(record: dict[str, Any], key: str, label: str) -> str:
    value = _clean_text(record.get(key))
    if not value:
        raise ValueError(f"{label} is missing.")
    return value


def _relationship_name(record: dict[str, Any], key: str) -> str:
    relationship = record.get(key)
    if not isinstance(relationship, dict):
        raise ValueError("Account Name is missing.")
    return _required_text(relationship, "Name", "Account Name")


def _salesforce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value if not isinstance(value, datetime) else value.date()
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _record_date(value: Any) -> date:
    return _salesforce_date(value) or date.min


def _case_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (_clean_text(record.get("CreatedDate")), _clean_text(record.get("Id")))
