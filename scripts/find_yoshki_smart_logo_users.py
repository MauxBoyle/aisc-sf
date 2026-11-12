"""Run the read-only Yoshki website scan and write ``yoshki_users.csv``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aisc_salesforce.app import _load_dotenv
from aisc_salesforce.salesforce import (
    SalesforceClient,
    SalesforceError,
    get_credentials,
    get_oauth_url,
    request_access_token,
)
from aisc_salesforce.yoshki_users import scan_yoshki_accounts, write_yoshki_csv


def main() -> int:
    """Connect read-only to Salesforce, scan sites, and save the CSV."""
    _load_dotenv(Path(".env"))
    try:
        environment = dict(os.environ)
        print("Connecting to Salesforce...", file=sys.stderr, flush=True)
        auth = request_access_token(
            get_credentials(environment), oauth_url=get_oauth_url(environment)
        )
        print("Salesforce connected.", file=sys.stderr, flush=True)
        result = scan_yoshki_accounts(
            SalesforceClient(auth),
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
        print("Writing yoshki_users.csv...", file=sys.stderr, flush=True)
        path = write_yoshki_csv(result.matches, Path("yoshki_users.csv"))
    except (SalesforceError, OSError) as error:
        print(f"Yoshki scan failed: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {len(result.matches)} matching Accounts to {path}.")
    for failure in result.failures:
        print(f"Site scan failed: {failure}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
