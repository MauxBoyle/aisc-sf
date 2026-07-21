"""Build read-only CSV staging snapshots for New profile update submissions."""

from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .profile_update_subjects import subject_has_profile_update
from .profile_updates import SUBMISSION_FIELDS, escape_soql_string
from .salesforce import SalesforceClient

ACCOUNT_FIELDS = [
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
    "Cert_Certification_Contact__c",
    "Cert_Principal_Contact__c",
    "Cert_Accounting_Contact__c",
    "Cert_Marketing_Contact__c",
    "Cert_Safety_Contact__c",
]

CONTACT_FIELDS = [
    "Id",
    "AccountId",
    "FirstName",
    "LastName",
    "Title",
    "Email",
    "Phone",
]

PROFILE_CASE_FIELDS = [
    "Id",
    "CaseNumber",
    "Subject",
    "Status",
    "CreatedDate",
    "AccountId",
]

KEY_ANSWER_FIELDS = [
    ("Did_the_Cert_contact_change__c", "Certification contact changed"),
    ("Did_the_executive_manager_change__c", "Executive manager changed"),
    ("Will_you_change_personnel__c", "Personnel will change"),
    ("Will_QMS_or_documentation_change__c", "QMS or documentation will change"),
    (
        "Existing_equipment_moved_to_new_facility__c",
        "Existing equipment moved to new facility",
    ),
    ("Will_new_equipment_be_purchased__c", "New equipment will be purchased"),
    ("Will_old_equipment_be_removed__c", "Old equipment will be removed"),
    ("Will_software_change__c", "Software will change"),
]

ADDRESS_FIELDS = [
    ("revised_facility_street", "Revised_Facility_Street__c", "BillingStreet"),
    ("revised_facility_city", "Revised_Facility_City__c", "BillingCity"),
    ("revised_facility_state", "Revised_Facility_State__c", "BillingState"),
    ("revised_facility_zip", "Revised_Facility_Zip__c", "BillingPostalCode"),
    ("revised_facility_country", "Revised_Facility_Country__c", "BillingCountry"),
]

KEY_UPDATE_FIELDS = [
    "Effective_Date__c",
    "Revised_Company_Name__c",
    "Revised_Company_Owner__c",
    *(api_name for _, api_name, _ in ADDRESS_FIELDS),
    *(api_name for api_name, _ in KEY_ANSWER_FIELDS),
]


@dataclass(frozen=True)
class RoleDefinition:
    """Map one submitted role to its fields and current Account lookup."""

    prefix: str
    label: str
    first_name_field: str
    last_name_field: str
    title_field: str | None
    email_field: str
    phone_field: str
    account_lookup: str

    @property
    def submitted_fields(self) -> list[tuple[str, str]]:
        """Return pairs of CSV suffixes and Salesforce submission fields."""
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
        return fields


ROLE_DEFINITIONS = [
    RoleDefinition(
        "certification",
        "Certification",
        "Cert_First_Name__c",
        "Cert_Last_Name__c",
        "Cert_Title__c",
        "Cert_Email__c",
        "Cert_Phone__c",
        "Cert_Certification_Contact__c",
    ),
    RoleDefinition(
        "principal",
        "Principal",
        "Principal_First_Name__c",
        "Principal_Last_Name__c",
        "Principal_Title__c",
        "Principal_Email__c",
        "Principal_Phone__c",
        "Cert_Principal_Contact__c",
    ),
    RoleDefinition(
        "accounting",
        "Accounting",
        "AP_First_Name__c",
        "AP_Last_Name__c",
        "AP_Title__c",
        "AP_Email__c",
        "AP_Phone__c",
        "Cert_Accounting_Contact__c",
    ),
    RoleDefinition(
        "quality",
        "Quality",
        "Quality_First_Name__c",
        "Quality_Last_Name__c",
        "QC_Title__c",
        "Quality_Email__c",
        "Quality_Phone__c",
        "Cert_Marketing_Contact__c",
    ),
    RoleDefinition(
        "new_york",
        "New York",
        "NY_First_Name__c",
        "NY_Last_Name__c",
        None,
        "NY_Email__c",
        "NY_Phone__c",
        "Cert_Safety_Contact__c",
    ),
]

