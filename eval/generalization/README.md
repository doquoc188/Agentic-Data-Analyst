# Unseen-database generalization suite

Phase 3.6 uses a separate synthetic SaaS database, `agentic_analyst_saas`, with
the existing manual agent and four generic tools. The original sales database,
24 sales cases, expected answers, and saved results stay separate.

## Verified status

The user completed the administrative migration. Read-only runtime checks and
all 16 reference queries succeeded as `analyst_agent`. Every case now stores a
PostgreSQL-derived `expected_result` with `ground_truth_verified: true`. Phase 3.6
preparation passed all 125 tests, including explicitly enabled SaaS checks.
The official recorded live SaaS baseline is **14/16 (87.50%)**; the separate Sales
baseline is **22/24 (91.67%)**. Phase 3 is closed. Phase 3.7 categorical grounding
was also verified live; no benchmark is rerun during release preparation.
For a future fresh fixture, pending cases use null/false markers and the runner
refuses them before calling Gemini.

## Database fixture

| Table | Verified rows | Relationships |
|---|---:|---|
| accounts | 60 | Account identity, country, signup date |
| plans | 4 | Plan tier and recurring price per seat |
| subscriptions | 120 | Account and plan foreign keys |
| invoices | 360 | Subscription foreign key |
| support_tickets | 240 | Account foreign key |

Each table has a real integer primary key. The four foreign keys, positive
prices/seats, allowed statuses, and timestamp/date checks are PostgreSQL
constraints. Fixed IDs, dates, arrays, and `generate_series` make fresh imports
repeatable without `RANDOM()` or dependence on today's date. Ticket timestamps
are generated in UTC. There are six countries, three plan tiers, active/paused/
cancelled subscriptions, paid/open/void invoices across three calendar months,
unresolved tickets, accounts without tickets, and accounts without paid invoices.

Column comments describe recurring USD price, active-status semantics, paid
invoice revenue, cancellation dates, and resolved-ticket duration. They contain
business definitions, never benchmark answers. The unmodified `describe_table`
tool exposes these comments and relationships at runtime.

## Administrative migration: user runs separately

Use the existing owner account and enter its password at the prompt. Do not put
administrative credentials in `.env` or paste passwords into chat.

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' `
  -X -v ON_ERROR_STOP=1 `
  -h localhost -p 5432 -U postgres -d postgres -W `
  -f 'D:\ReAct Agent\sql\generalization\00_create_database.sql'

& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' `
  -X -v ON_ERROR_STOP=1 `
  -h localhost -p 5432 -U postgres -d agentic_analyst_saas -W `
  -f 'D:\ReAct Agent\sql\generalization\01_create_schema.sql'
```

Database creation preserves an existing database. Schema/data creation requires
the exact target database and an empty public schema; it fails before changes
if public relations already exist. It never drops, truncates, or replaces tables.
The schema/data import is one transaction, so an error rolls it back. Repeat
verification freely; do not rerun the import over an existing fixture.

Optional owner-side read-only report:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' `
  -X -v ON_ERROR_STOP=1 `
  -h localhost -p 5432 -U postgres -d agentic_analyst_saas -W `
  -f 'D:\ReAct Agent\sql\generalization\02_verify.sql'
