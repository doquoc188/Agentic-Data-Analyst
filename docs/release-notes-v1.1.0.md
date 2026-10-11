# v1.1.0 release notes

**Status: release preparation for maintainer review; not tagged or released by
this documentation change.**

## Release summary

v1.1.0 adds a local real-world evaluation path based on the
[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).
This is an anonymized public dataset, not customer production data. The existing
generic Agent and SQL tools are used without an Olist-specific query path.

The local importer loads **550,759 rows** into eight relational PostgreSQL tables:

- `customers`
- `orders`
- `products`
- `sellers`
- `order_items`
- `payments`
- `reviews`
- `category_translation`

Raw CSV files remain local and Git ignored. Local runtime access uses the
read-only `analyst_agent` role, SELECT-only grants, and READ ONLY transactions.

Deterministic final-answer evidence grounding was also added. It conservatively
removes unsupported units, currency symbols, and alternate display labels when
they are not supported by the user request, database metadata, or tool results.

## Frozen Olist evaluation

The Olist suite contains 20 frozen cases:

- 4 easy
- 8 medium
- 8 hard

Reference SQL and expected results remain evaluator-only. The benchmark and
reference SQL stayed frozen during tuning, and scoring uses deterministic result
comparison with no LLM judge.

| Olist evaluation | Passed | Accuracy | Status |
| --- | ---: | ---: | --- |
| Official unseen baseline | 14/20 | **70.00%** | Frozen first valid analytical run |
| Post-tuning #1 | 17/20 | **85.00%** | Best retained result |
| Post-tuning #2 | 16/20 | **80.00%** | Rejected refinement experiment |

The best retained configuration improved by **3 cases and 15 percentage points**
over the official unseen baseline. The **17/20 (85.00%)** result is post-tuning
and must not be interpreted as an unseen score. A later refinement experiment
scored **16/20 (80.00%)** and was reverted. After the final configuration
decision, no further prompt tuning was performed against the frozen Olist
benchmark.

## Historical benchmark results

| Dataset / evaluation | Passed | Accuracy |
| --- | ---: | ---: |
| Sales | 22/24 | **91.67%** |
| SaaS unseen | 14/16 | **87.50%** |
| Olist unseen baseline | 14/20 | **70.00%** |
| Olist best retained post-tuning | 17/20 | **85.00%** |

These are separate, limited evaluations. They do not establish universal
database generalization.

## Production boundary

Olist remains a local evaluation dataset. It is unavailable from the production
FastAPI profile list, React frontend, MCP interface, and Neon databases. The
existing production deployment remains limited to the synthetic Sales and SaaS
datasets.

This release preparation does not change runtime code, tools, prompts,
benchmarks, evaluator behavior, database configuration, or deployment. See the
[Olist setup and evaluation evidence](olist.md), the
[frozen benchmark documentation](../eval/olist/README.md), and the preserved
[v1.0.0 release notes](release-notes-v1.0.0.md).
