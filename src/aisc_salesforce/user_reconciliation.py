"""Read-only planning for reconciling a Contact with a participant User.

The functions in this module produce a proposal only.  They deliberately use
``query_records`` and contain no Salesforce create, update, or delete calls.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .account_roles import ACCOUNT_ROLE_DEFINITIONS, AccountRole
from .contact_resolution import normalize_email
from .profile_updates import escape_soql_string
from .required_profile_rules import AccountRoleAssignment, determine_required_profile
from .salesforce import SalesforceClient
from .user_sync_config import (
    PROFILE_CONFIGURATION,
    ParticipantProfile,
    UserSyncConfigError,
    get_participant_profile_configuration,
)

CONTACT_FIELDS = [
    "Id",
    "FirstName",
    "LastName",
    "Email",
    "MailingStreet",
    "MailingCity",
    "MailingState",
    "MailingPostalCode",
    "MailingCountry",
]
USER_FIELDS = [
    "Id",
    "IsActive",
    "ContactId",
    "FirstName",
    "LastName",
    "Email",
    "Street",
    "City",
    "State",
    "PostalCode",
    "Country",
    "ProfileId",
    "Username",
]
ACCOUNT_FIELDS = [
    "Id",
    "ParentId",
    "Cert_Certification_Status__c",
    *(definition.account_lookup for definition in ACCOUNT_ROLE_DEFINITIONS),
]
PROFILE_FIELDS = ["Id", "Name"]
DESIRED_FIELD_ORDER = (
    "ContactId",
    "FirstName",
    "LastName",
    "Email",
    "Street",
    "City",
    "State",
    "PostalCode",
    "Country",
    "ProfileId",
    "Username",
)
_CONTACT_TO_USER = {
    "FirstName": "FirstName",
    "LastName": "LastName",
    "Email": "Email",
    "MailingStreet": "Street",
    "MailingCity": "City",
    "MailingState": "State",
    "MailingPostalCode": "PostalCode",
    "MailingCountry": "Country",
}


@dataclass(frozen=True)
class ReconciliationBlocker:
    """A reason the proposed User change must not be applied."""

    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class UserFieldChange:
    """One field whose active User value differs from the desired value."""

    field: str
    current: str
    desired: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "current": self.current, "desired": self.desired}


@dataclass(frozen=True)
class AccountRolePlan:
    """An Account role found for the Contact, including family context."""

    role: str
    account_id: str
    certification_status: str
    is_multi_account_family: bool

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "role": self.role,
            "account_id": self.account_id,
            "certification_status": self.certification_status,
            "is_multi_account_family": self.is_multi_account_family,
        }


@dataclass(frozen=True)
class UserReconciliationPlan:
    """A JSON-safe, read-only proposal for one Contact's participant User."""

    contact_id: str
    desired_user: tuple[tuple[str, str], ...]
    required_profile: str | None
    role_assignments: tuple[AccountRolePlan, ...]
    active_users: tuple[dict[str, Any], ...]
    inactive_users: tuple[dict[str, Any], ...]
    username_collisions: tuple[dict[str, Any], ...]
    proposed_operation: str | None
    proposed_create: tuple[tuple[str, str], ...] | None
    field_changes: tuple[UserFieldChange, ...]
    blockers: tuple[ReconciliationBlocker, ...]

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON contract for command output."""
        return {
            "contact_id": self.contact_id,
            "desired_user": dict(self.desired_user),
            "required_profile": self.required_profile,
            "role_assignments": [item.as_dict() for item in self.role_assignments],
            "active_users": [dict(item) for item in self.active_users],
            "inactive_users": [dict(item) for item in self.inactive_users],
            "username_collisions": [dict(item) for item in self.username_collisions],
            "proposed_operation": self.proposed_operation,
            "proposed_create": (
                dict(self.proposed_create) if self.proposed_create is not None else None
            ),
            "field_changes": [item.as_dict() for item in self.field_changes],
            "blockers": [item.as_dict() for item in self.blockers],
        }

    def to_json(self) -> str:
        """Serialize using stable keys so automation can compare results."""
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)


def build_user_reconciliation_plan(
    contact: Mapping[str, Any] | None,
    linked_users: Iterable[Mapping[str, Any]],
    accounts: Iterable[Mapping[str, Any]],
    family_accounts: Iterable[Mapping[str, Any]],
    profile_records: Iterable[Mapping[str, Any]],
    configured_profiles: Mapping[ParticipantProfile, str] | None,
    username_matches: Iterable[Mapping[str, Any]],
    *,
    configuration_error: str | None = None,
) -> UserReconciliationPlan:
    """Build a plan from already-read Salesforce records, without side effects."""
    if contact is None or not _text(contact.get("Id")):
        return UserReconciliationPlan(
            "",
            (),
            None,
            (),
            (),
            (),
            (),
            None,
            None,
            (),
            (ReconciliationBlocker("contact_not_found", "Contact was not found."),),
        )
    contact_id = _text(contact.get("Id"))
    desired, blockers = _desired_user(contact)
    role_assignments = _role_assignments(accounts, contact_id, family_accounts)
    decision = determine_required_profile(
        [
            AccountRoleAssignment(
                AccountRole(item.role), item.account_id, item.certification_status
            )
            for item in role_assignments
        ],
        {item.account_id for item in role_assignments if item.is_multi_account_family},
    )
    required_profile: str | None = None
    if decision.profile is None:
        blockers.append(
            ReconciliationBlocker(
                "no_qualifying_roles", decision.skip_reason or "No qualifying roles."
            )
        )
    else:
        required_profile = decision.profile.value
        if configuration_error:
            blockers.append(
                ReconciliationBlocker("profile_configuration", configuration_error)
            )
        elif (
            configured_profiles is None
            or configured_profiles.get(decision.profile) != required_profile
        ):
            blockers.append(
                ReconciliationBlocker(
                    "profile_configuration",
                    "Required participant Profile is not configured.",
                )
            )
        else:
            expected_name = PROFILE_CONFIGURATION[decision.profile][1]
            names = {
                _text(item.get("Id")): _text(item.get("Name"))
                for item in profile_records
            }
            if names.get(required_profile) != expected_name:
                blockers.append(
                    ReconciliationBlocker(
                        "profile_configuration",
                        f"{expected_name} Profile {required_profile} is missing or has the wrong Name.",
                    )
                )
            else:
                desired["ProfileId"] = required_profile

    active, inactive = _split_users(linked_users)
    linked_ids = {_text(item.get("Id")) for item in (*active, *inactive)}
    collisions = tuple(
        _user_snapshot(item)
        for item in username_matches
        if _text(item.get("Id")) not in linked_ids
    )
    if collisions:
        blockers.append(
            ReconciliationBlocker(
                "username_collision", "Another User already owns the desired username."
            )
        )
    if len(active) > 1:
        blockers.append(
            ReconciliationBlocker(
                "multiple_active_users",
                "More than one active User is linked to this Contact.",
            )
        )

    operation: str | None = None
    create: tuple[tuple[str, str], ...] | None = None
    changes: tuple[UserFieldChange, ...] = ()
    if not blockers:
        desired_items = tuple((field, desired[field]) for field in DESIRED_FIELD_ORDER)
        if not active:
            operation, create = "create", desired_items
        else:
            changes = tuple(
                UserFieldChange(field, _text(active[0].get(field)), desired[field])
                for field in DESIRED_FIELD_ORDER
                if _text(active[0].get(field)) != desired[field]
            )
            operation = "update" if changes else "none"
    return UserReconciliationPlan(
        contact_id,
        tuple((field, desired.get(field, "")) for field in DESIRED_FIELD_ORDER),
        required_profile,
        tuple(role_assignments),
        tuple(map(_user_snapshot, active)),
        tuple(map(_user_snapshot, inactive)),
        collisions,
        operation,
        create,
        changes,
        tuple(blockers),
    )


class UserReconciliationService:
    """Orchestrate the focused Salesforce reads used by the reconciliation plan."""

    def __init__(self, client: SalesforceClient):
        self.client = client

    def plan(
        self, contact_id: str, environment: dict[str, str]
    ) -> UserReconciliationPlan:
        """Read the smallest useful record sets and return a no-write proposal."""
        contact_id = contact_id.strip()
        contact_rows = self.client.query_records(
            "Contact", CONTACT_FIELDS, where=_where("Id", contact_id)
        )
        contact = next(
            (row for row in contact_rows if _text(row.get("Id")) == contact_id), None
        )
        if contact is None:
            return build_user_reconciliation_plan(None, (), (), (), (), None, ())
        users = self.client.query_records(
            "User", USER_FIELDS, where=_where("ContactId", contact_id)
        )
        accounts = self.client.query_records(
            "Account", ACCOUNT_FIELDS, where=_role_where(contact_id)
        )
        family = self._read_family_accounts(accounts)
        configuration_error: str | None = None
        configured: dict[ParticipantProfile, str] | None = None
        profiles: list[dict[str, Any]] = []
        try:
            configured = get_participant_profile_configuration(environment)
            ids = ", ".join(f"'{value}'" for value in configured.values())
            profiles = self.client.query_records(
                "Profile", PROFILE_FIELDS, where=f"Id IN ({ids})"
            )
        except UserSyncConfigError as error:
            configuration_error = str(error)
        username, comparison, _ = normalize_email(contact.get("Email"))
        username_matches = (
            self.client.query_records(
                "User", USER_FIELDS, where=_where("Username", username)
            )
            if username and comparison
            else []
        )
        return build_user_reconciliation_plan(
            contact,
            users,
            accounts,
            family,
            profiles,
            configured,
            username_matches,
            configuration_error=configuration_error,
        )

    def _read_family_accounts(
        self, accounts: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        account_list = list(accounts)
        account_ids = _ids(account_list, "Id")
        parent_ids = _ids(account_list, "ParentId")
        result = list(account_list)
        if account_ids:
            result.extend(
                self.client.query_records(
                    "Account", ACCOUNT_FIELDS, where=_where_in("ParentId", account_ids)
                )
            )
        if parent_ids:
            result.extend(
                self.client.query_records(
                    "Account", ACCOUNT_FIELDS, where=_where_in("Id", parent_ids)
                )
            )
            result.extend(
                self.client.query_records(
                    "Account", ACCOUNT_FIELDS, where=_where_in("ParentId", parent_ids)
                )
            )
        unique: dict[str, dict[str, Any]] = {}
        for item in result:
            if item_id := _text(item.get("Id")):
                unique[item_id] = dict(item)
        return list(unique.values())


def render_user_reconciliation_plan(plan: UserReconciliationPlan) -> str:
    """Render a short terminal summary that keeps all decisions visible."""
    lines = [f"User reconciliation plan for Contact {plan.contact_id}:"]
    lines.append(f"required profile: {plan.required_profile or '(none)'}")
    lines.append(f"active linked Users: {len(plan.active_users)}")
    lines.append(f"inactive linked Users (conflicts only): {len(plan.inactive_users)}")
    if plan.blockers:
        lines.append("blockers:")
        lines.extend(f"  - {item.code}: {item.message}" for item in plan.blockers)
    elif plan.proposed_operation == "create":
        lines.append("proposed action: create User (not performed)")
    elif plan.proposed_operation == "update":
        lines.append("proposed action: update User (not performed)")
        lines.extend(
            f"  - {item.field}: {item.current!r} -> {item.desired!r}"
            for item in plan.field_changes
        )
    else:
        lines.append("proposed action: no changes needed")
    lines.append("Read-only: no Salesforce records were changed.")
    return "\n".join(lines)


def _desired_user(
    contact: Mapping[str, Any],
) -> tuple[dict[str, str], list[ReconciliationBlocker]]:
    desired = {"ContactId": _text(contact.get("Id"))}
    blockers: list[ReconciliationBlocker] = []
    for source, target in _CONTACT_TO_USER.items():
        desired[target] = _text(contact.get(source))
        if not desired[target]:
            blockers.append(
                ReconciliationBlocker(
                    "missing_contact_data", f"Contact {source} is required."
                )
            )
    username, comparison, warnings = normalize_email(contact.get("Email"))
    desired["Username"] = username
    if not username or not comparison:
        blockers.append(
            ReconciliationBlocker(
                "invalid_email",
                warnings[0] if warnings else "Contact Email is required.",
            )
        )
    return desired, blockers


def _role_assignments(
    accounts: Iterable[Mapping[str, Any]],
    contact_id: str,
    family: Iterable[Mapping[str, Any]],
) -> list[AccountRolePlan]:
    family_ids = _multi_account_family_ids(family)
    result: list[AccountRolePlan] = []
    for account in accounts:
        account_id = _text(account.get("Id"))
        for definition in ACCOUNT_ROLE_DEFINITIONS:
            if _text(account.get(definition.account_lookup)) == contact_id:
                result.append(
                    AccountRolePlan(
                        str(definition.role),
                        account_id,
                        _text(account.get("Cert_Certification_Status__c")),
                        account_id in family_ids,
                    )
                )
    return result


def _multi_account_family_ids(accounts: Iterable[Mapping[str, Any]]) -> set[str]:
    """Return all IDs in parent/child groups that contain more than one Account."""
    graph: dict[str, set[str]] = defaultdict(set)
    for account in accounts:
        account_id, parent_id = _text(account.get("Id")), _text(account.get("ParentId"))
        if account_id:
            graph.setdefault(account_id, set())
        if account_id and parent_id:
            graph[account_id].add(parent_id)
            graph[parent_id].add(account_id)
    multi: set[str] = set()
    visited: set[str] = set()
    for start in graph:
        if start in visited:
            continue
        stack, component = [start], set()
        while stack:
            item = stack.pop()
            if item not in component:
                component.add(item)
                visited.add(item)
                stack.extend(graph[item] - component)
        if len(component) > 1:
            multi.update(component)
    return multi


def _split_users(
    users: Iterable[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    active, inactive = [], []
    for user in users:
        (active if user.get("IsActive") is True else inactive).append(user)
    return active, inactive


def _user_snapshot(user: Mapping[str, Any]) -> dict[str, Any]:
    return {field: user.get(field) for field in USER_FIELDS if field in user}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _where(field: str, value: str) -> str:
    return f"{field} = '{escape_soql_string(value)}'"


def _where_in(field: str, values: Iterable[str]) -> str:
    return f"{field} IN ({', '.join(repr(value) for value in sorted(set(values)))})"


def _role_where(contact_id: str) -> str:
    return " OR ".join(
        _where(item.account_lookup, contact_id) for item in ACCOUNT_ROLE_DEFINITIONS
    )


def _ids(records: Iterable[Mapping[str, Any]], field: str) -> list[str]:
    return sorted(
        {_text(item.get(field)) for item in records if _text(item.get(field))}
    )
