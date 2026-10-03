# Agentic Data Analyst: repository contract

## 1. Project Overview

This personal portfolio project builds an Agentic Data Analyst in small, teachable phases.
Keep the architecture simple so its agent mechanics remain understandable.

User → Gemini → manual LangChain tool-calling loop → Python tools → PostgreSQL
→ tool observations → Gemini final answer.

The loop is implemented by hand to show tool binding, calls, execution, and observations.
Do not replace it with an agent framework unless the user requests that phase.

## 2. Current Architecture

- `app/agent.py`: binds the four tools to Gemini, supplies system instructions,
  keeps the message list, dispatches every tool call, appends matching
  `ToolMessage` observations, and stops after at most eight model responses.
  `python -m app.agent` accepts an optional question argument. Every run persists
  an `AgentTrace` with model turns and tool calls before invocation,
  with pending/success/error status and safe failure details. Model integration
  failures retain an allowlisted phase/category/turn diagnostic, without raw errors.
  Argument-schema errors become matching tool observations for model correction;
  exceptions inside a tool still stop with a controlled failure.
- `app/trace.py`: per-run trace records, UTC/monotonic timing, centralized
  redaction, bounded result previews, atomic JSON persistence under Git-ignored
  `runs/`, deterministic operation metrics, and a read-only developer trace reader.
  It makes no extra model or database calls; persistence errors emit safe warnings.
- `app/tools.py`: LangChain `@tool` implementations of `calculator`,
  `get_schema`, `describe_table`, and `execute_sql`.
- `app/database.py`: reusable Psycopg 3 `get_connection()`; loads `.env` and
  requires database settings from environment variables.
- `app/llm.py`: loads `.env`, requires `GOOGLE_API_KEY`, and creates a
  temperature-0 `ChatGoogleGenerativeAI` model. It also has a connection check.
- `sql/01_normalize_sales.sql`: transactional, reproducible migration from
  `sales` to four normalized tables. It creates constraints and grants SELECT.
- `sql/02_verify_normalization.sql`: read-only counts, constraint, JOIN,
  revenue, row-matching, and role-privilege checks.
- `sql/03_discount_semantics.sql`: owner-run column comments documenting
  fractional discounts; installed and verified before the Phase 3.4.2 live run.
- `eval/cases.json`: 24 deterministic analytics questions with separate
  reference SQL, PostgreSQL-verified expected results, and comparison contracts.
- `eval/README.md`: case format and ground-truth separation rules.
- `eval/runner.py`: deterministic runner that sends questions to the agent,
  re-executes selected supporting SQL read-only, and reports case/summary metrics.
  Scalar comparisons are strict unless a case declares labeled-scalar support.
  New reports link `run_id`/`trace_path` rather than copying tool-event arrays.
- `eval/selection.py`: ground-truth-free answer-query selection heuristic;
  uses distinct answer evidence, question overlap, then recency; unmentioned
  result cells do not dilute support.
- `eval/rescore.py`: offline rescoring of saved evidence; preserves the original
  live results and labels changed questions. It calls neither Gemini nor PostgreSQL.
- `eval/generalization/`: 16 separate SaaS cases (4 easy, 6 medium, 6 hard),
  PostgreSQL-verified evaluator-only answers, and a direct Psycopg verifier.
  It validates reference SELECT/WITH SQL, requires `analyst_agent` and the SaaS
  database, sets READ ONLY/timeout on the cursor, and rolls back/closes safely.
- `eval/generalization_runner.py`: reuses deterministic scoring/selection and
  links persistent traces with `source="eval_generalization"`; questions alone
  reach the agent. Results remain separate from the sales suite.
- `sql/generalization/`: owner-run database/schema/data scripts and a read-only
  verification report for the deterministic synthetic SaaS fixture.
- `tests/test_agent.py`: mocked Gemini/agent registry and dispatch tests.
- `tests/test_tools.py`: calculator tests and live local PostgreSQL tool tests.
- `tests/test_eval_cases.py`: dataset format, coverage, and live read-only
  PostgreSQL ground-truth validation. It does not call Gemini.
