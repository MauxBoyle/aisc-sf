"""Tests for the operator-run Yoshki website scan."""

import csv

from aisc_salesforce.yoshki_users import (
    CSV_COLUMNS,
    scan_yoshki_accounts,
    write_yoshki_csv,
)


class FakeSalesforce:
    def __init__(self):
        self.calls = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.calls.append((object_name, fields, where, order_by))
        if object_name == "Account":
            return [
                {
                    "Id": "a1", "Name": "Alpha", "Cert_Certification_Status__c": "Certified",
                    "Website": "alpha.example", "Cert_Certification_Contact__c": "c1",
                    "Cert_Principal_Contact__c": "c2",
                },
                {
                    "Id": "a2", "Name": "Beta", "Cert_Certification_Status__c": "Dropped",
                    "Website": "https://beta.example", "Cert_Certification_Contact__c": None,
                    "Cert_Principal_Contact__c": "c3",
                },
                {
                    "Id": "a3", "Name": "Gamma", "Cert_Certification_Status__c": "Initials",
                    "Website": "gamma.example", "Cert_Certification_Contact__c": "c4",
                    "Cert_Principal_Contact__c": None,
                },
                {"Id": "a4", "Name": "No site", "Website": None},
            ]
        return [
            {"Id": "c1", "Email": "cert@example.com"},
            {"Id": "c2", "Email": "principal@example.com"},
            {"Id": "c3", "Email": ""},
        ]


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeHttp:
    def get(self, url, **kwargs):
        if "gamma" in url:
            raise OSError("connection refused")
        return Response("<script>55879r</script><script>55170r</script>")


def test_scan_statuses_matches_each_account_once_and_resolves_contact_emails():
    salesforce = FakeSalesforce()

    result = scan_yoshki_accounts(salesforce, http=FakeHttp())

    assert len(result.matches) == 2
    assert result.matches[0]["Certification Contact Email"] == "cert@example.com"
    assert result.matches[0]["Principal Contact Email"] == "principal@example.com"
    assert result.matches[1]["Certification Contact Email"] == ""
    assert result.matches[1]["Principal Contact Email"] == ""
    assert "Certified" in salesforce.calls[0][2]
    assert "Dropped" in salesforce.calls[0][2]
    assert "Initials" in salesforce.calls[0][2]
    assert "Gamma (https://gamma.example)" in result.failures[0]
    assert "No site: website is blank" in result.failures[1]


def test_csv_has_stable_column_order(tmp_path):
    path = write_yoshki_csv(({"Account Name": "Alpha"},), tmp_path / "yoshki_users.csv")

    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        assert next(reader) == list(CSV_COLUMNS)
        assert next(reader) == ["Alpha", "", "", "", ""]
