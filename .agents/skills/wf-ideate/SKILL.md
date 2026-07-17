---
name: wf-ideate
description: >-
  Create a GitHub issue from an idea. Asks clarifying questions, researches
  official docs, and writes a well-structured issue. Use when starting new
  work, reporting a bug, or capturing an idea.
---

# Ideate — Create a GitHub Issue

Turn an idea or problem into a well-structured GitHub issue.

## Orient Yourself

Run `git branch --show-current` and `git log --oneline -5` to see the current branch and recent commits before starting.

## The Idea

Use the idea or problem description the user provided when they invoked this skill.

## Steps

### 1. Clarify

Ask the user clarifying questions about the idea before writing anything. Understand:
- What problem does this solve?
- What should the end result look like?
- Are there constraints or preferences?

### 2. Research

If the idea involves packages, APIs, or frameworks used in the project, look up the **official documentation only** (not blog posts or unofficial GitHub repos).

### 3. Write the Issue

Create a GitHub issue that includes:
- A clear title
- Problem description or motivation
- Implementation suggestions (high-level, not a full plan)
- Any relevant links to official docs

Use `gh issue create` to create the issue.

**Do NOT plan the implementation.** The goal is a well-written issue that another developer can pick up — not a step-by-step execution plan.
