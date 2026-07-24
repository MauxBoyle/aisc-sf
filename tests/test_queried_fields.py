import pytest

from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.queried_fields import (
    FieldInventoryError,
    build_queried_field_inventory,
)


def test_inventory_merges_dictionary_and_code_fields_without_duplicates():
    inventory = build_queried_field_inventory(
        {
            "Account": [
                ExportField("Name", "name"),
                ExportField("Dictionary_Only__c", "dictionary_only"),
            ],
            "Case": [
                ExportField("Status", "status"),
                ExportField(
                    "Account.Cert_Certification_Status__c",
                    "certification_status",
                ),
            ],
        }
    )

    assert inventory["Account"].count("Name") == 1
    assert "Dictionary_Only__c" in inventory["Account"]
    assert inventory["Case"].count("Status") == 1


def test_relationship_fields_are_assigned_to_the_owning_object():
    inventory = build_queried_field_inventory()

    assert "Cert_Certification_Status__c" in inventory["Account"]
    assert "BillingCountry" in inventory["Account"]
    assert "Account.Cert_Certification_Status__c" not in inventory["Case"]
    assert "Name" in inventory["Account"]


def test_unknown_relationship_owner_is_rejected_instead_of_silently_ignored():
    with pytest.raises(FieldInventoryError, match="Unknown__r.Value__c"):
        build_queried_field_inventory({"Case": ["Unknown__r.Value__c"]})
