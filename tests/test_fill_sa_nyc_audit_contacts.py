from datetime import UTC, datetime

import pytest

from aisc_salesforce.fill_sa_nyc_audit_contacts import (
    AuditContactMetadataError,
    SANYCAuditContactFillService,
    thirteen_month_cutoff,
)
from aisc_salesforce.salesforce import SalesforceError

NOW = datetime(2026, 9, 22, 15, 30, tzinfo=UTC)
AUDIT_ROLE_FIELDS = (
    "Principal_Contact__c",
    "AP_Contact__c",
    "QC_Contact__c",
    "New_York_Contact__c",
)
ACCOUNT_ROLE_FIELDS = (
    "Cert_Principal_Contact__c",
    "Cert_Accounting_Contact__c",
    "Cert_Marketing_Contact__c",
    "Cert_Safety_Contact__c",
)


class FakeClient:
    def __init__(self, audits):
        self.audits = audits
        self.describes = []
        self.queries = []
        self.updated = []
        self.fail_ids = set()
        self.audit_types = {field: "reference" for field in AUDIT_ROLE_FIELDS}
        self.account_types = {field: "reference" for field in ACCOUNT_ROLE_FIELDS}

    def describe_object(self, object_name):
        self.describes.append(object_name)
        return (
            self.audit_types if object_name == "Cert_Audit__c" else self.account_types
        )

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        return list(self.audits)

    def update_record(self, object_name, record_id, values):
        if record_id in self.fail_ids:
            raise SalesforceError("write failed")
        self.updated.append((object_name, record_id, values))


def audit(audit_id, **values):
    record = {
        "Id": audit_id,
        "Principal_Contact__c": None,
        "AP_Contact__c": None,
        "QC_Contact__c": None,
        "New_York_Contact__c": None,
        "Cert_Account__r": {
            "Cert_Principal_Contact__c": "principal-id",
            "Cert_Accounting_Contact__c": "accounting-id",
            "Cert_Marketing_Contact__c": "quality-id",
            "Cert_Safety_Contact__c": "new-york-id",
        },
    }
    record.update(values)
    return record


def test_metadata_preflight_stops_before_query_when_a_role_is_missing():
    client = FakeClient([])
    client.audit_types.pop("QC_Contact__c")

    with pytest.raises(AuditContactMetadataError, match="QC_Contact__c"):
        SANYCAuditContactFillService(client, now=NOW).run()

    assert client.describes == ["Cert_Audit__c", "Account"]
    assert client.queries == []
    assert client.updated == []


def test_metadata_preflight_rejects_non_reference_account_roles():
    client = FakeClient([])
    client.account_types["Cert_Safety_Contact__c"] = "string"

    with pytest.raises(AuditContactMetadataError, match="Cert_Safety_Contact__c"):
        SANYCAuditContactFillService(client, now=NOW).run()

    assert client.queries == []


def test_thirteen_month_cutoff_uses_utc_and_handles_shorter_months():
    assert thirteen_month_cutoff(datetime(2024, 2, 29, 12, 15, tzinfo=UTC)) == datetime(
        2023, 1, 29, 12, 15, tzinfo=UTC
    )
    assert thirteen_month_cutoff(datetime(2026, 3, 31, 12, 15, tzinfo=UTC)) == datetime(
        2025, 2, 28, 12, 15, tzinfo=UTC
    )


def test_preview_queries_only_qualifying_sa_nyc_participant_audits():
    client = FakeClient([])

    counts = SANYCAuditContactFillService(client, now=NOW).run()

    assert counts.qualifying == 0
    object_name, _, where, order_by = client.queries[0]
    assert object_name == "Cert_Audit__c"
    assert "Cert_Audit_Type__c = 'SA-NYC'" in where
    assert "CreatedDate >= 2025-08-22T15:30:00Z" in where
    assert "Cert_Account__r.NY_Program_Participant__c = true" in where
    assert order_by == "CreatedDate ASC, Id ASC"


def test_preview_reports_all_mappings_without_writing():
    client = FakeClient([audit("audit-1")])
    output = []

    counts = SANYCAuditContactFillService(
        client, now=NOW, output_fn=output.append
    ).run()

    assert counts.would_update == 4
    assert counts.updated == 0
    assert client.updated == []
    assert output == [
        "audit-1 Principal: would update",
        "audit-1 AP: would update",
        "audit-1 QC: would update",
        "audit-1 New York: would update",
    ]


def test_mixed_populated_and_unavailable_roles_are_never_in_patch_payload():
    client = FakeClient(
        [
            audit(
                "audit-1",
                Principal_Contact__c="already-principal",
                Cert_Account__r={
                    "Cert_Principal_Contact__c": "principal-id",
                    "Cert_Accounting_Contact__c": None,
                    "Cert_Marketing_Contact__c": "quality-id",
                    "Cert_Safety_Contact__c": "",
                },
            )
        ]
    )

    counts = SANYCAuditContactFillService(client, now=NOW).run(apply=True)

    assert counts.updated == 1
    assert counts.unavailable == 2
    assert client.updated == [
        ("Cert_Audit__c", "audit-1", {"QC_Contact__c": "quality-id"})
    ]


def test_apply_continues_after_a_patch_failure():
    client = FakeClient([audit("fails"), audit("works")])
    client.fail_ids.add("fails")

    counts = SANYCAuditContactFillService(client, now=NOW).run(apply=True)

    assert counts.qualifying == 2
    assert counts.failed == 4
    assert counts.updated == 4
    assert client.updated == [
        (
            "Cert_Audit__c",
            "works",
            {
                "Principal_Contact__c": "principal-id",
                "AP_Contact__c": "accounting-id",
                "QC_Contact__c": "quality-id",
                "New_York_Contact__c": "new-york-id",
            },
        )
    ]
