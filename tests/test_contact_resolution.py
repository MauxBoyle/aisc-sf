from aisc_salesforce.contact_resolution import (
    ContactResolutionClassification,
    comparison_name,
    family_account_ids,
    is_single_edit_or_transposition,
    name_local_part_patterns,
    normalize_email,
    resolve_contact,
)


def contact(contact_id, email, *, account_id="account-1", first="Alex", last="Smith"):
    return {
        "Id": contact_id,
        "AccountId": account_id,
        "FirstName": first,
        "LastName": last,
        "Email": email,
    }


def test_email_normalization_is_trimmed_lowercase_and_dot_insensitive():
    assert normalize_email("  Alex.Smith@Example.COM ") == (
        "alex.smith@example.com",
        "alexsmith@example.com",
        [],
    )


def test_invalid_email_has_warning_and_no_comparison_key():
    normalized, key, warnings = normalize_email("not an email")

    assert normalized == "not an email"
    assert key == ""
    assert "Invalid email" in warnings[0]


def test_family_is_target_parent_and_siblings_but_root_is_only_itself():
    target = {"Id": "child-1", "ParentId": "parent-1"}
    accounts = [
        target,
        {"Id": "parent-1", "ParentId": ""},
        {"Id": "child-2", "ParentId": "parent-1"},
        {"Id": "unrelated", "ParentId": "other"},
    ]

    assert family_account_ids(target, accounts) == {
        "child-1",
        "parent-1",
        "child-2",
    }
    assert family_account_ids({"Id": "root", "ParentId": ""}, accounts) == {"root"}


def test_family_exact_match_wins_but_mixed_external_match_is_ambiguous():
    family = contact("family", "alex@example.com")
    external = contact("external", "alex@example.com", account_id="external-account")

    exact = resolve_contact(" ALEX@example.com ", [family], {"account-1"})
    mixed = resolve_contact("alex@example.com", [family, external], {"account-1"})

    assert exact.classification is ContactResolutionClassification.USE_EXISTING
    assert exact.selected_contact == family
    assert mixed.classification is ContactResolutionClassification.AMBIGUOUS
    assert {item["Id"] for item in mixed.candidates} == {"family", "external"}


def test_dot_only_difference_is_an_exact_comparison_key_match():
    existing = contact("contact-1", "alexsmith@example.com")

    result = resolve_contact("alex.smith@example.com", [existing], {"account-1"})

    assert result.classification is ContactResolutionClassification.USE_EXISTING
    assert result.confidence == "exact_comparison_key"


def test_name_evidence_with_different_domain_requires_operator():
    existing = contact("contact-1", "alex.smith@old.example")

    result = resolve_contact("alexsmith@new.example", [existing], {"account-1"})

    assert result.classification is ContactResolutionClassification.AMBIGUOUS
    assert result.candidates == [existing]
    assert "differing" in result.reason


def test_generic_mailbox_never_creates_automatically():
    existing = contact("shared", "info@example.com")
    result = resolve_contact("info@example.com", [existing], {"account-1"})

    assert result.classification is ContactResolutionClassification.AMBIGUOUS
    assert result.candidates == [existing]
    assert "generic" in result.warnings[0].casefold()


def test_bracket_suffix_is_only_removed_for_name_comparison():
    assert comparison_name("Smith [Former]") == "smith"
    assert "alexsmith" in name_local_part_patterns("Alex", "Smith [Former]")


def test_only_one_edit_or_adjacent_transposition_is_a_likely_typo():
    assert is_single_edit_or_transposition("alexsmith", "alexsmit")
    assert is_single_edit_or_transposition("alexsmith", "alexsimth")
    assert not is_single_edit_or_transposition("alexsmith", "alecjones")

    existing = contact("contact-1", "alex.smith@example.com")
    result = resolve_contact("alexsimth@example.com", [existing], {"account-1"})
    assert result.classification is ContactResolutionClassification.LIKELY_TYPO
    assert result.selected_contact == existing
