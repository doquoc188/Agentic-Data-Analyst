# MCP interoperability demo

## What this proves

An independent MCP-compatible client can discover the analytical tools and choose
Sales or SaaS explicitly. The client sends tool arguments, receives MCP results,
and uses the same read-only data layer as the web application. Gemini is not
required: this demo invokes tools directly, without an LLM.

```text
MCP-compatible client
        | stdio
        v
app.mcp_server
        +--> profile routing
        +--> existing safe LangChain tools
        v
Neon PostgreSQL: Sales / SaaS

Web: Vercel -> FastAPI -> manual Agent -> same safe data layer
```

The web Agent does not communicate through MCP. The MCP server remains local;
the Inspector's temporary local browser interface is not a public MCP deployment.

## Verification status

- **Automated:** the existing 16 MCP tests cover SDK sessions, stdio startup and
  shutdown, schemas, adapters, profile isolation, and mocked SQL safety.
- **Third-party CLI:** MCP Inspector 2.9.0 initialized the actual stdio server
  with protocol `2025-11-25`, listed all four tools, and called
  `list_database_profiles`. These discovery operations require no database.
- **Manual verification complete:** the user successfully verified real stdio
  MCP interoperability with Inspector 2.9.0, including both live datasets,
  metadata, runtime role/READ ONLY checks, counts, and write rejection.

CLI discovery alone does not establish live database connectivity; the user's
manual checks provide that evidence. This closure records the user's report and
does not repeat live calls. Example response renderings below remain illustrative,
not exported transcripts or screenshots.

## Verified results

| User-verified check | Result |
| --- | --- |
| Real stdio server connection | Successful with MCP Inspector 2.9.0 |
| Exposed tools | Exactly list_database_profiles, get_schema, describe_table, execute_sql |
| Profile listing | sales and saas |
| get_schema | Succeeded for both Sales and SaaS |
| describe_table | Succeeded for Sales orders and SaaS subscriptions |
| Both profiles: current_user | analyst_agent |
| Both profiles: transaction_read_only | on |
| Sales: SELECT COUNT(*) AS sales_rows FROM sales; | 300 |
| SaaS: SELECT COUNT(*) AS subscription_rows FROM subscriptions; | 120 |
| DELETE FROM sales; through MCP | Rejected; no write occurred |

No Gemini was involved. MCP remains local stdio only, with no production
deployment architecture change. **Phase 5.2 is complete.**

## Setup

1. Open PowerShell in the repository root and activate the existing environment.
2. Use Node.js 24 LTS (Inspector 2.9.0 requires Node >=22.19.0) with `npx` available.
3. For the manual Neon steps, privately configure both `DATABASE_SALES_URL` and
   `DATABASE_SAAS_URL` in the existing local `.env` or process environment. Both
   must use the restricted **analyst_agent** login and retain required SSL options.
   Verify the login privately before connecting; never use owner/admin URLs.
4. Check that both hosted variables are populated. Missing hosted variables
   select the existing local fallback, which would not prove Neon connectivity.

```powershell
conda activate llm
python -m app.mcp_server --help
node --version
```

If `node`/`npx` are missing from PATH, use your installed Node 24 runtime. For
this checkout's existing optional portable runtime, the session-only setup is:

```powershell
$env:Path = (Resolve-Path '.tools/node-v24.21.0-win-x64').Path + ';' + $env:Path
```

That Git-ignored folder is a local convenience, not included in a fresh clone.

Existing requirements must already be installed. Do not change working cloud
settings or primary local `DB_NAME` for this demo. Never pass credentials through
tool arguments, command-line `-e` flags, screenshots, or committed client files.

## Start the client and discover tools

Use the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector).
The version is explicit so these instructions use one known CLI syntax.

From the repository root, the following commands initialize separate sessions,
perform one operation, and disconnect. They do not query either database:

```powershell
npx -y @modelcontextprotocol/inspector@2.9.0 --cli python -m app.mcp_server -- --method initialize --format json
npx -y @modelcontextprotocol/inspector@2.9.0 --cli python -m app.mcp_server -- --method tools/list --format json
npx -y @modelcontextprotocol/inspector@2.9.0 --cli python -m app.mcp_server -- --method tools/call --tool-name list_database_profiles --format json
```

In Inspector 2.x CLI mode, the `--` separator keeps `python -m app.mcp_server`
together as the server command; Inspector options follow it. These flags are
documented in the [official CLI/configuration guide](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md).
The first `npx` launch may download the client; it is not a project dependency.

Expect server name `agentic-data-analyst`, version `5.1`, and exactly these tools:

| Tool | Required fields |
| --- | --- |
| `list_database_profiles` | None |
| `get_schema` | `profile` |
| `describe_table` | `profile`, `table_name` |
| `execute_sql` | `profile`, `query` |