- `tests/test_eval_runner.py`: offline runner and trace tests with fake agents.
- `tests/test_trace.py`: fake-model persistence, timing, previews, redaction,
  failure handling, evaluation linkage, and reader tests using temporary directories.
- `tests/test_generalization.py`: suite coverage, prompt isolation, direct
  verifier safety/lifecycle, tracing, environment override, and optional SaaS DB
  checks enabled by `RUN_GENERALIZATION_DB_TESTS=1`.
- `docs/tracing.md`: trace schema, storage, preview/security policy, and commands.
- `requirements.txt`: LangChain, Gemini integration, python-dotenv, Psycopg 3.

Use the code and SQL files as the source of truth. `.env` stays local; settings
are documented by name, never by secret values.

## 3. Current Tool Set

- `calculator`: explicit add, subtract, multiply, and divide operations.
- `get_schema`: dynamically lists columns and types of public BASE TABLES
  using PostgreSQL `information_schema`; it does not hard-code table names.
- `describe_table`: accepts a table name and describes public BASE TABLE
  columns, data types, nullability, defaults, column comments, and PK/FK metadata.
  It parameterizes table-name lookups; primary and foreign keys come from
  PostgreSQL constraint metadata, including ordered composite primary keys.
- `execute_sql`: accepts one analytical SELECT or supported WITH query.
  It rejects common write/DDL forms, runs a `READ ONLY` PostgreSQL
  transaction, sets a 5,000 ms statement timeout, returns at most 100 rows
  (`MAX_ROWS = 100`), and converts database errors into short observations.
  Its text checks are guardrails, not a complete SQL parser or security boundary.
  Do not add write capability without an explicitly requested architecture change.

## 4. Database Model

- Local database: `agentic_analyst`; schema: `public`.
- Current application role: `analyst_agent`. The code reads `DB_USER` from
  the environment, so keep the runtime configuration on this read-only role.
- Database settings: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
  `DB_PASSWORD`. Gemini setup uses `GOOGLE_API_KEY`. Never record values.
- `sales`: original denormalized source, currently 300 rows; retained for
  regression and revenue comparison. Its creation script is not in this repo.
- `customers`: `customer_id` PK; name and city; unique `(customer_name, city)`.
- `products`: `product_id` PK; name, category, catalog unit price; unique
  `(product_name, category)`.
- `orders`: original `order_id` PK; `customer_id` FK to `customers`; order
  date, payment method, and status.
- `order_items`: `order_item_id` PK; `order_id` FK to `orders`, `product_id`
  FK to `products`; quantity, transaction unit price, and fractional discount
  rate (`0.10` means 10%; revenue uses `1 - discount_pct`).

The current import has one `order_items` row per `sales` row; the schema can
hold multiple items per order later. The last verified counts were 20 customers,
12 products, 300 orders, 300 order items, and 300 sales rows. Recheck live data
before relying on these counts.

The separate synthetic SaaS database is `agentic_analyst_saas`, also using public
and `analyst_agent`. It contains accounts, plans, subscriptions, invoices, and
support_tickets with PK/FK constraints and business-unit column comments. Switch
databases with a process `DB_NAME` override, then restore it; `load_dotenv()`
preserves process settings. Keep the primary `.env` DB_NAME unchanged.

## 5. Database Safety Invariants

1. Gemini must never receive database credentials.
2. Application runtime uses `analyst_agent`, never the `postgres` superuser.
3. Keep `analyst_agent` read-only; do not grant it write privileges.
4. Keep `execute_sql` read-only and limited to analytical queries.
5. Treat application-side query checks as guardrails, not the security boundary.
6. Preserve independent PostgreSQL permission and READ ONLY transaction checks.
7. Keep SQL result output capped and execution timeout-protected.
8. Never log or expose `DB_PASSWORD`, `.env` values, or secret connection strings.
9. Keep `.env` ignored by Git; use environment variable names in documentation.
10. Make schema changes reproducible in SQL files, not only in pgAdmin.
11. Run administrative migrations separately as an authorized database owner;
    do not put admin credentials into application configuration.
