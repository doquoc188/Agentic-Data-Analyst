# Analytics evaluation cases

`cases.json` is a 24-question benchmark for the current local PostgreSQL fixture.
It is evaluation data, not an agent tool.

Each case has an `id`, `category`, `difficulty`, natural-language `question`,
read-only `reference_sql`, `expected_result`, and capability tags in `requires`.
The question is what an analyst would ask the agent. The reference SQL is an
independently checked way to calculate ground truth. The expected result is
the actual PostgreSQL result, not an answer written by the agent.

An `expected_result` is a simple JSON value for a one-cell answer. For a list,
it is `{"columns": [...], "rows": [[...], ...]}`. Stored expected results and
reference SQL retain the full verified ground truth; comparison may explicitly
project only requested fields.

Each case now has a `comparison` contract. Defaults are strict if it is omitted:

```json
{
  "result_type": "table",
  "column_matching": "position",
  "row_order": "unordered",
  "numeric_tolerance": 0.01,
  "time_granularity": null,
  "required_columns": ["product_name", "revenue"]
}
```

- `result_type`: `scalar` requires one row/one column; `table` compares rows.
- `scalar_support`: optional, scalar-only support for a labeled table result:
  `{"label_column": "status", "label_value": "Completed", "value_column": "count"}`.
  Ordinary one-cell results still work. Otherwise, the named label and value
  columns must each occur once, and exactly one row must match the declared label.
  Its measure is compared with the expected scalar using numeric tolerance.
  Missing/ambiguous columns, absent/duplicate labels, and wrong values fail.
  Only `completed_orders` opts in. Strict scalar shape remains the default;
  neither row selection nor SQL selection searches for the expected number.
- `column_matching`: `strict` requires column names; `position` allows harmless
  aliases while preserving field positions and requested field counts.
- `required_columns`: optional projection of the stored expected columns.
  Each side resolves requested names independently, allowing extra irrelevant
  fields and reordered requested fields. Strict matching requires those names.
  Positional matching also allows aliases in the declared minimal requested
  layout, or the full stored layout with supplementary fields unchanged. An
  arbitrary extra/reordered projection with missing names is ambiguous and fails;
  missing fields cannot borrow another known business field's position.
- `row_order`: `strict` compares rows in sequence; `unordered` compares all row
  occurrences, preserving duplicates and numeric tolerance. Rankings stay strict;
  group/set questions need not have a particular presentation order.
- `row_match`: `exact` by default. Explicit `prefix` accepts extra trailing rows
  only after every expected leading row matches in strict order. It requires a
  nonempty table expectation and cannot be combined with unordered matching.
  Six nonempty top-k cases opt in; empty/grouped results remain exact. An
  incorrect first-place value or tie order still fails.
- `numeric_tolerance`: Decimal-safe absolute tolerance, applied before JSON
  serialization. Incorrect discount calculations still fail.
- `time_granularity`: `month` makes month-start dates/timestamps and `YYYY-MM`
  equivalent. Date precision is otherwise preserved.

Ranking questions explicitly state the reference query's tie rules. Questions
also clarify required counts/amounts where needed. Unrequested city/category
context is excluded by projection; no reference SQL or expected business values
were changed during calibration.

During evaluation, the runner sends **only `question`** to the agent.
`reference_sql` and `expected_result` stay in the evaluator. The runner records
the final answer and a link to its persistent tool trace, selects a supporting successful SQL call, then
re-runs that query under the read-only `analyst_agent` role with a timeout and
row limit. Structured Psycopg results are scored against the comparison contract.
This scores the SQL result, not the wording of the final answer.

New case outputs include `run_id` and `trace_path` rather than duplicating the
tool-event array. Agent traces live under Git-ignored `runs/`, with `source="eval"`
and `case_id`. They contain only agent observations, never reference SQL, expected
results, or comparison decisions. SQL selection still uses complete in-memory
observations, not the bounded persistent previews. See [the tracing guide](../docs/tracing.md).

SQL selection is a deterministic heuristic in `selection.py`: rank completed
SQL observations by explicit empty-result support, distinct numeric answer
values, distinct other answer values, strongest matching row, question-term
overlap, then recency. Numbers may match their cent-rounded form; numbered-list
markers are excluded. Unmentioned cells do not dilute evidence and repeated
cells do not add votes. Empty observations support answers stating no matches;
"every/all" also supports an empty result for negative questions such as "never".
Without a final answer, selection is provisional for diagnostics only. With an
answer but no matching observation values, selection reports insufficient support.
The selected `answer_sql_call_id` and `sql_selection_reason` are recorded.
The selector receives only tool calls, final answer, and question: no case ID,
comparison contract, expected values, or reference SQL. It cannot cherry-pick a
query because it matches ground truth.

This heuristic can be ambiguous when several queries return overlapping values,
the answer omits details, or numbers are mentioned incidentally. It does not
prove that a query supports every claim in the answer. The existing pipe-rendered
observations are used only as selection evidence in fresh runs; correctness uses
structured database re-execution, not parsed terminal prints.

