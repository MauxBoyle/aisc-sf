import csv

import pytest

from aisc_salesforce.dictionary import DictionaryError, load_export_plan


def write_dictionary(path, rows, headers=None):
    headers = headers or [
        "Salesforce_Table",
        "Sensible_Python_Key",
        "Actual_Salesforce_API_Name",
        "ScriptsUsing",
    ]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_load_export_plan_groups_selected_fields_in_order(tmp_path):
    path = tmp_path / "dictionary.csv"
    write_dictionary(
        path,
        [
            {
                "Salesforce_Table": "Account",
                "Sensible_Python_Key": "name",
                "Actual_Salesforce_API_Name": "Name",
                "ScriptsUsing": " true ",
            },
            {
                "Salesforce_Table": "Contact",
                "Sensible_Python_Key": "email",
                "Actual_Salesforce_API_Name": "Email",
                "ScriptsUsing": "FALSE",
            },
            {
                "Salesforce_Table": "Account",
                "Sensible_Python_Key": "city",
                "Actual_Salesforce_API_Name": "BillingCity",
                "ScriptsUsing": "TRUE",
            },
        ],
    )

    plan = load_export_plan(path)

    assert list(plan) == ["Account"]
    assert [field.python_key for field in plan["Account"]] == ["name", "city"]
    assert [field.api_name for field in plan["Account"]] == ["Name", "BillingCity"]


@pytest.mark.parametrize(
    "rows, message",
    [
        ([], "no rows"),
        (
            [
                {
                    "Salesforce_Table": "Account",
                    "Sensible_Python_Key": "",
                    "Actual_Salesforce_API_Name": "Name",
                    "ScriptsUsing": "TRUE",
                }
            ],
            "incomplete",
        ),
        (
            [
                {
                    "Salesforce_Table": "Account",
                    "Sensible_Python_Key": "name",
                    "Actual_Salesforce_API_Name": "Name",
                    "ScriptsUsing": "TRUE",
                },
                {
                    "Salesforce_Table": "Account",
                    "Sensible_Python_Key": "name",
                    "Actual_Salesforce_API_Name": "Other",
                    "ScriptsUsing": "TRUE",
                },
            ],
            "duplicate",
        ),
    ],
)
def test_load_export_plan_rejects_invalid_selected_rows(tmp_path, rows, message):
    path = tmp_path / "dictionary.csv"
    write_dictionary(path, rows)
    with pytest.raises(DictionaryError, match=message):
        load_export_plan(path)


def test_load_export_plan_rejects_missing_columns(tmp_path):
    path = tmp_path / "dictionary.csv"
    write_dictionary(path, [], headers=["Salesforce_Table"])
    with pytest.raises(DictionaryError, match="missing required columns"):
        load_export_plan(path)
