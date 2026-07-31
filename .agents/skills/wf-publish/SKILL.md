---
name: wf-publish
description: >-
  Merge a pull request and clean up branches and issues. Use when publishing,
  merging a PR, finalizing work, or cleaning up after a merge.
---

# Publish — Merge PR and Clean Up

Merge an approved pull request and clean up Git.

## The PR

Use the PR URL or number the user specified.

## Steps

### 1. Check PR Status

Run `gh pr view --json state,mergeable,mergeStateStatus` against the PR the user specified to check the PR status. If there are merge conflicts with the base branch, **stop immediately** and tell the user. Do not attempt to resolve conflicts automatically.

### 2. Merge

Perform the merge using a **merge commit** (not squash or rebase), substituting the PR the user specified:

```bash
gh pr merge <pr> --merge
```

### 3. Update Local `main`

After a successful merge, make sure the local checkout uses the newly merged
code. First switch to `main`, then fetch and fast-forward it:

```bash
git switch main
git fetch origin main
git merge --ff-only origin/main
```

Verify the result:

```bash
git status --short
git log --oneline -3
```

If the working tree has uncommitted changes or local `main` has diverged,
stop and ask the user before changing or discarding anything. Do not use a
forced reset to update `main`.

### 4. Clean Up

After a successful merge:
- **Close related issues** referenced in the PR
- **Delete the working branch** (both remote and local)
- **Remove any obsolete branches** that are no longer needed

Do **not** review the PR — that has already been done.
