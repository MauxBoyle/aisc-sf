"""Read-only planning for reconciling a Contact with a participant User.

The functions in this module produce a proposal only.  They deliberately use
``query_records`` and contain no Salesforce create, update, or delete calls.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .account_roles import ACCOUNT_ROLE_DEFINITIONS, AccountRole
from .profile_updates import escape_soql_string
from .required_profile_rules import AccountRoleAssignment, determine_required_profile
from .salesforce import SalesforceClient
from .user_field_policies import (
    alias,
    community_nickname,
    contact_email,
    contact_first_name,
    contact_last_name,
    fixed_localization_fields,
    normalized_name_component,
    time_zone,
    username_from_email,
)
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
    "ProfileId",
    "Username",
    "Alias",
    "CommunityNickname",
    "TimeZoneSidKey",
    "LocaleSidKey",
    "LanguageLocaleKey",
    "EmailEncodingKey",
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
    "ProfileId",
    "Username",
    "Alias",
    "TimeZoneSidKey",
    "LocaleSidKey",
    "LanguageLocaleKey",
    "EmailEncodingKey",
)
APPLY_FIELD_ORDER = ("ProfileId", "FirstName", "LastName", "Email", "Username")
_CONTACT_TO_USER = {
    "FirstName": "FirstName",
    "LastName": "LastName",
    "Email": "Email",
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
    alias_collisions: tuple[dict[str, Any], ...] = ()
    community_nickname_collisions: tuple[dict[str, Any], ...] = ()
    # This deliberately excludes Alias, CommunityNickname, and localization.
    # It is the small optimistic-lock snapshot used by a reviewed apply plan.
    expected_current_user: tuple[tuple[str, str], ...] | None = None

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
            "alias_collisions": [dict(item) for item in self.alias_collisions],
            "community_nickname_collisions": [
                dict(item) for item in self.community_nickname_collisions
            ],
            "proposed_operation": self.proposed_operation,
            "proposed_create": (
                dict(self.proposed_create) if self.proposed_create is not None else None
            ),
            "field_changes": [item.as_dict() for item in self.field_changes],
            "blockers": [item.as_dict() for item in self.blockers],
            "expected_current_user": (
                dict(self.expected_current_user)
                if self.expected_current_user is not None
                else None
            ),
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
    alias_matches: Iterable[Mapping[str, Any]] = (),
    community_nickname_matches: Iterable[Mapping[str, Any]] = (),
    community_nickname_required: bool = False,
    clock: Callable[[], date] = date.today,
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
    desired, blockers = _desired_user(
        contact, clock, community_nickname_required=community_nickname_required
    )
    desired_field_order = _desired_field_order(community_nickname_required)
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
    alias_collisions = _identifier_collisions(alias_matches, linked_ids)
    if alias_collisions:
        blockers.append(
            ReconciliationBlocker(
                "alias_collision", "Another User already owns the desired Alias."
            )
        )
    nickname_collisions = _identifier_collisions(community_nickname_matches, linked_ids)
    if nickname_collisions:
        blockers.append(
            ReconciliationBlocker(
                "community_nickname_collision",
                "Another User already owns the desired Community Nickname.",
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
        desired_items = tuple((field, desired[field]) for field in desired_field_order)
        if not active:
            operation, create = "create", desired_items
        else:
            changes = tuple(
                UserFieldChange(field, _text(active[0].get(field)), desired[field])
                for field in desired_field_order
                if not _field_values_equal(field, active[0].get(field), desired[field])
            )
            operation = "update" if changes else "none"
    return UserReconciliationPlan(
        contact_id,
        tuple((field, desired.get(field, "")) for field in desired_field_order),
        required_profile,
        tuple(role_assignments),
        tuple(map(_user_snapshot, active)),
        tuple(map(_user_snapshot, inactive)),
        collisions,
        operation,
        create,
        changes,
        tuple(blockers),
        alias_collisions,
        nickname_collisions,
        (
            tuple((field, _text(active[0].get(field))) for field in APPLY_FIELD_ORDER)
            if len(active) == 1
            else None
        ),
    )


class ReconciliationPlanError(ValueError):
    """A saved reconciliation plan is not safe to use."""


@dataclass(frozen=True)
class UserReconciliationApplyResult:
    """The machine-readable outcome of an allowed User update."""

    contact_id: str
    user_id: str
    fields: tuple[dict[str, str], ...]
    events: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "user_id": self.user_id,
            "fields": [dict(item) for item in self.fields],
            "events": [dict(item) for item in self.events],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)


def load_user_reconciliation_plan(path: Path) -> UserReconciliationPlan:
    """Load a reviewed JSON plan, rejecting incomplete or hand-shaped input."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationPlanError(f"Could not read reconciliation plan: {error}") from error
    if not isinstance(raw, dict):
        raise ReconciliationPlanError("Reconciliation plan must be a JSON object.")
    required = {"contact_id", "desired_user", "active_users", "blockers", "expected_current_user"}
    if not required.issubset(raw):
        raise ReconciliationPlanError("Reconciliation plan is missing required review data.")
    if not isinstance(raw["contact_id"], str) or not raw["contact_id"].strip():
        raise ReconciliationPlanError("Reconciliation plan has no Contact ID.")
    desired = raw["desired_user"]
    expected = raw["expected_current_user"]
    active = raw["active_users"]
    if not isinstance(desired, dict) or not isinstance(expected, dict) or not isinstance(active, list):
        raise ReconciliationPlanError("Reconciliation plan has invalid User review data.")
    if (
        len(active) != 1
        or not isinstance(active[0], dict)
        or active[0].get("IsActive") is not True
        or not _text(active[0].get("Id"))
    ):
        raise ReconciliationPlanError("Reconciliation plan must target exactly one active User.")
    for field in APPLY_FIELD_ORDER:
        if not isinstance(desired.get(field), str) or not isinstance(expected.get(field), str):
            raise ReconciliationPlanError(f"Reconciliation plan is missing {field} review values.")
    if _text(desired.get("ContactId")) != raw["contact_id"].strip():
        raise ReconciliationPlanError("Reconciliation plan Contact IDs do not match.")
    # Reconstructing every descriptive field is unnecessary for apply safety;
    # the fresh plan is the authority for all Salesforce-derived context.
    return UserReconciliationPlan(
        raw["contact_id"].strip(), tuple(desired.items()), raw.get("required_profile"), (),
        (dict(active[0]),), (), (), raw.get("proposed_operation"), None, (),
        tuple(ReconciliationBlocker(str(item.get("code", "")), str(item.get("message", ""))) for item in raw["blockers"] if isinstance(item, dict)),
        expected_current_user=tuple((field, expected[field]) for field in APPLY_FIELD_ORDER),
    )


