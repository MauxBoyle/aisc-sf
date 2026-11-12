"""Find qualifying Accounts whose websites contain a retired Yoshki snippet."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests

from .salesforce import SalesforceClient

STATUSES = ("Certified", "Dropped", "Initials")
SNIPPETS = ("55879r", "55170r")
CSV_COLUMNS = (
    "Account Name",
    "Certification Status",
    "Website",
    "Certification Contact Email",
    "Principal Contact Email",
)


@dataclass(frozen=True)
class YoshkiScanResult:
    """Accounts found using a retired snippet and site failures to review."""

    matches: tuple[dict[str, str], ...]
    failures: tuple[str, ...]


def scan_yoshki_accounts(
    client: SalesforceClient,
    *,
    http: Any = requests,
    timeout: int = 20,
    progress: Callable[[str], None] | None = None,
) -> YoshkiScanResult:
    """Read qualifying Accounts and Contacts, then scan each Account website."""
    report = progress or (lambda _message: None)
    report("Querying Salesforce for qualifying Accounts...")
    status_values = ", ".join(f"'{status}'" for status in STATUSES)
    accounts = client.query_records(
        "Account",
        [
            "Id",
            "Name",
            "Cert_Certification_Status__c",
            "Website",
            "Cert_Certification_Contact__c",
            "Cert_Principal_Contact__c",
        ],
        where=f"Cert_Certification_Status__c IN ({status_values})",
        order_by="Name",
    )
    report(f"Found {len(accounts)} Accounts. Collecting contact email addresses...")
    contact_ids = sorted(
        {
            str(account[field])
            for account in accounts
            for field in ("Cert_Certification_Contact__c", "Cert_Principal_Contact__c")
            if account.get(field)
        }
    )
    contacts_by_id: dict[str, str] = {}
    for offset in range(0, len(contact_ids), 200):
        batch = contact_ids[offset : offset + 200]
        report(f"Loading Contact emails ({min(offset + len(batch), len(contact_ids))}/{len(contact_ids)})...")
        quoted_ids = ", ".join(f"'{contact_id}'" for contact_id in batch)
        contacts = client.query_records("Contact", ["Id", "Email"], where=f"Id IN ({quoted_ids})")
        contacts_by_id.update(
            {str(contact["Id"]): str(contact.get("Email") or "") for contact in contacts}
        )

    matches: list[dict[str, str]] = []
    failures: list[str] = []
    for index, account in enumerate(accounts, start=1):
        name = str(account.get("Name") or "(unnamed Account)")
        website = str(account.get("Website") or "").strip()
        report(f"Scanning Account {index}/{len(accounts)}: {name}")
        if not website:
            failures.append(f"{name}: website is blank")
            report(f"  Skipped: website is blank")
            continue
        url = website if urlparse(website).scheme else f"https://{website}"
        parsed_url = urlparse(url)
        if not parsed_url.hostname or any(char.isspace() for char in unquote(parsed_url.netloc)):
            failures.append(f"{name} ({website}): website is not a valid URL")
            report("  Skipped: website is not a valid URL")
            continue
        try:
            response = http.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
        except (requests.RequestException, OSError) as error:
            failures.append(f"{name} ({url}): {error}")
            report(f"  Failed: {error}")
            continue
        page = str(getattr(response, "text", "")).casefold()
        if not any(snippet in page for snippet in SNIPPETS):
            report("  No Yoshki snippet found")
            continue
        matches.append(
            {
                "Account Name": name,
                "Certification Status": str(account.get("Cert_Certification_Status__c") or ""),
                "Website": website,
                "Certification Contact Email": contacts_by_id.get(str(account.get("Cert_Certification_Contact__c") or ""), ""),
                "Principal Contact Email": contacts_by_id.get(str(account.get("Cert_Principal_Contact__c") or ""), ""),
            }
        )
        report("  Match found")
    report(f"Website scan complete: {len(matches)} matches, {len(failures)} site issues.")
    return YoshkiScanResult(tuple(matches), tuple(failures))


def write_yoshki_csv(matches: tuple[dict[str, str], ...], path: Path) -> Path:
    """Write matched Accounts to a local CSV with stable columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(matches)
    return path
