"""Safe creation of missing Experience Cloud participant Users.

This service is intentionally narrow: it creates a User only after a caller has
completed its own business workflow.  All reads needed to decide whether a
create is safe happen immediately before the create request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from .profile_updates import escape_soql_string
from .salesforce import SalesforceClient, SalesforceError
from .user_reconciliation import USER_FIELDS, UserReconciliationService


class ProvisioningConfigurationError(ValueError):
    """Deployment settings needed for external User creation are missing."""


@dataclass(frozen=True)
class ExternalUserProvisioningConfig:
    """The organization-specific constraints for an external User license."""

    license_name: str
    role_id: str | None = None

    @classmethod
    def from_environment(
        cls, environment: dict[str, str]
    ) -> ExternalUserProvisioningConfig:
        values = {
            "EXTERNAL_USER_LICENSE_NAME": environment.get(
                "EXTERNAL_USER_LICENSE_NAME", ""
            ).strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ProvisioningConfigurationError(
                "Missing external User provisioning configuration: "
                + ", ".join(missing)
            )
        role_id = environment.get("EXTERNAL_USER_ROLE_ID", "").strip() or None
        return cls(values["EXTERNAL_USER_LICENSE_NAME"], role_id)


@dataclass(frozen=True)
class AccountEligibilityPolicy:
    """A caller-owned Account field/value rule for external User creation."""

    field: str
    value: str

    @classmethod
    def from_environment(
        cls, environment: dict[str, str]
    ) -> AccountEligibilityPolicy:
        values = {
            "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_FIELD": environment.get(
                "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_FIELD", ""
            ).strip(),
            "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_VALUE": environment.get(
                "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_VALUE", ""
            ).strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ProvisioningConfigurationError(
                "Missing external User provisioning configuration: "
                + ", ".join(missing)
            )
        return cls(
            values["EXTERNAL_USER_ACCOUNT_ELIGIBILITY_FIELD"],
            values["EXTERNAL_USER_ACCOUNT_ELIGIBILITY_VALUE"],
        )


@dataclass(frozen=True)
class ProvisioningOutcome:
    """One contact's durable provisioning result or actionable reason to stop."""

    contact_id: str
    action: str
    message: str = ""
    code: str = ""
    user_id: str = ""
    warning: str = ""


class ParticipantUserProvisioningError(RuntimeError):
    """A preflight or Salesforce failure that must leave the batch retryable."""

    def __init__(self, outcome: ProvisioningOutcome):
        super().__init__(outcome.message)
        self.outcome = outcome


