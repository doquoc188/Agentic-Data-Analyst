# Olist benchmark

This directory contains the Phase 6.2A Olist benchmark, frozen at commit
`4fca654`. Its 20 questions are separate from the six questions used during
manual Agent development.

- `cases.json` contains evaluator-only reference SQL, PostgreSQL-derived expected
  results, and deterministic comparison contracts.
- `verify.py` recomputes the reference results against the fixed local
  `agentic_analyst_olist` database as `analyst_agent` in read-only transactions.
- `../olist_runner.py` reuses the existing deterministic evaluator for an
  explicitly authorized live Agent run.

Verify the frozen reference results without Gemini:

```powershell
conda activate llm
python -m eval.olist.verify
```

The baseline command below calls Gemini and is never part of offline validation:

```powershell
python -m eval.olist_runner
```

Reference SQL and expected results remain inside the evaluator and are never
included in the question sent to the Agent. Scoring is pass/fail; no LLM judge
or fuzzy subjective grading is used.

## Official baseline

Phase 6.2B attempt #1 was an invalid analytical run. All 20 cases ended with
`ConnectError` during the first model invocation, before Agent execution, tool
calls, SQL, or final answers. It is preserved for infrastructure diagnosis and
is not treated as an analytical score.

Attempt #2 is the first valid official Olist analytical baseline:

- **Official Olist baseline: 14/20 (70.00%)**
- Easy: **3/4 (75.00%)**
- Medium: **6/8 (75.00%)**
- Hard: **5/8 (62.50%)**
- 20/20 final answers, 56 tool calls, 30 SQL attempts, zero SQL execution
  errors, and zero provider failures

Four cases were rejected because the frozen evaluator could not project the
requested result from extra supporting columns or a different result shape,
even though the Agent stated the correct requested business values:

- `olist_review_comment_percentage`
- `olist_late_delivery_percentage`
- `olist_credit_card_multi_installment_percentage`
- `olist_top_categories_by_freight_ratio`

Two cases were genuine analytical aggregation failures:

- `olist_top_categories_by_review_score`: raw order-item joins duplicated review
  rows, so order-category relationships needed deduplication before averaging.
- `olist_delivered_payment_item_total_mismatches`: payment rows and item rows
  were joined before independent per-order aggregation, causing many-to-many
  multiplication.

The diagnostic business-answer correctness is **18/20**, but this is
**non-official diagnostic evidence only**. It does not replace or rescore the
official frozen baseline of **14/20**.

The ignored attempt #2 artifact is preserved locally as
`results/attempt-002-first-valid-baseline.json`. Its SHA-256 is:

```text
810FBAC54BE4978F3EAC2B14EE19B456D97C815A7CF65E6BF2DB85A2092F308B
```

Raw result artifacts and linked traces remain Git ignored. The historical
scores remain Sales **22/24 (91.67%)**, unseen SaaS **14/16 (87.50%)**, and the
official Olist baseline **14/20 (70.00%)**.