SHARED_COLUMNS = [
    "source_submission_ids",
    "source_submission_names",
    "earliest_submission_date",
    "latest_submission_date",
    "account_id",
    "account_name",
    "case_id",
    "case_number",
    "case_status",
    "case_match_status",
    "certification_id",
    "submitter_name",
    "submitter_email",
    "submitter_phone",
    "comments",
    "personnel_notes",
    "has_contact_derived_values",
    "has_no_update_content",
    "has_warnings",
    "warnings",
]

KEY_COLUMNS = [
    "has_key_updates",
    "earliest_key_update_date",
    "effective_date",
    "revised_company_name",
    "revised_company_owner",
    "revised_facility_street",
    "revised_facility_city",
    "revised_facility_state",
    "revised_facility_zip",
    "revised_facility_country",
    "key_answers",
]

ROLE_RESOLUTION_SUFFIXES = [
    "resolution_action",
    "salesforce_contact_id",
    "resolution_source",
    "source_submission_id",
    "source_role",
    "warning",
]


def _role_columns(role: RoleDefinition) -> list[str]:
    submitted = [f"{role.prefix}_{suffix}" for suffix, _ in role.submitted_fields]
    resolution = [f"{role.prefix}_{suffix}" for suffix in ROLE_RESOLUTION_SUFFIXES]
    return submitted + resolution


CSV_COLUMNS = [
    *SHARED_COLUMNS,
    *KEY_COLUMNS,
    *(column for role in ROLE_DEFINITIONS for column in _role_columns(role)),
]


@dataclass
class StagingResult:
    """Rows and warning totals produced by one read-only staging pass."""

    rows: list[dict[str, str]]
    warning_count: int


@dataclass
class MergedRole:
    """A role's latest nonblank submitted values and their source."""

    definition: RoleDefinition
    values: dict[str, str]
    source_submission_id: str = ""