class UserReconciliationService:
    """Orchestrate the focused Salesforce reads used by the reconciliation plan."""

    def __init__(
        self, client: SalesforceClient, *, clock: Callable[[], date] = date.today
    ):
        self.client = client
        self.clock = clock

    def plan(
        self,
        contact_id: str,
        environment: dict[str, str],
        *,
        community_nickname_required: bool = False,
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
        email, comparison, _ = contact_email(contact)
        username = username_from_email(email)
        username_matches = (
            self.client.query_records(
                "User", USER_FIELDS, where=_where("Username", username)
            )
            if username and comparison
            else []
        )
        alias_value = alias(
            contact.get("FirstName"), contact.get("LastName"), self.clock
        )
        alias_matches = (
            self.client.query_records(
                "User", USER_FIELDS, where=_where("Alias", alias_value)
            )
            if alias_value
            else []
        )
        nickname_value = community_nickname(
            contact.get("FirstName"),
            contact.get("LastName"),
            self.clock,
            required=community_nickname_required,
        )
        nickname_matches = (
            self.client.query_records(
                "User", USER_FIELDS, where=_where("CommunityNickname", nickname_value)
            )
            if nickname_value
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
            alias_matches=alias_matches,
            community_nickname_matches=nickname_matches,
            community_nickname_required=community_nickname_required,
            clock=self.clock,
            configuration_error=configuration_error,
        )

    def apply(
        self,
        reviewed_plan: UserReconciliationPlan,
        contact_id: str,
        environment: dict[str, str],
    ) -> UserReconciliationApplyResult:
        """Re-read Salesforce, then apply only a still-current reviewed update."""
        if reviewed_plan.contact_id != contact_id.strip():
            raise ReconciliationPlanError("Contact ID does not match the reviewed plan.")
        fresh = self.plan(contact_id, environment)
        _validate_apply_plan(reviewed_plan, fresh)
        user_id = _text(fresh.active_users[0].get("Id"))
        desired = dict(fresh.desired_user)
        current = dict(fresh.expected_current_user or ())
        values = {
            field: desired[field]
            for field in APPLY_FIELD_ORDER
            if not _field_values_equal(field, current.get(field), desired[field])
        }
        # A single PATCH keeps the five related identity fields together.  No
        # User fields outside this explicit allow-list can reach Salesforce.
        if values:
            self.client.update_record("User", user_id, values)
        fields = tuple(
            {
                "field": field,
                "status": "applied" if field in values else "skipped",
                "current": current.get(field, ""),
                "desired": desired[field],
            }
            for field in APPLY_FIELD_ORDER
        )
        identity_fields = [field for field in ("Email", "Username") if field in values]
        events: tuple[dict[str, Any], ...] = ()
        if identity_fields:
            events = (
                {
                    "type": "login_identity_changed",
                    "user_id": user_id,
                    "fields": identity_fields,
                },
            )
        return UserReconciliationApplyResult(fresh.contact_id, user_id, fields, events)

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
    clock: Callable[[], date],
    *,
    community_nickname_required: bool,
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
    email, comparison, warnings = contact_email(contact)
    desired["FirstName"] = contact_first_name(contact)
    desired["LastName"] = contact_last_name(contact)
    desired["Email"] = email
    desired["Username"] = username_from_email(email)
    desired["Alias"] = alias(desired["FirstName"], desired["LastName"], clock)
    nickname = community_nickname(
        desired["FirstName"],
        desired["LastName"],
        clock,
        required=community_nickname_required,
    )
    if nickname is not None:
        desired["CommunityNickname"] = nickname
    desired["TimeZoneSidKey"] = time_zone(contact)
    desired.update(fixed_localization_fields())
    for field in ("FirstName", "LastName"):
        if not normalized_name_component(desired[field]):
            blockers.append(
                ReconciliationBlocker(
                    "invalid_name", f"Contact {field} has no usable letters or digits."
                )
            )
    if not desired["Username"] or not comparison:
        blockers.append(
            ReconciliationBlocker(
                "invalid_email",
                warnings[0] if warnings else "Contact Email is required.",
            )
        )
    return desired, blockers


def _identifier_collisions(
    matches: Iterable[Mapping[str, Any]], linked_ids: set[str]
) -> tuple[dict[str, Any], ...]:
    return tuple(
        _user_snapshot(item)
        for item in matches
        if _text(item.get("Id")) not in linked_ids
    )


def _desired_field_order(community_nickname_required: bool) -> tuple[str, ...]:
    if not community_nickname_required:
        return DESIRED_FIELD_ORDER
    alias_index = DESIRED_FIELD_ORDER.index("Alias") + 1
    return (
        *DESIRED_FIELD_ORDER[:alias_index],
        "CommunityNickname",
        *DESIRED_FIELD_ORDER[alias_index:],
    )


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


def _field_values_equal(field: str, current: Any, desired: Any) -> bool:
    """Compare login email values without treating cosmetic casing as a change."""
    if field in {"Email", "Username"}:
        return _text(current).casefold() == _text(desired).casefold()
    return _text(current) == _text(desired)


def _validate_apply_plan(
    reviewed: UserReconciliationPlan, fresh: UserReconciliationPlan
) -> None:
    """Reject stale, blocked, create, and no-op plans before any PATCH."""
    if reviewed.blockers:
        raise ReconciliationPlanError("Reviewed reconciliation plan is blocked; no User was updated.")
    if reviewed.proposed_operation != "update":
        raise ReconciliationPlanError("Reviewed reconciliation plan is not an active-User update.")
    if fresh.blockers:
        raise ReconciliationPlanError("Fresh reconciliation plan is blocked; no User was updated.")
    if fresh.proposed_operation != "update" or len(fresh.active_users) != 1:
        raise ReconciliationPlanError("Fresh reconciliation plan is not an active-User update.")
    reviewed_expected = dict(reviewed.expected_current_user or ())
    fresh_expected = dict(fresh.expected_current_user or ())
    reviewed_user_id = _text(reviewed.active_users[0].get("Id")) if len(reviewed.active_users) == 1 else ""
    fresh_user_id = _text(fresh.active_users[0].get("Id"))
    if (
        reviewed.contact_id != fresh.contact_id
        or reviewed_user_id != fresh_user_id
        or reviewed_expected != fresh_expected
        or dict(reviewed.desired_user) != dict(fresh.desired_user)
    ):
        raise ReconciliationPlanError("Reviewed reconciliation plan is stale or no longer safe to apply.")


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
