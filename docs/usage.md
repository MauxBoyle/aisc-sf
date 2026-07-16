# Usage

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Configuration

Copy the environment template and fill in the Salesforce values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---:|---|
| `SF_CLIENT_ID` | Yes | Salesforce Connected App client ID |
| `SF_CLIENT_SECRET` | Yes | Salesforce Connected App secret |
| `CERTIFICATION_QUEUE_ID` | For profile updates | Owner ID for Profile Update Cases |
| `PRIMARY_RESPONDER_ID` | For profile updates | Primary Responder ID for Profile Update Cases |
| `SF_LOGIN_URL` | No | Salesforce org URL or complete OAuth token URL |

The CLI loads `.env` without replacing environment variables that are already
set.

## Snapshot command

```bash
uv run aisc_salesforce snapshot
uv run aisc_salesforce snapshot --output-dir /secure/snapshot-location
```

## Profile Update command

Run one daily processing pass:

```bash
uv run aisc_salesforce profile-updates
```

The command evaluates eligible audits and submissions whose `Status__c` is
`New`. It creates or reuses Account-scoped Profile Update Cases, matches a
submission Contact by email when the match is unambiguous, and posts missing
Chatter messages.

Example output:

```text
Profile updates complete:
created: 2
reused: 1
skipped: 4
failed: 0
```

Exit code `0` means the run completed without failures. Exit code `1` means
configuration, Salesforce communication, or one or more records failed.

## Scheduling and retries

Use cron, Windows Task Scheduler, or another external scheduler to call the
command daily. For example:

```cron
0 2 * * * cd /path/to/aisc-sf && uv run aisc_salesforce profile-updates
```

Retries are safe: the service uses the Account, Case Subject, Profile Update
name, and exact Chatter text to recognize completed work. If a run stops after
one Chatter post, the next run posts only the missing message.

The project does not install or manage a scheduler itself.
