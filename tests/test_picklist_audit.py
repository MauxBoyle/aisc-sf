from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

import pytest

from aisc_salesforce.picklist_audit import (
    PicklistAuditError,
    PicklistEnumAuditService,
    audit_date_field,
    two_year_cutoff,
)
from aisc_salesforce.salesforce import SalesforceError


class KnownStatus(StrEnum):
    PENDING = "Pending"


class KnownTag(StrEnum):
    FIRST = "First"
    SECOND = "Second"


class Client:
    def __init__(self):
        self.describe_calls = []
        self.query_calls = []

    def describe_object(self, object_name):
        self.describe_calls.append(object_name)
        return {
            "Status": "picklist",
            "Tags__c": "multipicklist",
            "Uncataloged__c": "picklist",
            "Subject": "string",
        }

    def query_records(self, object_name, fields, *, where, order_by):
        self.query_calls.append((object_name, fields, where, order_by))
        return [
            {
                "Status": "Pending",
                "Tags__c": "First;Second",
                "Uncataloged__c": None,
            },
            {
                "Status": "Unexpected",
                "Tags__c": "Second;Missing Tag",
                "Uncataloged__c": "Zebra",
            },
            {
                "Status": "Unexpected",
                "Tags__c": "",
                "Uncataloged__c": "Alpha",
            },
            {"Status": "", "Tags__c": None, "Uncataloged__c": "Alpha"},
        ]


def test_audit_filters_recent_records_and_groups_only_missing_values():
    client = Client()
    cutoff = datetime(2024, 7, 24, 15, 30, tzinfo=UTC)
    catalog = {
        ("Case", "Status"): KnownStatus,
        ("Case", "Tags__c"): KnownTag,
    }

    result = PicklistEnumAuditService(
        client, cutoff=cutoff, enum_catalog=catalog
    ).audit(
        {
            "Case": (
                "Subject",
                "Status",
                "Tags__c",
                "Uncataloged__c",
                "Status",
            )
        }
    )

    assert client.describe_calls == ["Case"]
    assert client.query_calls == [
        (
            "Case",
            ["Status", "Tags__c", "Uncataloged__c"],
            "LastModifiedDate >= 2024-07-24T15:30:00Z",
            "Id ASC",
        )
    ]
    assert [
        (finding.field_name, finding.values, finding.has_enum)
        for finding in result.findings
    ] == [
        ("Status", ("Unexpected",), True),
        ("Tags__c", ("Missing Tag",), True),
        ("Uncataloged__c", ("Alpha", "Zebra"), False),
    ]


def test_audit_describes_objects_even_when_no_queried_field_is_a_picklist():
    class NoPicklists:
        def __init__(self):
            self.queries = []

        def describe_object(self, object_name):
            return {"Name": "string"}

        def query_records(self, *args, **kwargs):
            self.queries.append((args, kwargs))

    client = NoPicklists()
    result = PicklistEnumAuditService(
        client,
        cutoff=datetime(2024, 1, 1, tzinfo=UTC),
    ).audit({"Account": ("Name",)})

    assert result.findings == ()
    assert client.queries == []


@pytest.mark.parametrize("object_name", ["AccountHistory", "Widget__History"])
def test_history_objects_filter_on_created_date(object_name):
    class HistoryClient:
        def __init__(self):
            self.query = None

        def describe_object(self, described_object):
            assert described_object == object_name
            return {"Field": "picklist", "CreatedDate": "datetime"}

        def query_records(
            self, queried_object, fields, *, where, order_by
        ):
            self.query = (queried_object, fields, where, order_by)
            return [{"Field": "UnknownHistoryField"}]

    client = HistoryClient()
    result = PicklistEnumAuditService(
        client,
        cutoff=datetime(2024, 7, 24, 15, 30, tzinfo=UTC),
    ).audit({object_name: ("Field",)})

    assert client.query == (
        object_name,
        ["Field"],
        "CreatedDate >= 2024-07-24T15:30:00Z",
        "Id ASC",
    )
    assert result.findings[0].values == ("UnknownHistoryField",)


def test_non_history_objects_filter_on_last_modified_date():
    assert audit_date_field("Account") == "LastModifiedDate"
    assert audit_date_field("AccountHistory") == "CreatedDate"
    assert audit_date_field("Widget__History") == "CreatedDate"


def test_two_year_cutoff_preserves_time_and_handles_leap_day():
    assert two_year_cutoff(datetime(2026, 7, 24, 12, 5, tzinfo=UTC)) == datetime(
        2024, 7, 24, 12, 5, tzinfo=UTC
    )
    assert two_year_cutoff(datetime(2024, 2, 29, 8, 0, tzinfo=UTC)) == datetime(
        2022, 2, 28, 8, 0, tzinfo=UTC
    )
    central = timezone(-timedelta(hours=5))
    assert two_year_cutoff(datetime(2026, 7, 24, 7, 0, tzinfo=central)) == datetime(
        2024, 7, 24, 12, 0, tzinfo=UTC
    )


def test_audit_dates_must_be_timezone_aware():
    with pytest.raises(PicklistAuditError, match="UTC offset"):
        two_year_cutoff(datetime(2026, 7, 24))


def test_non_string_picklist_data_is_rejected_clearly():
    class BadClient:
        def describe_object(self, object_name):
            return {"Status": "picklist"}

        def query_records(self, *args, **kwargs):
            return [{"Status": 3}]

    service = PicklistEnumAuditService(
        BadClient(),
        cutoff=datetime(2024, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(SalesforceError, match=r"Case\.Status"):
        service.audit({"Case": ("Status",)})
