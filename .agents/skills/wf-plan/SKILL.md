---
name: wf-plan
description: >-
  Plan implementation for a GitHub issue. Reads the issue, researches official
  docs, and produces a TDD-based plan. Use when planning work, preparing an
  implementation, or creating a development plan for an issue.
---

# Plan — Plan Implementation from a GitHub Issue

Read a GitHub issue and produce a development plan.

## The Issue

Use the GitHub issue URL or number the user specified.

## Steps

### 1. Read the Issue

Run `gh issue view` against the issue the user specified to fetch the issue details. Then identify:
- The goal and expected outcome
- Technical requirements
- Potential challenges

### 2. Research

If needed, research using **official documentation only** (not blog posts or unofficial GitHub repos).

### 3. Raise Doubts

Point out any doubts about the implementation. **Only continue if you are confident about the approach.** If unsure, explain what's unclear and ask the user.

### Review File Organization

  Before planning file changes, inventory the affected files and classify each as
  one of: source code, test, documentation, version-controlled configuration,
  secret, local operational data, generated output, or tool cache.

  Use this layout as the default unless the project’s needs justify another choice:

  - Keep the repository root limited to project identity, dependency, and tooling files.
  - Put application code in `src/`, tests in `tests/`, documentation in `docs/`,
    and reusable scripts in `scripts/`.
  - Put small, version-controlled business settings in `config/`.
  - Put operational data in `data/`; ignore it in Git unless it is intentionally
    versioned, such as an audit record.
  - Put generated reports and exports in `reports/` or `data/`, and ignore them
    unless there is a documented reason to track them.
  - Keep secrets out of Git; use `.env` locally and `.env.example` as the safe template.

  For every moved file, include updates to its default path, environment-variable
  configuration, tests, documentation, and `.gitignore` rules. Preserve existing
  user changes and do not move a tracked data file without confirming whether its
  history is intentionally retained.

### 4. Create the Plan

The plan must follow this structure:

1. **Create a working branch** with a descriptive slug
2. **Write tests first** (TDD)
3. **Write code** to make the tests pass
4. **Run tests** to verify
5. **Repeat** until all requirements are met
6. **Before committing**, ensure that:
   - Documentation is updated
   - README is updated

### 5. Present Options

Ask the user:

- **"Clear context and execute"** — start a fresh session to implement the plan
- **"Save plan as issue comment"** — post the plan as a comment on the issue for later