Trace events are recorded before invocation (`pending`) and then marked `success`
or `error`. Argument validation uses the existing LangChain input schema before
invoking the tool with its original arguments. A validation failure remains an
error with `error_category=tool_argument_validation_error` and becomes a matching
`ToolMessage`: field/error codes, expected field types, and received field names,
without raw input values. The model may repair the call within the same eight
responses; arguments are never silently renamed. Other runtime exceptions,
including validation errors inside the tool body, still stop with a controlled
failure (`error_category=tool_runtime_error`). Secret fields and configured
password/API-key values are redacted. No extra retry counter is introduced.

Model failures are separate from tool failures. `model_error` retains the phase
(`model_setup`, `tool_binding`, or `model_invocation`), exception type, allowlisted
provider category if available, and attempted model turn (zero for setup/binding).
`model_turns` still counts successfully received responses. Exception messages,
headers, requests, and provider response objects are not saved. No broad retry
policy is added. Unknown provider categories remain `unknown`.

The agent's convergence guidance accepts correctly filtered empty results,
stops after sufficient evidence, and avoids redundant metadata/source checks.
The eight-response safeguard and manual dispatch remain. Business units are
provided by PostgreSQL column comments through `describe_table`, rather than
benchmark data or prompt-specific answers. `sql/03_discount_semantics.sql` adds
fractional-discount comments to sales/order_items; an owner must run it separately.

From the project root, with the existing Conda environment:

```powershell
conda activate llm
python -m eval.runner --case total_orders
python -m eval.runner --limit 3
python -m eval.runner
```

Each command writes `eval/results/latest.json` with case traces, pass/fail
reasons, and summary metrics: overall, difficulty/category/capability pass rates,
average tool and SQL calls, SQL execution errors/recoveries, and schema-tool usage.
It replaces the previous `latest.json`. Live evaluation calls Gemini and may
incur API usage; the automated runner tests use fake agents and do not call it.

Metrics distinguish `total_sql_attempts`, `cases_with_multiple_sql_calls`,
`sql_execution_errors`, `cases_with_sql_errors`, `sql_invocation_errors`, and
`sql_rejections`. Multiple successful SQL calls can be exploration/confirmation;
they are not called error retries. `recovered_after_sql_error` requires a real
SQL execution-error observation, a later successful SQL call, and a passing case.
It does not infer semantic revisions from SQL text changes. Per-case `sql_errors`
is retained as an alias of `sql_execution_errors` for older consumers.
Additional summary counters are `iteration_limit_failures`,
`model_integration_errors`, `tool_invocation_errors` (failed invocations across
all tools), and `evaluator_selection_failures`. The last counts missing/unsupported
answer-query selections, not inferred semantic selector mistakes. Per-case
`failure_type` distinguishes these from SQL execution errors/rejections,
comparison mismatches, and evaluator SQL failures.

`tool_argument_validation_errors` counts argument-schema failures across all
tools, separately from SQL execution errors. The summary's
`cases_recovered_after_tool_validation_error` counts cases with a failed argument
validation, a later successful invocation of that same tool, and a passing final
evaluation. Per-case `recovered_after_tool_validation_error` stores that boolean.
An unrelated tool success, a failed case, or a model integration failure does not
count as recovery. Validation observations count toward invocation-error metrics
even though their result text is now nonempty. Legacy validation events lacking
`error_category` remain readable for offline rescoring.

To rescore the previous saved run without Gemini or PostgreSQL:

```powershell
python -m eval.rescore
```

This writes `eval/results/local_rescore.json` and preserves `latest.json`. It uses
saved structured `actual_result` when available for the selected call. Otherwise,
it decodes a complete legacy tool observation, rejecting truncated/malformed
tables. That legacy renderer is lossy (unescaped pipes, numeric-looking text,
literal `NULL`); it is suitable for this saved fixture's simple values, not a
general substitute for structured capture. Missing failed calls cannot be rebuilt.
Saved structured results are associated with the original `answer_sql_call_id`
(or original query for older artifacts), never merely the last successful call.
Runs without a recorded final answer remain failures even if a correct SQL
observation exists. Old generic model exceptions retain unknown diagnostic
details; offline rescoring does not manufacture their cause or missing responses.

For new linked reports, rescoring uses the already associated structured
`actual_result` and preserves the trace link. It does not reconstruct complete
SQL results from bounded trace previews. The legacy path above remains supported.

The report is labeled **offline rescore of the previous live run**, records the
original question and whether it changed, and applies current contracts/tie rules
to old answers. Changed questions have not been rerun. A local rescore is not a
fresh live benchmark; a fresh Gemini run needs the user's explicit approval.

To revise a case, write a distinct analyst question, a read-only reference
query with explicit joins and deterministic ordering, then execute that query
against PostgreSQL and record its real result. For this version, keep exactly
24 cases; expand the case count only in a later requested benchmark update.
If the fixture data changes, regenerate and verify every affected expected
result from PostgreSQL. Never guess a result or copy an unverified model query.

From the project root, validate the cases and then run the complete suite:

```powershell
conda activate llm
python -m unittest discover -s tests -p test_eval_cases.py -v
python -m unittest discover -s tests -v
```
