---
name: wf-review
description: >-
  Review a pull request for critical issues and post findings as a PR comment.
  Focuses on bugs, performance, security, and correctness. Use when reviewing
  a PR, checking code quality, or auditing changes before merge.
---

# Review — Review a Pull Request

Analyze a pull request for critical issues and post the review as a comment.

> Based on [Jose Casanova's PR review prompt](https://www.josecasanova.com/prompts/git-concise-pr-review-prompt).

## The PR

Use the PR URL or number the user specified.

## Steps

### 1. Fetch and Analyze Changes

Run `gh pr diff` against the PR the user specified to get the diff. Focus **only** on critical issues:

- **Bugs** — logic errors, edge cases, race conditions
- **Performance** — inefficient queries, unnecessary loops, memory issues
- **Security** — injection, exposure, auth gaps
- **Correctness** — does the code do what it claims?

### 2. Write the Review

- If **critical issues found**: list them as short bullet points
- If **no critical issues**: provide a simple approval

Skip style suggestions and minor nitpicks unless they impact performance, security, or correctness.

### 3. Post as PR Comment

Use `gh api` to add the review as a comment on the PR. **Never merge.**

Sign off with:
- ✅ (approved) or ⚠️ (issues found)
- The model you are running as
