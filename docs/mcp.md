# Local MCP interface

Phase 5.1 adds interoperability: MCP-compatible clients can discover and query
the same synthetic Sales and SaaS datasets through the existing read-only tools.
The server does not create an LLM or require `GOOGLE_API_KEY`. An external client
may use its own model; that client's model usage and data handling are separate.

## Architecture

```text
Web: Vercel -> FastAPI -> manual LangChain Agent -> tools -> PostgreSQL
MCP: local MCP client -> stdio MCP server -> same tools -> PostgreSQL
```

The web agent does not use MCP internally. `app/mcp_server.py` is an independent
entrypoint using the official Python MCP SDK. The supported 1.x API is bounded
with `mcp>=1.30,<2` and tested with 1.30.0 on Python 3.10.20; no CLI extra is needed.
The low-level server provides explicit schemas and safe MCP-native error results.
See the [official SDK documentation](https://py.sdk.modelcontextprotocol.io/v1/).

## Tools

| MCP tool | Required arguments | Result |
| --- | --- | --- |
| `list_database_profiles` | None (`{}`) | JSON text: supported public IDs, names, descriptions; no connection check |
| `get_schema` | `profile` | Dynamic public BASE TABLES, columns, and types |
| `describe_table` | `profile`, `table_name` | Public table columns, defaults, nullability, comments, PK/FK metadata |
| `execute_sql` | `profile`, `query` | Existing column/row text format, row count, and truncation indicator |

Choose `sales` or `saas` explicitly on every database call. Missing arguments,
incorrect types, additional fields, and unknown profile IDs fail safely. Clients
cannot supply hosts, connection URLs, credentials, or arbitrary destinations.
For example:

```json
{"profile": "sales", "table_name": "orders"}
```

`list_database_profiles` advertises the supported profiles, not their live
availability. It can be used without database configuration or credentials.
Schema discovery and query calls require the chosen profile's private settings.

## Routing and safety

Each database operation loads the existing settings and calls `resolve_profile()`.
The corresponding `DATABASE_SALES_URL` or `DATABASE_SAAS_URL` takes precedence.
Otherwise the existing `DB_HOST`, `DB_PORT`, `DB_USER`, and `DB_PASSWORD` settings
are used with the profile's local database name. Dotenv loading preserves process
environment overrides. No separate connection configuration is introduced.

`database_context()` scopes the selected destination to that operation and resets
it in `finally`, including on failure. The adapter calls the original LangChain
tool's `.invoke()`; it contains no schema queries or alternate SQL executor.
Synchronous database work runs in a worker thread without blocking protocol IO.

The existing defenses remain:

- One SELECT or supported WITH; existing write/DDL and multi-statement rejection.
- PostgreSQL `READ ONLY` transaction before analytical SQL.
- Five-second connection timeout and 5,000 ms analytical statement timeout.
- At most 100 result rows; larger results include a truncation indicator.
- Parameterized public-table metadata lookups and dynamic schema discovery.
- Runtime `analyst_agent` with SELECT-only PostgreSQL grants: the final permission boundary.

Configure only restricted runtime credentials, never owner/superuser URLs. MCP's
read-only tool annotations describe intent; the database permissions enforce it.
The existing SQL text validation is a guardrail, not a full parser. Metadata tools
retain their existing behavior; the analytical timeout/row cap belongs to
`execute_sql`. A row cap does not limit the byte size of an individual cell.

Tool failures use `CallToolResult.isError=true`. Query rejection, SQL syntax and
timeout errors retain the existing concise observations. Unknown tables have a
safe missing-table message; configuration, connection, and unexpected failures
use fixed messages. Validation errors do not echo supplied values, and output
uses the existing centralized secret/connection-URL redaction. The dedicated
stdio process disables logging to prevent SDK protocol diagnostics from echoing
malformed input; stdout carries only MCP messages during server operation.
No exception text, traceback, arbitrary file access, or shell tool is exposed.

## Local stdio usage

From the repository root, in the existing environment:

```powershell
conda activate llm
python -m pip install -r requirements.txt
python -m app.mcp_server --help
```

The launch command is:

```powershell
python -m app.mcp_server
```

It waits for MCP protocol input, not interactive questions. Usually the MCP client
launches it and manages its lifetime. Do not start an unattended persistent server.
There is no listening HTTP port or public MCP endpoint in this phase.

### Generic client configuration template

```json
{
  "mcpServers": {
    "agentic-data-analyst": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "${PROJECT_ROOT}",
      "env": {
        "DATABASE_SALES_URL": "${DATABASE_SALES_URL}",
        "DATABASE_SAAS_URL": "${DATABASE_SAAS_URL}"
      }
    }
  }
}
```

This is a template, not universal client syntax. Configure the working directory
as the repository root and the executable as the Python interpreter from `llm`.
Environment placeholder expansion and `cwd` support depend on the client; the
server does not expand literal `${...}` strings. Use the client's private settings
or environment inheritance, never commit populated credentials. If the client
cannot set a working directory, launch it from the repository root or use its
documented equivalent. Local fallback can use the existing private `.env` instead
of hosted URLs. Gemini configuration is unnecessary for this interface.

For the Inspector demo, prefer launching from the repository root and keeping
runtime URLs in the existing private environment/`.env`, rather than copying them
into an Inspector catalog or passing `-e KEY=value` on the command line. The
template above is illustrative: do not give a client literal unexpanded URL
placeholders. The [Phase 5.2 walkthrough](mcp-demo.md) documents Inspector 2.9.0,
including its CLI `--` separator for Python's `-m` argument and manual Neon steps.

## Offline verification

```powershell
python -m pytest -q tests/test_mcp_server.py -p no:cacheprovider
```

Tests use SDK in-memory sessions, fake connections, and a real stdio subprocess
that initializes, lists tools/profiles, checks safe argument errors, then closes.
They verify profile isolation, existing schemas/tool reuse, write rejection,
READ ONLY/timeout/row-cap behavior, resource closure, and secret handling.
No Gemini or live database calls are made. Existing full-suite PostgreSQL tests
must be deselected when database access is forbidden; that is not live permission
verification.

## Verified Phase 5.2 results

Phase 5.2 is complete based on the user's successful manual verification with
MCP Inspector 2.9.0 through the real local stdio client:

- The server connected and exposed exactly `list_database_profiles`, `get_schema`,
  `describe_table`, and `execute_sql`; profile listing returned `sales` and `saas`.
- `get_schema` succeeded for both datasets; `describe_table` succeeded for Sales
  `orders` and SaaS `subscriptions`.
- Both profiles reported `current_user = analyst_agent` and
  `transaction_read_only = on` through `execute_sql`.
- `SELECT COUNT(*) AS sales_rows FROM sales;` returned **300** for Sales.
- `SELECT COUNT(*) AS subscription_rows FROM subscriptions;` returned **120** for SaaS.
- `DELETE FROM sales;` was rejected through MCP; no write occurred.

No Gemini was involved. MCP remains local stdio only, and production deployment
architecture is unchanged. This documentation closure records the user's report;
Codex did not repeat the client/database checks. See [mcp-demo.md](mcp-demo.md)
for the reproducible walkthrough and verification summary.

## Limits and next phase

- Local stdio only; no public hosting, HTTP MCP, authentication, or OAuth.
- Exactly four MCP tools; calculator, shell, files, and write tools are not exposed.
- No agent loop, sampling/model invocation, automatic retries, or additional tracing.
- SDK subprocess interoperability and Inspector 2.9.0 CLI initialization,
  tool discovery, and profile listing are verified without database calls.
  The user also completed real-client/live read-only Sales and SaaS verification.
- Render/Vercel deployment and Docker startup configuration remain unchanged;
  the image is not rebuilt as part of this phase.

Phase 5.2 interoperability/demo is complete. The next phase requires an explicit
user request; external model calls or production changes require separate authorization.
