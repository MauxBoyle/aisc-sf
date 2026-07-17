---
name: wf-pr
description: >-
  Create a pull request from the current branch. Summarizes all commits and
  references connected issues. Use when creating a PR, staging changes, or
  opening a pull request.
---

# PR — Create a Pull Request

Create a pull request from the current branch.

## Orient Yourself

Run `git branch --show-current`, `git log --oneline main..HEAD`, and `git diff main..HEAD --stat` to see the current branch, the commits on it, and the changed files.

## Steps

### 1. Review All Commits

Look at **all** commits on this branch (not just the latest). Understand the full scope of changes.

### 2. Create the PR

Use `gh pr create` to create a pull request targeting the target branch the user specified (default: `main`).

The PR should include:
- A clear, concise title
- A summary of what changed and why
- References to connected GitHub issues (e.g., `Closes #123`, `Fixes #45`)

Keep it short and focused — this is mechanical work, not a review.
