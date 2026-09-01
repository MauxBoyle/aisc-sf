"""Focused tests for deterministic participant User field policies."""

from datetime import date

from aisc_salesforce.user_field_policies import (
    FIXED_EMAIL_ENCODING,
    FIXED_LANGUAGE,
    FIXED_LOCALE,
    alias,
    community_nickname,
    fixed_localization_fields,
    normalized_name_component,
    time_zone,
)


def test_name_normalization_and_clock_based_identifiers():
    def clock() -> date:
        return date(2030, 1, 1)

    assert normalized_name_component("  Élodie!  ") == "élodie"
    assert alias("Élodie", "O'Neil-Smith", clock) == "oneilé30"
    assert alias("?", "!", clock) == "30"
    assert community_nickname("Ada", "Lovelace", clock, required=False) is None
    assert community_nickname("Ada", "Lovelace", clock, required=True) == "lovelacea30"


def test_time_zone_mapping_and_chicago_fallback():
    assert (
        time_zone({"MailingCountry": "United States", "MailingState": "CA"})
        == "America/Los_Angeles"
    )
    assert (
        time_zone({"MailingCountry": "Canada", "MailingState": "Ontario"})
        == "America/Toronto"
    )
    assert (
        time_zone({"MailingCountry": "Canada", "MailingState": "Unknown"})
        == "America/Chicago"
    )
    assert time_zone({}) == "America/Chicago"


def test_fixed_localization_values():
    assert fixed_localization_fields() == {
        "LocaleSidKey": FIXED_LOCALE,
        "LanguageLocaleKey": FIXED_LANGUAGE,
        "EmailEncodingKey": FIXED_EMAIL_ENCODING,
    }