12. Keep run traces local and Git ignored. Persist at most 10 SQL row lines and
    4,000 preview characters per tool result; redact secrets before serialization.
    Never capture raw provider objects, exception messages, headers, environment
    dumps, or evaluator ground truth in agent traces.

## 6. Agent Behavior Invariants

- Never invent database tables, columns, relationships, or business results.
- Use `get_schema` for an unfamiliar database structure.
- Use `describe_table` when detailed table metadata or relationships are needed.
- Ground unknown categorical/text filter literals in trusted metadata or bounded
  read-only value queries. An inferred, unverified literal producing zero rows
  or zero count needs grounding before a no-match conclusion. Reuse established
  values; verified zero results remain valid without repeated inspection.
- Follow trusted column comments for business units; avoid unnecessary table
  descriptions, repeated checks, or reopening resolved assumptions without evidence.
- Use `execute_sql` for real business results; do not fabricate query output.
- Treat SQL errors as observations; revise failed SQL before retrying, using
  metadata when needed. Never repeat the exact failed query unchanged.
- Treat argument-schema validation errors as observations with matching
  `tool_call_id`; let the model repair arguments, never silently rename them.
  Unexpected runtime exceptions remain controlled failures.
- If reasonable correction attempts fail, explain that the query could not
  be completed rather than inventing a result.
- Let Gemini choose the necessary tools; do not force a fixed tool sequence.
- Keep `calculator` available for arithmetic questions without database access.
- Handle unknown tool requests gracefully and return an observation to Gemini.
- Append the AI response before its tool observations; preserve each
  `tool_call_id`, including when one response contains multiple calls.
- Keep a maximum model-response limit to prevent unbounded loops.
- Conclude after sufficient successful evidence, including correctly filtered
  empty results; reserve response budget for the final answer. Avoid redundant
  sales cross-checks after a sufficient normalized result.

## 7. Completed Project Phases

- Phase 1 through 1.5: Gemini connection, calculator, and manual tool loop.
- Phase 2.0/2.0.5: local PostgreSQL and 300-row `sales` dataset; environment-based
  Psycopg connection. The source dataset setup is not scripted in this repo.
- Phase 2.1: dynamic public-schema `get_schema`.
- Phase 2.2: parameterized public-table `describe_table` with column and FK data.
- Phase 2.3: dedicated read-only `analyst_agent` role and guarded `execute_sql`
  with a read-only transaction, timeout, row cap, and concise error handling.
- Phase 2.4: all four tools bound to the manual loop; registry, dispatch,
  `ToolMessage` association, eight-response limit, and live analytics verified.
- Phase 3.0: normalized customers/products/orders/order_items with real PK/FK
  constraints; SQL migration and verification; revenue parity with `sales`;
  a live agent JOIN question verified.
- Phase 3.1: `describe_table` now reads primary-key columns from PostgreSQL
  catalogs under `analyst_agent`, preserving composite-key order and FK output.
- Phase 3.2: Gemini receives SQL errors as tool observations and is instructed
  to inspect metadata when needed, revise failed SQL, retry, and answer only
  from successful results. The manual loop and eight-response limit remain.
- Phase 3.3: 24 version-controlled evaluation questions with read-only
  reference SQL, stored PostgreSQL results, and coverage/ground-truth tests.
  The agent receives only the question in future evaluations.
- Phase 3.4 CLOSED: deterministic evaluation runner, per-run agent trace,
  read-only SQL re-execution, case comparisons, and aggregate metrics.
  Final fresh baseline: 22/24 (91.67%), zero SQL execution errors, and live
  argument-validation recovery verified. No further benchmark tuning is planned.
- Phase 3.4.1: explicit strict/positional and ordered/unordered comparison
  contracts, requested-field projection, month normalization, stated tie rules,
  supporting-SQL selection, and failed-invocation traces. Metrics separate multiple
  SQL calls from execution errors and recovery. Saved-run rescoring is not a new
  live benchmark.
