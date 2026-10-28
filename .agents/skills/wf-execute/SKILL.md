---
name: wf-execute
description: >-
  Implement an approved plan for a GitHub issue on a working branch. Use when
  executing a prepared implementation plan or completing planned issue work.
---

# Execute — Implement a Plan

Implement the approved plan for the issue the user specified.

## Verify the Branch

Before creating, modifying, moving, or deleting any file, run:

```bash
test "$(git branch --show-current)" != "main"
```

Do not edit files unless this command succeeds. If it fails because the issue
implementation started on `main`, find the working-branch name in the approved
plan and create that exact branch before doing anything that writes a file. Run
the verification command again after switching branches.

If the plan does not name a branch, the planned branch cannot be created, or
uncommitted changes make switching unsafe, stop and ask the user how to
proceed. Do not write any files while still on `main`.

## Execute the Plan

Follow the approved plan in order. Preserve unrelated user changes, run the
planned tests and checks, and report what changed and what verification passed.

Track every plan step and report its final status as exactly one of:

- `completed` — the step was carried out successfully
- `skipped` — the step was not needed or could not be attempted; explain why
- `failed` — the step was attempted but did not succeed; include the failure

Do not omit plan steps from the final report, even when they were skipped or
failed.

## Report Verification Evidence

For every test or verification check, report all of the following:

- the exact command that was run
- the numeric exit code
- the result, including whether it passed or failed and the relevant output

Do not summarize a check as passing without this evidence. If a planned check
was not run, mark its plan step as `skipped` and explain why.

## Final Handoff

Include an explicit list of every plan requirement that was not satisfied.
For each one, state why it remains unsatisfied and what is needed to complete
it. If all plan requirements were satisfied, state `Unsatisfied requirements:
none` rather than omitting the section.