class ParticipantUserProvisioningService:
    """Preflight and create external Users for Contacts that require a Profile."""

    def __init__(
        self, client: SalesforceClient, *, clock: Callable[[], date] = date.today
    ):
        self.client = client
        self.clock = clock
        self._planner = UserReconciliationService(client, clock=clock)

    def provision(
        self,
        contact_ids: set[str],
        environment: dict[str, str],
        *,
        account_eligibility_policy: AccountEligibilityPolicy | None = None,
    ) -> tuple[ProvisioningOutcome, ...]:
        """Create each eligible missing User, or raise with an actionable blocker.

        A linked active User is a successful no-op. A supplied Account policy is
        applied only to this call; without one, no Account eligibility field is
        read or enforced.
        """
        try:
            config = ExternalUserProvisioningConfig.from_environment(environment)
        except ProvisioningConfigurationError as error:
            raise ParticipantUserProvisioningError(
                ProvisioningOutcome(
                    "",
                    "failed",
                    str(error),
                    "provisioning_configuration_invalid",
                )
            ) from error
        outcomes: list[ProvisioningOutcome] = []
        for contact_id in sorted(
            value.strip() for value in contact_ids if value.strip()
        ):
            plan = self._planner.plan(contact_id, environment)
            if plan.required_profile is None and any(
                blocker.code == "no_qualifying_roles" for blocker in plan.blockers
            ):
                outcomes.append(
                    ProvisioningOutcome(
                        contact_id,
                        "skipped",
                        "Contact has no qualifying participant role.",
                    )
                )
                continue
            if plan.active_users:
                outcomes.append(
                    ProvisioningOutcome(
                        contact_id,
                        "reused",
                        user_id=str(plan.active_users[0].get("Id", "")),
                    )
                )
                continue
            if plan.blockers or plan.proposed_create is None:
                blocker = plan.blockers[0] if plan.blockers else None
                raise ParticipantUserProvisioningError(
                    ProvisioningOutcome(
                        contact_id,
                        "failed",
                        blocker.message
                        if blocker
                        else "User creation could not be planned.",
                        blocker.code if blocker else "create_not_planned",
                    )
                )
            payload = dict(plan.proposed_create)
            warning = self._validate_external_requirements(
                contact_id, payload, config, account_eligibility_policy
            )

            # A concurrent workflow may have created the User while preflight ran.
            active = self.client.query_records(
                "User",
                USER_FIELDS,
                where=f"ContactId = '{escape_soql_string(contact_id)}' AND IsActive = TRUE",
            )
            if active:
                outcomes.append(
                    ProvisioningOutcome(
                        contact_id,
                        "reused",
                        "An active linked User appeared during recheck.",
                        user_id=str(active[0].get("Id", "")),
                        warning=warning,
                    )
                )
                continue
            if config.role_id:
                payload["UserRoleId"] = config.role_id
            payload["IsActive"] = True
            try:
                user_id = self.client.create_record("User", payload)
            except SalesforceError as error:
                raise ParticipantUserProvisioningError(
                    ProvisioningOutcome(
                        contact_id,
                        "failed",
                        str(error),
                        error.error_code or "salesforce_error",
                    )
                ) from error
            outcomes.append(
                ProvisioningOutcome(
                    contact_id,
                    "created",
                    f"User {payload['Email']} created",
                    user_id=user_id,
                    warning=warning,
                )
            )
        return tuple(outcomes)

    def _validate_external_requirements(
        self,
        contact_id: str,
        payload: dict[str, Any],
        config: ExternalUserProvisioningConfig,
        account_eligibility_policy: AccountEligibilityPolicy | None,
    ) -> str:
        contact_rows = self.client.query_records(
            "Contact",
            ["Id", "AccountId"],
            where=f"Id = '{escape_soql_string(contact_id)}'",
        )
        contact = next(
            (row for row in contact_rows if row.get("Id") == contact_id), None
        )
        account_id = str((contact or {}).get("AccountId") or "").strip()
        if not account_id:
            self._fail(
                contact_id,
                "contact_account_missing",
                "Contact must be related to an Account.",
            )
        account_rows = self.client.query_records(
            "Account",
            [
                "Id",
                "OwnerId",
                *(
                    [account_eligibility_policy.field]
                    if account_eligibility_policy is not None
                    else []
                ),
            ],
            where=f"Id = '{escape_soql_string(account_id)}'",
        )
        account = next(
            (row for row in account_rows if row.get("Id") == account_id), None
        )
        if account is None:
            self._fail(
                contact_id, "account_not_found", "Contact Account could not be read."
            )
        if account_eligibility_policy is not None and (
            str(account.get(account_eligibility_policy.field) or "").strip()
            != account_eligibility_policy.value
        ):
            self._fail(
                contact_id,
                "account_not_eligible",
                f"Account {account_eligibility_policy.field} must be {account_eligibility_policy.value!r}.",
            )
        owner_id = str(account.get("OwnerId") or "").strip()
        owners = (
            self.client.query_records(
                "User",
                ["Id", "IsActive", "UserRoleId", "ContactId"],
                where=f"Id = '{escape_soql_string(owner_id)}'",
            )
            if owner_id
            else []
        )
        owner = next((row for row in owners if row.get("Id") == owner_id), None)
        if (
            owner is None
            or owner.get("IsActive") is not True
            or not str(owner.get("UserRoleId") or "").strip()
            or str(owner.get("ContactId") or "").strip()
        ):
            self._fail(
                contact_id,
                "account_owner_invalid",
                "Account owner must be an active internal User with a role.",
            )

        profile_id = str(payload.get("ProfileId") or "")
        profiles = self.client.query_records(
            "Profile",
            ["Id", "Name", "UserLicense.Name"],
            where=f"Id = '{escape_soql_string(profile_id)}'",
        )
        profile = next((row for row in profiles if row.get("Id") == profile_id), None)
        license_name = str(
            ((profile or {}).get("UserLicense") or {}).get("Name") or ""
        ).strip()
        if license_name != config.license_name:
            self._fail(
                contact_id,
                "profile_license_mismatch",
                f"Profile must use external User license {config.license_name!r}.",
            )
        username = str(payload.get("Username") or "")
        matches = self.client.query_records(
            "User", ["Id"], where=f"Username = '{escape_soql_string(username)}'"
        )
        if matches:
            self._fail(
                contact_id,
                "username_collision",
                "Another User already owns the desired username.",
            )
        return self._validate_license_capacity(contact_id, config.license_name)

    def _validate_license_capacity(self, contact_id: str, license_name: str) -> str:
        try:
            rows = self.client.query_records(
                "UserLicense",
                ["Id", "Name", "TotalLicenses", "UsedLicenses"],
                where=f"Name = '{escape_soql_string(license_name)}'",
            )
        except SalesforceError:
            # Some org permissions do not allow UserLicense reads. Salesforce
            # remains the final authority at create time, so this is a warning.
            return "UserLicense capacity could not be queried; Salesforce will enforce capacity during creation."
        license_row = next(
            (row for row in rows if row.get("Name") == license_name), None
        )
        if license_row is None:
            self._fail(
                contact_id,
                "license_not_found",
                f"External User license {license_name!r} was not found.",
            )
        try:
            if int(license_row.get("UsedLicenses", 0)) >= int(
                license_row.get("TotalLicenses", 0)
            ):
                self._fail(
                    contact_id,
                    "license_capacity_exhausted",
                    f"External User license {license_name!r} has no available licenses.",
                )
        except (TypeError, ValueError):
            self._fail(
                contact_id,
                "license_capacity_invalid",
                "External User license capacity values are invalid.",
            )
        return ""

    @staticmethod
    def _fail(contact_id: str, code: str, message: str) -> None:
        raise ParticipantUserProvisioningError(
            ProvisioningOutcome(contact_id, "failed", message, code)
        )
