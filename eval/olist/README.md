# Olist benchmark

This directory contains the frozen Phase 6.2A Olist benchmark. Its 20 questions
are separate from the six questions used during manual Agent development.

- `cases.json` contains evaluator-only reference SQL, PostgreSQL-derived expected
  results, and deterministic comparison contracts.
- `verify.py` recomputes the reference results against the fixed local
  `agentic_analyst_olist` database as `analyst_agent` in read-only transactions.
- `../olist_runner.py` reuses the existing deterministic evaluator for the future
  first official Agent baseline.

Verify the frozen reference results without Gemini:

```powershell
conda activate llm
python -m eval.olist.verify
```

The future baseline command below calls Gemini. It is intentionally not run as
part of Phase 6.2A:

```powershell
python -m eval.olist_runner
```

Reference SQL and expected results remain inside the evaluator and are never
included in the question sent to the Agent. Scoring is pass/fail; no LLM judge
or fuzzy subjective grading is used.
