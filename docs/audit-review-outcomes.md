# Project plan

This file is a home for ideas that are useful but too large or unfinished for a
single issue. It should record decisions, open questions, and safe next steps.

## Audit Review outcomes and appeals

### Goal

Keep a permanent, searchable Account-level record of how each Audit Review
ended. Later, connect that history to appeals, which currently happen outside
Salesforce.

For this project, a completely favorable result means a Certificate,
Conditional Certification, or Part-B jobsite audit. The other outcomes to
distinguish are Drop, Additional Audit Needed, and Cautionary Letter.

### Completed export work

The `audit-review-outcomes` command creates a read-only CSV containing Audit
Reviews created during the rolling two-year window. It includes:

- Audit Review display name (for example, `AR-000123`) and created date
- Account name and Salesforce Account ID
- CRG Outcome and CRG Comments
- Six mutually exclusive review columns: `standard`, `drop`, `additional`,
  `cautionary_letter`, `scope`, and `needs_manual_review`

### Agreed matching rules

The exporter searches both CRG fields without caring about capitalization.

- `CRG Withdrawal` is always `drop`.
- `Certificate Recommended`, `Certification Recommended`, and `Certificate
  Processed` are `standard` unless comments explicitly describe a scope change
  or mention a Cautionary Letter.
- `CRG Follow Up Needed` is always non-standard. A `drop` or `withdraw` term
  is `drop` unless a certification code appears, in which case it is `scope`.
- `Closed Without Certificate` is `additional` for an Additional Audit, and
  `standard` for Conditional Certification, Part B, or a jobsite audit. It
  needs manual review when there is no clarifying text.
- `additional` matches `additional`, `add'l`, and `addt'l`. The phrase
  `additional audit`, when no jobsite is mentioned, takes precedence over a
  certification code; `full scope additional audit` is not a scope change.
- `cautionary_letter` requires `cautionary`; `letter` alone is unclear, and
  `conditional` prevents a Cautionary Letter match.
- `scope` recognizes the certification codes in the glossary with scope
  language (`add`, `remove`, `upgrade`, or `scope`). `grant <code> only` and
  an explicit comment that the scope is changing also match.
- A Drop plus Additional result, or a Scope plus Cautionary Letter result,
  requires manual review. Drop or Additional takes precedence over a potential
  Cautionary Letter result.
- Familiar favorable phrases such as `renew certification`, `recommend
  renewal`, and `grant certification` are left unflagged. Empty CRG text is
  also unflagged. Other meaningful text is flagged as `needs_manual_review`.

The six exported columns are derived from one classification, so no row has
more than one true outcome flag. These are first-pass rules for manual review,
not final outcome decisions.

Run it with:

```bash
uv run aisc_salesforce audit-review-outcomes
```

By default it creates `audit_review_outcomes.csv`. Choose another location with
`--output PATH`.

### Decisions still needed

- Confirm whether the two-year window should use Audit Review `CreatedDate`,
  `LastModifiedDate`, a completion date, or the parent Audit date.
- Review false positives and false negatives in the agreed matching rules.
- Agree on stable Account-post wording and hashtag(s). The post should include
  enough information to trace it to the source Audit Review without exposing
  unnecessary detail.
- Decide how reruns avoid duplicate Account posts and whether a correction
  creates a new post rather than replacing history.

### Later phases

1. Add an interactive command that guides a reviewer through Audit Reviews
   flagged as `needs_manual_review`. It is documented here only and is not part
   of the current export work.
2. Use Audit Review field history to help clarify unclear reviews.
3. Filter out Audit Reviews attached to Audits with New York scope.
4. Add a preview-only command that shows the exact Account posts it would make.
5. Add an explicit apply step that posts approved, standardized language and
   records an audit trail.
6. Design appeal intake and status tracking around the Account-level outcome
   history.
7. Add reporting that connects outcomes, appeals, and final appeal results.

No Salesforce posting or appeal automation is part of the current step.
