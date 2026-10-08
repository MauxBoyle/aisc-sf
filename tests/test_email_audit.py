import csv

from aisc_salesforce.email_audit import (
    CONTACT_EMAIL_FIELDS,
    REPORT_COLUMNS,
    ContactEmailAuditService,
    assess_email,
    load_email_audit_config,
    load_consumer_domains,
    load_name_variants,
    write_contact_email_audit,
)


def test_assess_email_recognizes_name_forms_variants_and_typos():
    exact = assess_email("José", "Quinn", "O'Neil", "Jr.", "jose.oneil@example.org")
    assert exact.local_part_pattern == "first_last"
    assert exact.name_match_status == "exact"

    normalized = assess_email("Jane", "", "Smith", "", "J-Smith@example.org")
    assert normalized.local_part_pattern == "f_last"
    assert normalized.name_match_status == "normalized_exact"

    variant = assess_email("Alexander", "", "Smith", "", "alex.smith@example.org")
    assert variant.name_match_status == "name_variant"
    assert "name_variant" in variant.review_reasons

    typo = assess_email("Jane", "", "Smith", "", "jane.smit@example.org")
    assert typo.name_match_status == "near_match"
    assert "possible_name_typo" in typo.review_reasons


def test_assess_email_handles_special_missing_and_malformed_addresses():
    assert assess_email("A", "", "B", "", "").local_part_pattern == "missing"
    assert "malformed_email" in assess_email("A", "", "B", "", "wrong").review_reasons
    assert assess_email("A", "", "B", "", "ap@example.org").account_kind == "role"
    assert (
        assess_email("A", "", "B", "", "verify-77@example.org").account_kind == "system"
    )
    assert (
        assess_email("A", "", "B", "", "10042@example.org").account_kind == "numeric_id"
    )
    assert assess_email("A", "", "B", "", "a@gmail.com").domain_kind == "consumer"


def test_configuration_loaders_accept_the_versioned_formats(tmp_path):
    variants = tmp_path / "variants.csv"
    variants.write_text("canonical_name,variant\nAlexander,Alex\n", encoding="utf-8")
    domains = tmp_path / "domains.txt"
    domains.write_text("gmail.com\n# comment\n\n", encoding="utf-8")

    assert load_name_variants(variants)["alex"] == frozenset({"alex", "alexander"})
    assert load_consumer_domains(domains) == frozenset({"gmail.com"})


def test_default_configuration_loads_from_package_data():
    config = load_email_audit_config()

    assert "alex" in config.name_variants
    assert "gmail.com" in config.consumer_domains


def test_audit_fetches_all_contacts_and_writes_a_stable_csv(tmp_path):
    class Client:
        def __init__(self):
            self.calls = []

        def query_records(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return [
                {
                    "Id": "b",
                    "FirstName": "Jane",
                    "MiddleName": None,
                    "LastName": "Smith",
                    "Suffix": None,
                    "Email": "JANE.SMITH@example.org",
                },
                {
                    "Id": "a",
                    "FirstName": "Other",
                    "MiddleName": None,
                    "LastName": "Person",
                    "Suffix": None,
                    "Email": "jane.smith@example.org",
                },
                {
                    "Id": "c",
                    "FirstName": "Blank",
                    "MiddleName": None,
                    "LastName": "Email",
                    "Suffix": None,
                    "Email": None,
                },
            ]

    client = Client()
    rows = ContactEmailAuditService(client).build()
    assert client.calls == [
        (("Contact", list(CONTACT_EMAIL_FIELDS)), {"order_by": "Id ASC"})
    ]
    assert [row["contact_id"] for row in rows] == ["a", "b", "c"]
    assert rows[0]["duplicate_email_count"] == "2"
    assert rows[0]["duplicate_email_contact_ids"] == "b|a"
    assert "duplicate_email" in rows[0]["review_reason"]

    output = write_contact_email_audit(rows, tmp_path / "report.csv")
    with output.open(encoding="utf-8", newline="") as report:
        reader = csv.DictReader(report)
        assert tuple(reader.fieldnames or []) == REPORT_COLUMNS
        assert [row["contact_id"] for row in reader] == ["a", "b", "c"]
