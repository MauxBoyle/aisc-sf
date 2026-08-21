import pytest

from aisc_salesforce.user_sync_config import (
    ParticipantProfile,
    UserSyncConfigError,
    UserSyncConfigValidator,
    get_participant_profile_configuration,
)


def configured_environment():
    return {
        "PARTICIPANT_PROFILE_ID": ParticipantProfile.PARTICIPANT,
        "PARTICIPANT_PRINCIPAL_PROFILE_ID": ParticipantProfile.PRINCIPAL,
        "PARTICIPANT_AP_PROFILE_ID": ParticipantProfile.AP,
        "PARTICIPANT_QC_PROFILE_ID": ParticipantProfile.QC,
        "PARTICIPANT_RAS_PROFILE_ID": ParticipantProfile.RAS,
    }


def test_participant_profile_contract_contains_expected_ids():
    assert ParticipantProfile.PARTICIPANT == "00e5w000000k7KfAAI"
    assert ParticipantProfile.PRINCIPAL == "00e5w000000kDqiAAE"
    assert ParticipantProfile.AP == "00e5w000000kDqdAAE"
    assert ParticipantProfile.QC == "00e5w000000kDqnAAE"
    assert ParticipantProfile.RAS == "00e5w000000kDqsAAE"


def test_configuration_maps_each_profile_to_its_environment_value():
    assert get_participant_profile_configuration(configured_environment()) == {
        profile: profile.value for profile in ParticipantProfile
    }


def test_configuration_rejects_missing_profile_id():
    environment = configured_environment()
    environment["PARTICIPANT_QC_PROFILE_ID"] = ""

    with pytest.raises(UserSyncConfigError, match="PARTICIPANT_QC_PROFILE_ID"):
        get_participant_profile_configuration(environment)


def test_configuration_rejects_an_id_that_does_not_match_the_contract():
    environment = configured_environment()
    environment["PARTICIPANT_AP_PROFILE_ID"] = "00e000000000000AAA"

    with pytest.raises(UserSyncConfigError, match="PARTICIPANT_AP_PROFILE_ID"):
        get_participant_profile_configuration(environment)


class FakeClient:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def query_records(self, object_name, fields, *, where=None):
        self.calls.append((object_name, fields, where))
        return self.records


def profile_record(profile, name):
    return {"Id": profile.value, "Name": name}


def expected_records():
    return [
        profile_record(ParticipantProfile.PARTICIPANT, "Participant"),
        profile_record(ParticipantProfile.PRINCIPAL, "Participant Principal"),
        profile_record(ParticipantProfile.AP, "Participant AP"),
        profile_record(ParticipantProfile.QC, "Participant QC"),
        profile_record(ParticipantProfile.RAS, "Participant RAS"),
    ]


def test_validator_queries_profiles_and_accepts_matching_records():
    client = FakeClient(expected_records())

    UserSyncConfigValidator(client).validate(configured_environment())

    object_name, fields, where = client.calls[0]
    assert object_name == "Profile"
    assert fields == ["Id", "Name"]
    assert "00e5w000000k7KfAAI" in where


def test_validator_reports_a_missing_profile():
    client = FakeClient(expected_records()[:-1])

    with pytest.raises(UserSyncConfigError, match="Participant RAS.*not found"):
        UserSyncConfigValidator(client).validate(configured_environment())


def test_validator_reports_a_profile_name_mismatch():
    records = expected_records()
    records[2]["Name"] = "Participant Accounts Payable"

    with pytest.raises(UserSyncConfigError, match="Participant AP.*expected.*got"):
        UserSyncConfigValidator(FakeClient(records)).validate(configured_environment())