- Phase 3.4.2 implementation complete: independent requested-field projection,
  distinct answer-support SQL selection, explicit strict ranked-prefix contracts,
  convergence/empty-result guidance, column-comment metadata, sanitized model
  failures, and separate failure metrics. Discount comments are installed.
  The subsequent fresh live run recorded 22/24 (91.67%).
- Phase 3.4.3 implementation complete: recoverable argument-schema validation
  observations, generic schema feedback, opt-in labeled-scalar comparison,
  and separate validation-error/recovery metrics. Subsequent fresh verification
  confirmed one validation error recovered in a passing case.
- Phase 3.5 complete: persistent structured observability in `runs/`, ordered
  model/tool/SQL timing, safe errors, bounded previews, deterministic metrics,
  trace reader, and evaluation linkage. All 103 tests passed, including local
  PostgreSQL checks. No Gemini calls or benchmark reruns during implementation;
  prompt, eight-response limit, tools, and read-only protections are unchanged.
- Phase 3.6 complete: the user installed the separate synthetic SaaS
  migration; runtime schema/comments/PK/FK/read-only privileges and all 16
  PostgreSQL reference results are verified. Direct verification parameterizes
  privilege names without changing agent query safety. All 125 tests passed with
  SaaS checks enabled. Agent prompt/tools and the sales benchmark are unchanged.
  Official baselines: original sales **22/24 (91.67%)**, unseen SaaS
  **14/16 (87.50%)**. The SaaS diagnostic found one categorical-value grounding
  miss and one separate provider RESOURCE_EXHAUSTED interruption.
- Phase 3.7 categorical grounding implemented: generic prompt guidance for exact
  stored literals, targeted metadata/bounded value inspection, ungrounded-zero
  checks, value reuse, and valid grounded zero conclusions. Fake-model tests
  cover correction and convergence; all 129 tests passed with SaaS DB checks
  enabled. Tools, tracing, loop, eight-response limit, provider policy, and both
  benchmark suites are unchanged. Fresh live benchmark verification is pending.

## 8. Current Known Issue / Next Work

**Known limitations:** Provider RESOURCE_EXHAUSTED remains a separate failure
mode; Phase 3.7 adds no provider retries. Prompt compliance and impact on live
categorical-filter accuracy are unmeasured until fresh verification.

**Next step: fresh unseen SaaS benchmark verification.** Wait for explicit user
approval before calling Gemini. After that result is known, an original sales
regression benchmark may be requested separately; do not run either automatically.

## 9. Scope Discipline

- Work only on the phase the user requests; inspect current code first.
- Preserve completed behavior and avoid unrelated refactoring.
- Keep changes small, readable, and educational; reuse existing modules.
- Do not introduce LangGraph, FastAPI, UI, RAG, vector databases, or a prebuilt
  SQL agent unless an explicitly requested phase calls for them.
- Never silently modify database schema, permissions, or source data.
- Inspect Git status before editing and preserve unrelated uncommitted work.

## 10. Testing Rules

Use the existing Conda environment `llm`; do not create or use `.venv`.
The complete suite is:

```powershell
conda activate llm
python -m unittest discover -s tests -v
```

Run focused tests for relevant changes, then the complete suite. Preserve
existing passing tests unless the requested specification intentionally changes.
Distinguish mocked unit tests, local PostgreSQL integration tests, and live
Gemini end-to-end checks. Do not send local business/query data to an external
model during verification without explicit approval when required.

## 11. Development Workflow

1. Read this file, inspect the relevant code and tests, and check Git changes.
2. State which files will change and why before editing.
3. Implement only the requested phase with the smallest clear change.
4. Run focused checks and the complete test suite.
5. Report files changed, key decisions, results, and exact manual commands.
6. For elevated PostgreSQL work, provide a separate `psql` migration command;
   leave runtime `.env` configured for `analyst_agent` and never log passwords.

## 12. Maintain This File

After each completed phase or material architecture change, update only the
sections that need it. Move finished work into the concise phase summary,
refresh the next-work section, and avoid appending a chronological chat log.
