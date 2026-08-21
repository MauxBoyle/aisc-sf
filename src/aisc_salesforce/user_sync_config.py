"""Read-only validation for the participant user-provisioning profiles."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .salesforce import SalesforceClient


class ParticipantProfile(StrEnum):
    """The Salesforce Profile IDs supported by participant provisioning."""

    PARTICIPANT = "00e5w000000k7KfAAI"
    PRINCIPAL = "00e5w000000kDqiAAE"
    AP = "00e5w000000kDqdAAE"
    QC = "00e5w000000kDqnAAE"
    RAS = "00e5w000000kDqsAAE"


PROFILE_CONFIGURATION = {
    ParticipantProfile.PARTICIPANT: ("PARTICIPANT_PROFILE_ID", "Participant"),
    ParticipantProfile.PRINCIPAL: (
        "PARTICIPANT_PRINCIPAL_PROFILE_ID",
        "Participant Principal",
    ),
    ParticipantProfile.AP: ("PARTICIPANT_AP_PROFILE_ID", "Participant AP"),
    ParticipantProfile.QC: ("PARTICIPANT_QC_PROFILE_ID", "Participant QC"),
    ParticipantProfile.RAS: ("PARTICIPANT_RAS_PROFILE_ID", "Participant RAS"),
}


class UserSyncConfigError(RuntimeError):
    """The participant Profile configuration does not match its contract."""


def get_participant_profile_configuration(
    environment: dict[str, str],
) -> dict[ParticipantProfile, str]:
    """Read and verify the configured Profile IDs before contacting Salesforce."""
    configured_profiles: dict[ParticipantProfile, str] = {}
    errors: list[str] = []
    for profile, (variable_name, _) in PROFILE_CONFIGURATION.items():
        configured_id = environment.get(variable_name, "").strip()
        if not configured_id:
            errors.append(f"{variable_name} is required")
        elif configured_id != profile.value:
            errors.append(
                f"{variable_name} must be {profile.value}, not {configured_id}"
            )
        else:
            configured_profiles[profile] = configured_id
    if errors:
        raise UserSyncConfigError("Invalid user sync configuration: " + "; ".join(errors))
    return configured_profiles


class UserSyncConfigValidator:
    """Confirm configured participant Profiles exist and retain their names."""

    def __init__(self, client: SalesforceClient):
        self.client = client

    def validate(self, environment: dict[str, str]) -> None:
        """Run only Salesforce reads and raise when the contract is not met."""
        configured_profiles = get_participant_profile_configuration(environment)
        ids = ", ".join(f"'{profile_id}'" for profile_id in configured_profiles.values())
        records = self.client.query_records(
            "Profile", ["Id", "Name"], where=f"Id IN ({ids})"
        )
        records_by_id = {
            record.get("Id"): record
            for record in records
            if isinstance(record, dict) and isinstance(record.get("Id"), str)
        }
        errors: list[str] = []
        for profile, configured_id in configured_profiles.items():
            _, expected_name = PROFILE_CONFIGURATION[profile]
            record: dict[str, Any] | None = records_by_id.get(configured_id)
            if record is None:
                errors.append(f"{expected_name} Profile {configured_id} was not found")
                continue
            actual_name = record.get("Name")
            if actual_name != expected_name:
                errors.append(
                    f"{expected_name} Profile {configured_id} expected Name "
                    f"{expected_name!r}; got {actual_name!r}"
                )
        if errors:
            raise UserSyncConfigError("; ".join(errors))
