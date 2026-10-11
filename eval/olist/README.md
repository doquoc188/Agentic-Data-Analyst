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

## Post-tuning evaluation #1

Phase 6.3 added generic SQL result-shape and aggregation-grain guidance without
changing this frozen benchmark or its evaluator. The first post-baseline run
recorded **17/20 (85.00%)**: easy **4/4**, medium **7/8**, and hard **6/8**.
Compared with the official unseen baseline, this is an improvement of **3 cases**
and **15 percentage points**. It is a tuned evaluation and must not be described
as an unseen score; the official unseen Olist baseline remains **14/20 (70.00%)**.

All 14 baseline-passing cases remained passing. Three baseline failures were
corrected:

- `olist_review_comment_percentage`: the final SQL returned only the requested
  percentage.
- `olist_credit_card_multi_installment_percentage`: categorical exploration was
  kept separate from the final scalar query.
- `olist_delivered_payment_item_total_mismatches`: payment and item child rows
  were independently aggregated to order grain before their aggregates were
  joined.

Three cases remained failed:

- `olist_late_delivery_percentage`: the business value was correct, but the
  query still returned extra supporting columns that the frozen scalar contract
  rejected.
- `olist_top_categories_by_freight_ratio`: the requested values were correct,
  but extra total-price and total-freight columns caused another frozen
  result-shape rejection.
- `olist_top_categories_by_review_score`: raw detail joins still duplicated
  review observations before averaging. `COUNT(DISTINCT order_id)` corrected the
  count but did not correct the weighting of `AVG`.

Execution produced 20/20 final answers, 76 model responses, 56 tool calls, and
29 SQL attempts. One SQL execution error was recovered inside the existing
manual loop; there were no provider failures and no benchmark or case reruns.
The benchmark fixture, expected results, and evaluator remained unchanged.

The ignored artifact is preserved locally as `results/post-tuning-001.json`.
Its SHA-256 is:

```text
1A8D9DEA342D73C6192E44EFBBD1736808956815F1EA518FBDE01C92A6DD601B
```

## Final evaluation decision

The Phase 6.4A final-refinement experiment recorded post-tuning evaluation #2
at **16/20 (80.00%)**. It corrected `olist_late_delivery_percentage`, but it did
not correct `olist_top_categories_by_freight_ratio` or
`olist_top_categories_by_review_score`. One previously passing case ended after
a provider `RESOURCE_EXHAUSTED` failure. Another previously passing case
retrieved and stated the correct result, but later unnecessary exploration led
the frozen SQL-selection heuristic to choose a broader result. The recorded
score therefore fell below post-tuning evaluation #1.

Phase 6.4A was reverted. The retained Phase 6.3A configuration produced
post-tuning evaluation #1, the highest deterministic Olist score at
**17/20 (85.00%)**. It improved on the unseen baseline by **3 cases** and
**15 percentage points**, with no regressions among the 14 baseline-passing
cases. The final refinement experiment did not provide enough measured benefit
to retain.

The final result history is:

| Evaluation | Result | Status |
| --- | ---: | --- |
| Sales | 22/24 (91.67%) | Historical official score |
| SaaS unseen | 14/16 (87.50%) | Historical official score |
| Olist unseen baseline | 14/20 (70.00%) | Official frozen unseen baseline |
| Olist post-tuning #1 | 17/20 (85.00%) | Best retained result; Phase 6.3A |
| Olist post-tuning #2 | 16/20 (80.00%) | Rejected Phase 6.4A experiment |

The ignored local artifacts are preserved without being tracked:

| Artifact | SHA-256 |
| --- | --- |
| `results/attempt-002-first-valid-baseline.json` | `810FBAC54BE4978F3EAC2B14EE19B456D97C815A7CF65E6BF2DB85A2092F308B` |
| `results/post-tuning-001.json` | `1A8D9DEA342D73C6192E44EFBBD1736808956815F1EA518FBDE01C92A6DD601B` |
| `results/post-tuning-002.json` | `4108622ACDE16CDF37F4A0E4F4E985AC7670A84406A09AAFDAF1E6B3C19D85A2` |

No further prompt tuning will be performed against this frozen benchmark.