`profile` accepts only `sales` or `saas`; extra tool fields are rejected. Profile
listing contains IDs, names, and descriptions, never connection settings.

### Manual browser session

```powershell
npx -y @modelcontextprotocol/inspector@2.9.0 python -m app.mcp_server
```

Open the local URL printed by Inspector. Keep its session token private. Select
the stdio target with command `python` and arguments `-m app.mcp_server`, then
connect if it is not already connected. The process must run from the repository
root using the activated `llm` interpreter. Confirm the connected server name
and inspect the `initialize` response in protocol history, then open **Tools**
and list them. Select each tool, supply the following fields through its form
or JSON input, and invoke it. No natural-language Agent query is needed.

## Manual Sales and SaaS checks

Perform these only after confirming both private URLs are restricted runtime
URLs. Successful tool calls should have `isError: false`.

| Tool | Arguments | Expected evidence |
| --- | --- | --- |
| `list_database_profiles` | `{}` | `sales` and `saas`, with safe display metadata |
| `get_schema` | `{"profile":"sales"}` | Public customers, products, orders, order_items, sales and their columns |
| `get_schema` | `{"profile":"saas"}` | Public accounts, plans, subscriptions, invoices, support_tickets and their columns |
| `describe_table` | `{"profile":"sales","table_name":"orders"}` | Columns, order_id PK, customer_id FK to customers |
| `describe_table` | `{"profile":"saas","table_name":"subscriptions"}` | Columns, PostgreSQL comments, PK/FK metadata |

Use `execute_sql` with each profile in turn to verify the runtime role and the
transaction before the count checks:

```sql
SELECT current_user AS runtime_role,
       current_setting('transaction_read_only') AS transaction_read_only;
```

Expect `analyst_agent | on`. Stop if the role or transaction differs; do not
continue the demonstration or change permissions to make it work.

**Sales** — select `execute_sql`, supply:

```json
{"profile":"sales","query":"SELECT COUNT(*) AS sales_rows FROM sales;"}
```

Expected synthetic fixture count: **300**. Conceptual MCP result:

```json
{
  "content": [{"type": "text", "text": "COLUMNS:\nsales_rows\n\nROWS:\n300\n\nRows returned: 1"}],
  "isError": false
}
```

**SaaS** — select `execute_sql`, supply:

```json
{"profile":"saas","query":"SELECT COUNT(*) AS subscription_rows FROM subscriptions;"}
```

Expect column `subscription_rows`, value **120**, and `Rows returned: 1`.
Counts are fixture expectations; a mismatch needs investigation, not data changes.

The client receives a structured MCP `CallToolResult` envelope with typed text
content and an error flag. The existing SQL table and profile JSON are rendered
inside text; the server does **not** advertise typed row `structuredContent`.

## Manual write-rejection demonstration

With the verified `sales` runtime profile, call `execute_sql` once with:

```json
{"profile":"sales","query":"DELETE FROM sales;"}
```

Expected MCP result:

```json
{
  "content": [{"type": "text", "text": "Query rejected: only SELECT or WITH queries are allowed."}],
  "isError": true
}
```

The existing executor validates SQL before opening a connection, so this statement
is never submitted to PostgreSQL. Repeat the harmless Sales count afterward;
expect 300. Do not attempt a bypass or use administrative credentials.

Protection is layered: SQL validation rejects this request; accepted analytical
queries run in READ ONLY transactions; analyst_agent's SELECT-only grants are
the final permission boundary. This rejection demo alone does not independently
retest PostgreSQL privileges; the mocked tests prove the adapter preserves the
existing executor, and role provisioning/verification remains separate.

## Evidence to capture and shutdown

Capture the date, Inspector/Node/Python versions, initialize response, four-tool
listing, safe profiles, both schemas, metadata, role/READ ONLY results, counts,
and `isError: true` rejection. Crop screenshots to tool arguments/results and
protocol responses; exclude environment panels, connection URLs, browser session
tokens, and terminal startup links. Record these as **manual verification** only
after actually performing them. Keep sensitive raw exports local.

Disconnect the Inspector session and press Ctrl+C in its launch terminal. Confirm
the terminal prompt returns; the client owns and closes the server subprocess.

## Limits and next step

This is direct tool interoperability, not an autonomous client Agent or accuracy
benchmark. The existing eight-response web loop, API/frontend, SQL boundary,
database schemas, and deployment remain unchanged. There is no Gemini invocation,
remote MCP hosting, OAuth, filesystem/shell tool, or credential-retrieval tool.

Phase 5.2 is closed following the user's successful manual results recorded above.
Further work requires an explicitly requested phase; this closure changes documentation only.
