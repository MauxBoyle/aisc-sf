import pytest

from aisc_salesforce.contact_normalization import (
    CONTACT_CASE_EXCEPTIONS,
    normalize_email,
    normalize_phone,
    normalize_proper_case,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  jane DOE  ", "Jane Doe"),
        ("anne-marie smith/jones", "Anne-Marie Smith/Jones"),
        ("o'brien & sons", "O'Brien & Sons"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_proper_case_handles_general_text_punctuation_and_blanks(value, expected):
    assert normalize_proper_case(value) == expected


@pytest.mark.parametrize("canonical", CONTACT_CASE_EXCEPTIONS)
def test_proper_case_applies_every_whole_token_exception(canonical):
    value = f"chief {canonical.swapcase()} officer"

    assert normalize_proper_case(value) == f"Chief {canonical} Officer"


def test_proper_case_exceptions_do_not_change_part_of_a_larger_word():
    assert normalize_proper_case("ceos and apis") == "Ceos And Apis"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" ALEX.SMITH@Example.COM ", "alex.smith@example.com"),
        (" NOT-AN-EMAIL ", "not-an-email"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_email_is_trimmed_and_lowercased_even_when_invalid(value, expected):
    assert normalize_email(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3125550100", "312.555.0100"),
        ("(312) 555-0100", "312.555.0100"),
        ("312.555.0100", "312.555.0100"),
        ("1-312-555-0100", "312.555.0100"),
        ("+1 (312) 555-0100", "312.555.0100"),
        ("312-555-0100 x123", "312.555.0100 x123"),
        ("312-555-0100 ext 4567", "312.555.0100 x4567"),
        ("312-555-0100 ext. 890", "312.555.0100 x890"),
        ("312-555-0100 extension 00123", "312.555.0100 x00123"),
        ("312-555-0100 #9876543210", "312.555.0100 x9876543210"),
    ],
)
def test_recognizable_north_american_phones_are_canonical(value, expected):
    assert normalize_phone(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "+44 20 7946 0958",
        "555-0100",
        "call 312-555-0100",
        "+52 55 1234 5678",
        "312-555-0100 ext.",
    ],
)
def test_unrecognized_phones_are_only_trimmed(value):
    assert normalize_phone(f"  {value}  ") == value