```

The migration grants existing `analyst_agent` CONNECT, public USAGE, and table
SELECT. It removes table write privileges and database/schema CREATE and
TEMPORARY access, including inherited PUBLIC grants in this new database.
Owner default table privileges grant SELECT for future tables. No role password
or privilege in the primary database is changed.

## Runtime verification: only after migration confirmation

Keep the primary `.env` DB_NAME unchanged and DB_USER on `analyst_agent`.
`load_dotenv()` already preserves existing process environment values. Override
DB_NAME for a single workflow and restore it afterward. This example also
preserves an override that was already present:

```powershell
conda activate llm
$previousDatabase = $env:DB_NAME
try {
  $env:DB_NAME='agentic_analyst_saas'
  python -m eval.generalization.verify --write-expected
} finally {
  if ($null -eq $previousDatabase) {
    Remove-Item Env:DB_NAME -ErrorAction SilentlyContinue
  } else {
    $env:DB_NAME=$previousDatabase
  }
}
```

The utility verifies current database/role, fixture counts, PK/FK definitions,
effective SELECT/write/CREATE privileges, `get_schema`, and `describe_table`
comments and relationships. It proves write denial with privilege queries,
without attempting destructive writes. It executes all reference queries as
`analyst_agent` through `app.database.get_connection()`. The verifier sets READ
ONLY directly on its Psycopg cursor, applies the existing 5,000 ms timeout and
100-row cap, and rolls back/closes each transaction. Reference SELECT/WITH SQL
uses the unchanged query guardrail. Privilege names such as INSERT and CREATE
are bound parameters, so harmless privilege checks do not trigger that guardrail.
No transaction-control SQL goes through the LangChain `execute_sql` tool.
Only after every check succeeds does it atomically save verified
expected results. Without `--write-expected`, it verifies existing ground truth.
Neither mode calls Gemini. The case metadata's `expected_columns` describes
reference query output structure; it is evaluator-only.

## Cases and comparison

There are exactly **16** cases: **4 easy, 6 medium, 6 hard**. They cover counts,
filters, DISTINCT, grouped SUM/AVG, monthly aggregation, 2/3/4-table reasoning,
rankings and explicit tie rules, an empty-result condition, recurring revenue,
paid invoices, and support-ticket duration. Questions specify required output
fields and distinguish current recurring revenue from historical invoice revenue.

Each case uses the original evaluator's explicit comparison contract: scalar
or table; positional aliases and requested columns; unordered rows when order
is irrelevant; ordered prefixes for top-k; cent tolerance for monetary values;
and month normalization only for the monthly case. There is no global relaxation
and no LLM judge. Every expected result comes from PostgreSQL, not hand calculation.

Reference SQL, expected results, verification markers, and comparison contracts
stay evaluator-only. The agent receives **only the question**. No prompt or
generic tool knows the SaaS schema in advance.

## Automated tests

Before migration:

```powershell
python -m unittest discover -s tests -p test_generalization.py -v
python -m unittest discover -s tests -v
```

The three new live database tests are skipped unless explicitly enabled.
Existing sales integration tests still use the primary database; all model tests
use fakes.

After successful migration and ground-truth generation, run the complete suite
without a global SaaS DB_NAME override. The new integration tests scope their
own override so existing sales tests retain the primary database:

```powershell
$env:RUN_GENERALIZATION_DB_TESTS='1'
try {
  python -m unittest discover -s tests -v
} finally {
  Remove-Item Env:RUN_GENERALIZATION_DB_TESTS
}
```

## Reproducing live evaluation (explicit approval required)

Wait for deterministic verification, the full test suite, and explicit user
approval before running the live runner. It checks verified ground truth and
the target database before invoking the agent. It reuses `eval.runner` and
`eval.selection`, including SQL selection, comparisons, recovery counts, and
summary metrics. It supports `--case` and `--limit` for future approved runs.

Preparation and deterministic checks are complete. The next command calls
Gemini; run it only after explicit approval:

```powershell
$env:DB_NAME='agentic_analyst_saas'
try {
  python -m eval.generalization_runner
} finally {
  Remove-Item Env:DB_NAME
}
```

Results go to `eval/generalization/results/latest.json`, ignored by Git. Each
case links `run_id` and `trace_path`; its single Phase 3.5 trace uses
`source="eval_generalization"` and the case ID. Trace previews are bounded and
redacted as before, with no ground truth or scoring decisions included. Existing
traces support later failure diagnosis without a new analysis framework.

The recorded 14/16 score is the official unseen-database baseline, not a guarantee
for arbitrary schemas. No target pass percentage is encoded, and no Sales or SaaS
benchmark is rerun automatically. Offline rescoring does not replace live scores.