class ProfileUpdateStagingService:
    """Query and merge New profile updates without writing to Salesforce."""

    def __init__(self, client: SalesforceClient):
        self.client = client

    def stage(self) -> StagingResult:
        """Return deterministic CSV-ready rows for all New submissions."""
        submissions = self.client.query_records(
            "Company_Profile_Change__c",
            SUBMISSION_FIELDS,
            where="Status__c = 'New'",
            order_by="CreatedDate ASC, Id ASC",
        )
        submissions = sorted(submissions, key=_submission_sort_key)

        requested_account_ids = _unique_text_values(
            record.get("Account__c") for record in submissions
        )
        accounts = self._query_accounts("Id", requested_account_ids)
        accounts_by_id = {
            account_id: record
            for record in accounts
            if (account_id := _clean_text(record.get("Id")))
        }

        parent_ids = _unique_text_values(
            account.get("ParentId") for account in accounts
        )
        siblings = self._query_accounts("ParentId", parent_ids)
        all_accounts_by_id = dict(accounts_by_id)
        for sibling in siblings:
            sibling_id = _clean_text(sibling.get("Id"))
            if sibling_id:
                all_accounts_by_id.setdefault(sibling_id, sibling)

        contacts = self._query_contacts(list(all_accounts_by_id.values()))
        cases = self._query_cases(requested_account_ids)
        grouped = _group_submissions(submissions)
        rows = [
            self._build_row(
                records,
                accounts_by_id,
                all_accounts_by_id,
                contacts,
                cases,
            )
            for records in grouped
        ]
        rows.sort(
            key=lambda row: (
                row["earliest_submission_date"],
                row["account_id"],
                row["source_submission_ids"],
            )
        )
        warning_count = sum(
            len(row["warnings"].splitlines()) for row in rows if row["warnings"]
        )
        return StagingResult(rows, warning_count)

    def _query_accounts(
        self, field_name: str, values: list[str]
    ) -> list[dict[str, Any]]:
        if not values:
            return []
        return self.client.query_records(
            "Account",
            ACCOUNT_FIELDS,
            where=_where_in(field_name, values),
            order_by="Id ASC",
        )

    def _query_contacts(self, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        account_ids = _unique_text_values(account.get("Id") for account in accounts)
        lookup_ids = _unique_text_values(
            account.get(role.account_lookup)
            for account in accounts
            for role in ROLE_DEFINITIONS
        )
        clauses = []
        if account_ids:
            clauses.append(_where_in("AccountId", account_ids))
        if lookup_ids:
            clauses.append(_where_in("Id", lookup_ids))
        if not clauses:
            return []
        where = clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"
        return self.client.query_records(
            "Contact",
            CONTACT_FIELDS,
            where=where,
            order_by="AccountId ASC, Id ASC",
        )

    def _query_cases(self, account_ids: list[str]) -> list[dict[str, Any]]:
        if not account_ids:
            return []
        return self.client.query_records(
            "Case",
            PROFILE_CASE_FIELDS,
            where=(
                f"{_where_in('AccountId', account_ids)} "
                "AND Subject LIKE '%Profile Update%'"
            ),
            order_by="CreatedDate DESC, Id DESC",
        )

    def _build_row(
        self,
        records: list[dict[str, Any]],
        accounts_by_id: dict[str, dict[str, Any]],
        all_accounts_by_id: dict[str, dict[str, Any]],
        contacts: list[dict[str, Any]],
        cases: list[dict[str, Any]],
    ) -> dict[str, str]:
        row = {column: "" for column in CSV_COLUMNS}
        merged = _merge_records(records)
        warnings: list[str] = []

        account_id = _clean_text(merged.get("Account__c"))
        account = accounts_by_id.get(account_id)
        key_records = [record for record in records if _is_key_update(record)]
        row.update(
            {
                "source_submission_ids": json.dumps(
                    [_clean_text(record.get("Id")) for record in records],
                    ensure_ascii=False,
                ),
                "source_submission_names": json.dumps(
                    [_clean_text(record.get("Name")) for record in records],
                    ensure_ascii=False,
                ),
                "earliest_submission_date": _clean_text(records[0].get("CreatedDate")),
                "latest_submission_date": _clean_text(records[-1].get("CreatedDate")),
                "account_id": account_id,
                "account_name": _account_name(account, merged),
                "has_key_updates": "true" if key_records else "false",
                "earliest_key_update_date": (
                    _clean_text(key_records[0].get("CreatedDate"))
                    if key_records
                    else ""
                ),
                "certification_id": _first_nonblank(
                    account.get("Certification_ID__c") if account else None,
                    merged.get("Certification_ID__c"),
                ),
                "submitter_name": _clean_text(merged.get("Name__c")),
                "submitter_email": _clean_text(merged.get("Email__c")),
                "submitter_phone": _clean_text(merged.get("Phone__c")),
                "comments": _collect_submitted_values(records, "Comments__c"),
                "personnel_notes": _collect_submitted_values(
                    records, "Other_Personnel_Notes__c"
                ),
                "has_contact_derived_values": "false",
                "has_no_update_content": (
                    "false" if _has_submitted_update_content(records) else "true"
                ),
                "effective_date": _display_value(merged.get("Effective_Date__c")),
                "revised_company_name": _display_value(
                    merged.get("Revised_Company_Name__c")
                ),
                "revised_company_owner": _display_value(
                    merged.get("Revised_Company_Owner__c")
                ),
            }
        )
        self._populate_case(row, records, cases, warnings)

        if not row["submitter_email"]:
            warnings.append("Submission group has a blank submitter email.")
        if not account_id:
            warnings.append("Submission does not contain an Account ID.")
        elif account is None:
            warnings.append(f"Account {account_id} could not be retrieved.")
        else:
            if not _clean_text(account.get("Name")):
                warnings.append(f"Account {account_id} has no Account Name.")
            if not row["certification_id"]:
                warnings.append(f"Account {account_id} has no Certification ID.")

        for record in records:
            if not _clean_text(record.get("CreatedDate")):
                source_id = _clean_text(record.get("Id")) or "(unknown)"
                warnings.append(f"Submission {source_id} has no CreatedDate.")

        self._populate_address(row, merged, account, warnings)
        self._populate_key_answers(row, merged)

        merged_roles = _merge_roles(records)
        has_contact_derived_values = False
        for merged_role in merged_roles:
            role_has_derived_values = self._populate_role(
                row,
                merged_role,
                merged_roles,
                account,
                all_accounts_by_id,
                contacts,
                warnings,
            )
            has_contact_derived_values = (
                has_contact_derived_values or role_has_derived_values
            )

        row["has_contact_derived_values"] = (
            "true" if has_contact_derived_values else "false"
        )
        row["has_warnings"] = "true" if warnings else "false"
        row["warnings"] = "\n".join(warnings)
        return row

    @staticmethod
    def _populate_case(
        row: dict[str, str],
        records: list[dict[str, Any]],
        cases: list[dict[str, Any]],
        warnings: list[str],
    ) -> None:
        account_id = row["account_id"]
        source_names = [
            name for record in records if (name := _clean_text(record.get("Name")))
        ]
        account_cases = [
            case for case in cases if _clean_text(case.get("AccountId")) == account_id
        ]
        matches = [
            case
            for case in account_cases
            if source_names
            and all(
                subject_has_profile_update(case.get("Subject"), name)
                for name in source_names
            )
        ]
        if len(matches) == 1:
            case = matches[0]
            row.update(
                {
                    "case_id": _clean_text(case.get("Id")),
                    "case_number": _clean_text(case.get("CaseNumber")),
                    "case_status": _clean_text(case.get("Status")),
                    "case_match_status": "matched",
                }
            )
            return

        partial_matches = [
            case
            for case in account_cases
            if any(
                subject_has_profile_update(case.get("Subject"), name)
                for name in source_names
            )
        ]
        status = "ambiguous" if matches or partial_matches else "missing"
        row["case_match_status"] = status
        if status == "ambiguous":
            warning = (
                "Blocking Case match: source submissions match more than one "
                "Case or do not share one Case."
            )
        else:
            warning = (
                "Blocking Case match: no Case contains the source submission names."
            )
        warnings.append(warning)

    @staticmethod
    def _populate_address(
        row: dict[str, str],
        merged: dict[str, Any],
        account: dict[str, Any] | None,
        warnings: list[str],
    ) -> None:
        supplied = any(
            _has_value(merged.get(api_name)) for _, api_name, _ in ADDRESS_FIELDS
        )
        if not supplied:
            return
        for csv_name, api_name, billing_name in ADDRESS_FIELDS:
            value = _display_value(merged.get(api_name))
            if not value and account is not None:
                value = _display_value(account.get(billing_name))
            row[csv_name] = value
            if not value:
                readable = csv_name.removeprefix("revised_facility_").replace("_", " ")
                warnings.append(
                    f"Revised address {readable} is blank and could not be "
                    "filled from the Account billing address."
                )

    @staticmethod
    def _populate_key_answers(row: dict[str, str], merged: dict[str, Any]) -> None:
        is_key_data = _normalized(merged.get("Type__c")) == "key data"
        if not is_key_data and not any(
            _has_value(merged.get(api_name)) for api_name in KEY_UPDATE_FIELDS
        ):
            return
        row["key_answers"] = "\n".join(
            f"{label}: {_display_value(merged.get(api_name))}"
            for api_name, label in KEY_ANSWER_FIELDS
        )

    @staticmethod
    def _populate_role(
        row: dict[str, str],
        role: MergedRole,
        all_roles: list[MergedRole],
        account: dict[str, Any] | None,
        all_accounts_by_id: dict[str, dict[str, Any]],
        contacts: list[dict[str, Any]],
        warnings: list[str],
    ) -> bool:
        prefix = role.definition.prefix
        if not role.values:
            return False
        for suffix, _ in role.definition.submitted_fields:
            row[f"{prefix}_{suffix}"] = role.values.get(suffix, "")

        resolution, warning = _resolve_role(
            role,
            all_roles,
            account,
            all_accounts_by_id,
            contacts,
        )
        for suffix in ROLE_RESOLUTION_SUFFIXES:
            if suffix == "warning":
                continue
            row[f"{prefix}_{suffix}"] = resolution.get(suffix, "")
        has_derived_values = _fill_missing_optional_role_fields(
            row,
            role,
            all_roles,
            contacts,
            resolution,
        )
        row[f"{prefix}_warning"] = warning
        if warning:
            warnings.append(f"{role.definition.label} role: {warning}")
        return has_derived_values


def _group_submissions(
    submissions: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for index, record in enumerate(submissions):
        account_id = _clean_text(record.get("Account__c"))
        email = _normalized(record.get("Email__c"))
        record_id = _clean_text(record.get("Id")) or str(index)
        account_key = account_id or f"blank-account:{record_id}"
        email_key = email or f"blank-email:{record_id}"
        groups.setdefault((account_key, email_key), []).append(record)
    return list(groups.values())


def _merge_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if _has_value(value):
                merged[key] = value
    return merged


def _collect_submitted_values(
    records: list[dict[str, Any]], field_name: str
) -> str:
    """Join every nonblank submitted value in deterministic submission order."""
    return "\n".join(
        _display_value(record.get(field_name))
        for record in records
        if _has_value(record.get(field_name))
    )


def _has_submitted_update_content(records: list[dict[str, Any]]) -> bool:
    """Return whether raw submissions contain any reviewable update content."""
    update_fields = [
        *KEY_UPDATE_FIELDS,
        *(
            api_name
            for role in ROLE_DEFINITIONS
            for _, api_name in role.submitted_fields
        ),
        "Comments__c",
        "Other_Personnel_Notes__c",
    ]
    return any(
        _has_value(record.get(field_name))
        for record in records
        for field_name in update_fields
    )


def _merge_roles(records: list[dict[str, Any]]) -> list[MergedRole]:
    roles: list[MergedRole] = []
    for definition in ROLE_DEFINITIONS:
        values: dict[str, str] = {}
        source_submission_id = ""
        for record in records:
            supplied_here = False
            for suffix, api_name in definition.submitted_fields:
                if _has_value(record.get(api_name)):
                    values[suffix] = _display_value(record.get(api_name))
                    supplied_here = True
            if supplied_here:
                source_submission_id = _clean_text(record.get("Id"))
        roles.append(MergedRole(definition, values, source_submission_id))
    return roles


def _resolve_role(
    role: MergedRole,
    all_roles: list[MergedRole],
    account: dict[str, Any] | None,
    all_accounts_by_id: dict[str, dict[str, Any]],
    contacts: list[dict[str, Any]],
) -> tuple[dict[str, str], str]:
    values = role.values
    first_name = values.get("first_name", "")
    last_name = values.get("last_name", "")
    email = values.get("email", "")
    title = values.get("title", "")
    phone = values.get("phone", "")

    submitted_candidates = [
        candidate
        for candidate in all_roles
        if candidate.definition.prefix != role.definition.prefix and candidate.values
    ]
    account_id = _clean_text(account.get("Id")) if account else ""
    parent_id = _clean_text(account.get("ParentId")) if account else ""
    sibling_ids = {
        candidate_id
        for candidate_id, candidate in all_accounts_by_id.items()
        if candidate_id != account_id
        and parent_id
        and _clean_text(candidate.get("ParentId")) == parent_id
    }
    account_contacts = [
        contact
        for contact in contacts
        if _clean_text(contact.get("AccountId")) == account_id
    ]
    sibling_contacts = [
        contact
        for contact in contacts
        if _clean_text(contact.get("AccountId")) in sibling_ids
    ]

    if first_name:
        tiers = [
            (
                "submitted_role",
                _matching_submitted_names(submitted_candidates, first_name, last_name),
            ),
            (
                "account_contact",
                _matching_contact_names(account_contacts, first_name, last_name),
            ),
            (
                "sibling_contact",
                _matching_contact_names(sibling_contacts, first_name, last_name),
            ),
        ]
        resolution, warning = _first_tier_resolution(tiers, no_match_is_warning=False)
        if resolution or warning:
            return resolution, warning
        return _new_contact_resolution(
            role,
            "No exact contact match was found; a new contact will need to be created.",
        )

    if last_name:
        return _new_contact_resolution(
            role,
            "Last-name-only input cannot be resolved safely; a new contact will "
            "need to be created after human review.",
        )

    if email:
        tiers = [
            (
                "submitted_role",
                _matching_submitted_emails(submitted_candidates, email),
            ),
            (
                "account_contact",
                [
                    contact
                    for contact in account_contacts
                    if _normalized(contact.get("Email")) == _normalized(email)
                ],
            ),
            (
                "sibling_contact",
                [
                    contact
                    for contact in sibling_contacts
                    if _normalized(contact.get("Email")) == _normalized(email)
                ],
            ),
        ]
        resolution, warning = _first_tier_resolution(tiers, no_match_is_warning=False)
        if resolution or warning:
            return resolution, warning
        return _existing_role_resolution(
            account,
            role.definition,
            action="change_email",
            missing_message="Email does not match a known contact",
        )

    if title or phone:
        return _existing_role_resolution(
            account,
            role.definition,
            action="update_contact",
            missing_message="Partial contact update",
        )

    return {}, "Submitted role fields could not be resolved."


def _matching_submitted_names(
    candidates: list[MergedRole], first_name: str, last_name: str
) -> list[MergedRole]:
    matches = [
        candidate
        for candidate in candidates
        if _normalized(candidate.values.get("first_name")) == _normalized(first_name)
        and (
            not last_name
            or _normalized(candidate.values.get("last_name")) == _normalized(last_name)
        )
    ]
    return _distinct_submitted_contacts(
        matches,
        identity_fields=("first_name", "last_name"),
        conflict_field="email",
    )


def _matching_submitted_emails(
    candidates: list[MergedRole], email: str
) -> list[MergedRole]:
    matches = [
        candidate
        for candidate in candidates
        if _normalized(candidate.values.get("email")) == _normalized(email)
    ]
    return _distinct_submitted_contacts(matches, identity_fields=("email",))


def _distinct_submitted_contacts(
    candidates: list[MergedRole],
    *,
    identity_fields: tuple[str, ...],
    conflict_field: str | None = None,
) -> list[MergedRole]:
    """Combine repeated role entries for the same submitted person."""
    grouped: dict[tuple[str, ...], list[MergedRole]] = {}
    for candidate in candidates:
        identity = tuple(
            _normalized(candidate.values.get(field_name))
            for field_name in identity_fields
        )
        grouped.setdefault(identity, []).append(candidate)

    distinct: list[MergedRole] = []
    for group in grouped.values():
        conflicts = (
            {
                value
                for candidate in group
                if (value := _normalized(candidate.values.get(conflict_field)))
            }
            if conflict_field
            else set()
        )
        if len(conflicts) > 1:
            distinct.extend(group)
        else:
            distinct.append(max(group, key=_role_detail_count))
    return distinct


def _role_detail_count(role: MergedRole) -> int:
    return sum(bool(_clean_text(value)) for value in role.values.values())


def _matching_contact_names(
    contacts: list[dict[str, Any]], first_name: str, last_name: str
) -> list[dict[str, Any]]:
    return [
        contact
        for contact in contacts
        if _normalized(contact.get("FirstName")) == _normalized(first_name)
        and (
            not last_name
            or _normalized(contact.get("LastName")) == _normalized(last_name)
        )
    ]


def _first_tier_resolution(
    tiers: list[tuple[str, list[Any]]], *, no_match_is_warning: bool = True
) -> tuple[dict[str, str], str]:
    for source, candidates in tiers:
        if not candidates:
            continue
        if len(candidates) > 1:
            return (
                {},
                f"Resolution is ambiguous: {len(candidates)} matches in "
                f"{source.replace('_', ' ')}.",
            )
        candidate = candidates[0]
        if isinstance(candidate, MergedRole):
            return (
                {
                    "resolution_action": "use_submitted_contact",
                    "resolution_source": source,
                    "source_submission_id": candidate.source_submission_id,
                    "source_role": candidate.definition.prefix,
                },
                "",
            )
        return (
            {
                "resolution_action": "update_contact",
                "salesforce_contact_id": _clean_text(candidate.get("Id")),
                "resolution_source": source,
            },
            "",
        )
    if no_match_is_warning:
        return {}, "No exact contact match was found."
    return {}, ""


def _existing_role_resolution(
    account: dict[str, Any] | None,
    definition: RoleDefinition,
    *,
    action: str,
    missing_message: str,
) -> tuple[dict[str, str], str]:
    contact_id = _clean_text(account.get(definition.account_lookup)) if account else ""
    resolution = {
        "resolution_action": action,
        "resolution_source": "account_role_lookup",
    }
    if not contact_id:
        return (
            resolution,
            f"{missing_message}, and the Account has no existing "
            f"{definition.label} role contact.",
        )
    resolution["salesforce_contact_id"] = contact_id
    return resolution, ""


def _new_contact_resolution(
    role: MergedRole, warning: str
) -> tuple[dict[str, str], str]:
    """Record that submitted role data describes a contact to be created."""
    return (
        {
            "resolution_action": "create_contact",
            "resolution_source": "submitted_data",
            "source_submission_id": role.source_submission_id,
            "source_role": role.definition.prefix,
        },
        warning,
    )


def _fill_missing_optional_role_fields(
    row: dict[str, str],
    role: MergedRole,
    all_roles: list[MergedRole],
    contacts: list[dict[str, Any]],
    resolution: dict[str, str],
) -> bool:
    """Fill missing title and phone from the resolved contact when available."""
    fallback: dict[str, Any] | None = None
    if resolution.get("resolution_source") == "submitted_role":
        source_role = resolution.get("source_role")
        matched_role = next(
            (
                candidate
                for candidate in all_roles
                if candidate.definition.prefix == source_role
            ),
            None,
        )
        if matched_role is not None:
            fallback = matched_role.values
    else:
        contact_id = resolution.get("salesforce_contact_id")
        fallback = next(
            (
                contact
                for contact in contacts
                if _clean_text(contact.get("Id")) == contact_id
            ),
            None,
        )

    if fallback is None:
        return False
    prefix = role.definition.prefix
    copied_value = False
    if role.definition.title_field is not None and not row[f"{prefix}_title"]:
        fallback_title = _first_nonblank(
            fallback.get("title"),
            fallback.get("Title"),
        )
        if fallback_title:
            row[f"{prefix}_title"] = fallback_title
            copied_value = True
    if not row[f"{prefix}_phone"]:
        fallback_phone = _first_nonblank(
            fallback.get("phone"),
            fallback.get("Phone"),
        )
        if fallback_phone:
            row[f"{prefix}_phone"] = fallback_phone
            copied_value = True
    return copied_value


def write_staged_profile_updates(
    rows: list[dict[str, str]],
    output_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Atomically publish one timestamped ``profile_updates.csv`` snapshot."""
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    folder_name = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = _available_snapshot_path(output_dir, folder_name)
    temporary_path = output_dir / f".{folder_name}-{uuid4().hex}.tmp"
    try:
        temporary_path.mkdir()
        _write_profile_updates_csv(
            temporary_path / "profile_updates.csv",
            rows,
        )
        os.replace(temporary_path, final_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise
    return final_path


def _write_profile_updates_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _available_snapshot_path(output_dir: Path, folder_name: str) -> Path:
    candidate = output_dir / folder_name
    number = 1
    while candidate.exists():
        candidate = output_dir / f"{folder_name}-{number:02d}"
        number += 1
    return candidate


def _submission_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        _clean_text(record.get("CreatedDate")),
        _clean_text(record.get("Id")),
    )


def _is_key_update(record: dict[str, Any]) -> bool:
    return record.get("Type__c") == "Key Data"


def _where_in(field_name: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{escape_soql_string(value)}'" for value in sorted(values))
    return f"{field_name} IN ({quoted})"


def _unique_text_values(values: Any) -> list[str]:
    return sorted({_clean_text(value) for value in values if _clean_text(value)})


def _account_name(account: dict[str, Any] | None, merged: dict[str, Any]) -> str:
    if account is not None and _clean_text(account.get("Name")):
        return _clean_text(account.get("Name"))
    relationship = merged.get("Account__r")
    if isinstance(relationship, dict):
        return _clean_text(relationship.get("Name"))
    return ""


def _first_nonblank(*values: Any) -> str:
    for value in values:
        if _has_value(value):
            return _display_value(value)
    return ""


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    return not isinstance(value, str) or bool(value.strip())


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip()


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalized(value: Any) -> str:
    return _clean_text(value).casefold()
