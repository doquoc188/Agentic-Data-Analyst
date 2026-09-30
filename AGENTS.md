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
  `python -m app.agent` accepts an optional question argument.
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
- `tests/test_agent.py`: mocked Gemini/agent registry and dispatch tests.
- `tests/test_tools.py`: calculator tests and live local PostgreSQL tool tests.
- `requirements.txt`: LangChain, Gemini integration, python-dotenv, Psycopg 3.

Use the code and SQL files as the source of truth. The current README has an
outdated folder tree; `.env.example` is mentioned there but is not present.

## 3. Current Tool Set

- `calculator`: explicit add, subtract, multiply, and divide operations.
- `get_schema`: dynamically lists columns and types of public BASE TABLES
  using PostgreSQL `information_schema`; it does not hard-code table names.
- `describe_table`: accepts a table name and describes public BASE TABLE
  columns, data types, nullability, defaults, and PK/FK metadata.
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
  FK to `products`; quantity, transaction unit price, and discount percent.

The current import has one `order_items` row per `sales` row; the schema can
hold multiple items per order later. The last verified counts were 20 customers,
12 products, 300 orders, 300 order items, and 300 sales rows. Recheck live data
before relying on these counts.

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

## 6. Agent Behavior Invariants

- Never invent database tables, columns, relationships, or business results.
- Use `get_schema` for an unfamiliar database structure.
- Use `describe_table` when detailed table metadata or relationships are needed.
- Use `execute_sql` for real business results; do not fabricate query output.
- After a SQL error, use metadata and observations to correct the query.
- Let Gemini choose the necessary tools; do not force a fixed tool sequence.
- Keep `calculator` available for arithmetic questions without database access.
- Handle unknown tool requests gracefully and return an observation to Gemini.
- Append the AI response before its tool observations; preserve each
  `tool_call_id`, including when one response contains multiple calls.
- Keep a maximum model-response limit to prevent unbounded loops.

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

## 8. Current Known Issue / Next Work

**Next planned phase: 3.2.** SQL error recovery and self-correction/retry
behavior. Wait for the user's phase requirements before implementing it.

Planned, not implemented: Phase 3.3 evaluation questions; Phase 3.4
evaluation runner and metrics; Phase 3.5 traces and observability.

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
