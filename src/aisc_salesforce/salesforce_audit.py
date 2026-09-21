"""Metadata-only, local audit logging for Salesforce REST operations."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

AUDIT_DIRECTORY = Path(".audit/salesforce")
RETENTION_DAYS = 30
PROCESS_RUN_ID = str(uuid4())
_SAFE_CALLER_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def caller_from_argv(argv: list[str] | None = None) -> str:
    """Return only a safe program-and-command label for the current process."""
    values = sys.argv if argv is None else argv
    if not values:
        return "python"

    program = Path(values[0]).name or "python"
    parts = [program if _SAFE_CALLER_PART.fullmatch(program) else "python"]
    if len(values) > 1 and _SAFE_CALLER_PART.fullmatch(values[1]):
        parts.append(values[1])
    return " ".join(parts)


class SalesforceAuditWriter:
    """Append safe Salesforce operation metadata to daily JSON Lines files."""

    def __init__(
        self,
        directory: Path = AUDIT_DIRECTORY,
        *,
        run_id: str = PROCESS_RUN_ID,
        caller: str | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.directory = directory
        self.run_id = run_id
        self.caller = caller if caller is not None else caller_from_argv()
        self._now = now or (lambda: datetime.now(UTC))

    def write(self, event: dict[str, Any]) -> None:
        """Write an event, swallowing local filesystem failures.

        Salesforce work must not fail merely because its diagnostic trail cannot
        be stored, for example when the current directory is read-only.
        """
        try:
            timestamp = self._now().astimezone(UTC)
            record = {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "run_id": self.run_id,
                "caller": self.caller,
                **event,
            }
            self.directory.mkdir(parents=True, exist_ok=True)
            path = (
                self.directory
                / f"salesforce-audit-{timestamp.date().isoformat()}.jsonl"
            )
            with path.open("a", encoding="utf-8") as audit_file:
                audit_file.write(json.dumps(record, sort_keys=True) + "\n")
                audit_file.flush()
                os.fsync(audit_file.fileno())
            self._prune()
        except (OSError, TypeError, ValueError):
            return

    def _prune(self) -> None:
        """Keep the newest daily audit files and remove older local files."""
        paths = sorted(self.directory.glob("salesforce-audit-*.jsonl"), reverse=True)
        for path in paths[RETENTION_DAYS:]:
            path.unlink()
