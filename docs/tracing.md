# Local agent traces

With persistence enabled and a writable destination, each `run_agent()` invocation
saves one UTF-8 JSON document, including failed runs. The default project-root
`runs/` directory is created automatically and ignored by Git. File names contain
a UTC start timestamp and UUID run ID:

```text
runs/20261003T141812Z_<run_id>.json
```

`TRACE_ENABLED=false` disables file persistence without changing in-memory
observations, answers, or failure handling; API trace_path is then null.
`TRACE_DIR` selects another directory. Relative paths resolve from the repository
root; keep custom directories outside Git or ignore them. An unwritable directory
only emits a safe warning. `python -m app.trace --latest` uses the configured
directory. Ephemeral hosting disks can lose traces; there is no cloud storage
integration or public trace-download endpoint.

The manual loop, prompt, eight-response limit, tool behavior, validation recovery,
and read-only database protections are unchanged. Tracing makes no additional
model or SQL calls and adds no dependencies. Successful CLI runs can print the
saved path. Files are written to a temporary file in `runs/`, closed, then
atomically replaced. A persistence error produces a generic warning on stderr;
it does not replace the agent answer or original failure.

## Schema version 1

| Field | Meaning |
|---|---|
| `schema_version`, `run_id` | Format version and unique run identity |
| `started_at`, `finished_at`, `duration_ms` | UTC ISO timestamps and elapsed monotonic duration |
| `source`, `case_id`, `question` | CLI, evaluation, API, or other entry point; optional case ID; user question |
| `database_profile` | Safe API profile name (`sales`/`saas`); null for existing CLI/evaluation runs |
| `status`, `termination_reason` | Run completion or failure, independently of analytical correctness |
| `model` | Provider and model name when available locally |
| `turns` | Ordered invocation timing, response type, requested tool names/count, safe errors, optional token counts |
| `tool_calls` | Ordered tool IDs, model turns, arguments, timing, status, safe errors, bounded results |
| `sql_calls` | SQL text linked to its tool ID, timing, success/error, returned-row count and truncation metadata |
| `final_answer` | Delivered answer, or null when no answer was produced |
| `model_error` | Safe setup, binding, or invocation error diagnostic |
| `metrics` | Operation counts and total duration, without correctness judgments |

Run statuses are `success`, `iteration_limit`, `model_error`, `tool_error`,
`database_error`, and `unexpected_error`. Their termination reasons are `success`,
`iteration_limit`, `model_integration_error`, `tool_runtime_error`,
`database_unavailable`, and `unexpected_error`. Failed database connections retain
only fixed safe details; no native connection error text is persisted.
Validation failures are observations: the overall run can succeed after recovery.

Tool statuses are `success`, `validation_error`, and `runtime_error`. Non-validation
errors retain their distinguishing error type/category, including SQL execution
errors, query rejection, and unknown tools. SQL records use `success`/`error`.
An invalid SQL invocation is recorded with null SQL text if no `query` argument
was provided; its original invalid arguments remain in the tool record.

`model_turn_count` counts attempted invocations, including failed invocations.
The older evaluator `model_turns` still counts successfully received responses.
Model setup/binding failure has no fabricated invocation. Token usage is omitted
when unavailable; only nonnegative integer input/output/total counts are saved.
Raw model response objects and hidden reasoning are not recorded.

Metrics also include `tool_call_count`, `successful_tool_calls`, `failed_tool_calls`,
`sql_call_count`, `sql_execution_errors`, `tool_validation_errors`, `model_errors`,
`schema_tool_calls`, `describe_table_calls`, and `total_duration_ms`. SQL call count
includes invalid/rejected requests; it does not imply that they reached PostgreSQL.

## Preview policy

Tool results are persisted as metadata plus a text preview, never the unlimited
in-memory observation:

- At most **10 SQL row lines**, and at most **4,000 preview characters** total.
- Other tool text, including schema descriptions, is capped at **4,000 characters**.
- `size_chars`, `row_count`, `rows_shown`, `result_truncated`, and
  `preview_truncated` distinguish returned data from the stored preview.
- Row counts come from the existing renderer's footer; unknown counts are null.
- `result_truncated` describes the existing 100-row application cap.
  `preview_truncated` describes the smaller trace preview.
- The pipe renderer is text, not a lossless structured table format. Tracing
  neither parses SQL nor interprets pipe-separated cells as typed database data.

Full in-memory observations still drive tool messages and evaluation SQL
selection. Persistent previews do not change analytical behavior or output caps.
Questions, arguments/SQL, and final answers are retained, so these files can
contain business data. Keep them local and review them before sharing.

## Redaction

`app/trace.py` centralizes redaction. Configured database passwords and supported
API keys, secret-bearing fields, Authorization/Bearer text, credential URLs,
headers, and environment-dump fields are redacted. Redaction runs before previews
are sliced and again before JSON publication. Numeric token counts are allowlisted.
Exception messages, stack traces, provider requests/responses, and environment
dumps are not captured. Persistence warnings contain no paths or exception details.
Configured hosted connection URLs and their natively decoded passwords are also
redacted, including a password appearing alone rather than inside its URL.

## Inspect a run without rerunning it

```powershell
conda activate llm
python -m app.trace --latest
python -m app.trace "D:\ReAct Agent\runs\<trace-file>.json"
```

The reader prints the question, chronological turns/tools, SQL, row counts,
safe errors, final answer, and termination. It makes no model or database calls.
`--latest` selects the most recently modified completed JSON file. Unsupported
or unreadable files produce a short safe error.

## Evaluation linkage

Each new evaluation case records `run_id` and `trace_path`. Its trace has
`source="eval"` and the case ID. `latest.json` retains scoring results and aggregate
counts, but no duplicate tool-event array. Scoring still uses complete in-memory
observations and the existing read-only structured SQL re-execution.

The separate generalization suite uses `source="eval_generalization"`, its own
case IDs, and `eval/generalization/results/latest.json`. It reuses the same trace
and scoring mechanisms; reference answers remain outside agent traces.

Agent traces receive no `expected_result`, `reference_sql`, comparison contract,
or evaluator decision. A trace reporting success means an answer was delivered;
the evaluator can independently mark that answer's SQL result incorrect.

Legacy reports containing `tool_calls` remain readable by `eval.rescore`.
New linked reports rescore their saved structured `actual_result`; bounded
previews are never substituted for complete scoring evidence. Missing answers
remain failures. None of this reruns a benchmark or produces new model responses.

## Tests

```powershell
python -m unittest discover -s tests -p test_trace.py -v
python -m unittest discover -s tests -v
```

Model tests use fakes and temporary trace directories. The complete existing
suite also includes read-only local PostgreSQL integration checks; no automated
test calls Gemini.
