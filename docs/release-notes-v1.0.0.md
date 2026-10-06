# v1.0.0 release notes

**Status: release candidate prepared for maintainer review; not tagged or released.**

Agentic Data Analyst answers natural-language analytical questions using real
PostgreSQL query results. Its manual LangChain tool-calling loop keeps schema
discovery, tool execution, observations, correction, and stopping visible.

## Included capabilities

- Gemini chooses among calculator, get_schema, describe_table, and execute_sql;
  the loop allows at most eight model responses and can correct SQL or tool arguments.
- Dynamic public-table discovery, PK/FK inspection, and semantic PostgreSQL comments
  provide schema and business-unit grounding without fixed query templates.
- Read-only analytical execution combines SQL guardrails, READ ONLY transactions,
  a restricted analyst_agent role, a 5,000 ms timeout, and a 100-row result cap.
  Credentials stay in server configuration; SQL text checks are not the security boundary.
- Deterministic evaluation uses PostgreSQL-verified reference results and explicit
  comparison contracts. Reference answers remain outside the agent's input.
- The same core agent and tools work across synthetic Sales and SaaS fixtures.
- Optional local structured traces include timing, safe failures, bounded result
  previews, and redaction. Production file persistence is disabled.
- The maintainer verified the deployed React/Vite frontend on Vercel, FastAPI
  backend on Render, and separate Neon Sales/SaaS databases end-to-end.
- A backend Dockerfile supplies optional packaging and portability. Render deploys
  Python from source; Docker image/runtime verification remains pending.
- A separate local stdio MCP server exposes list_database_profiles, get_schema,
  describe_table, and execute_sql over the same safe data layer. MCP Inspector
  2.9.0 interoperability was user-verified for both datasets, including metadata,
  analyst_agent, transaction_read_only=on, counts of 300 sales rows and 120
  subscriptions, and DELETE rejection with no write. No Gemini was involved.

The web agent does not use MCP internally. There is no public MCP endpoint.

## Official recorded benchmarks

| Suite | Passed | Pass rate |
| --- | ---: | ---: |
| Sales | 22/24 | 91.67% |
| SaaS unseen | 14/16 | 87.50% |

These live baselines are unchanged. Offline rescoring is not a replacement live
score, and two synthetic fixtures do not establish universal schema accuracy.
No benchmark, model call, or database query was run during release preparation.

## Known limitations

- LLM behavior can vary even at temperature 0; provider rate limits, quota, and
  integration failures may interrupt requests. No automatic provider retries exist.
- The agent may exhaust its response budget before finalizing. Evaluation query
  selection and result-representation contracts also have known limitations.
- Only predefined synthetic datasets are available; arbitrary database connections,
  write operations, authentication, sessions, and streaming are unsupported.
- MCP is local stdio only. A separate MCP client's model usage is outside this demo.
- Readiness checks configuration, not live connectivity or provider quota.
  Free hosting may cold start, and production has no durable trace storage.
- Docker build/container checks remain unverified from the unavailable Linux engine;
  the working source deployment does not establish container readiness.
- The $0/month infrastructure target depends on provider allowances and usage;
  it is not an audited billing guarantee.

See the [release checklist](release-checklist.md) for final manual gates and
[portfolio guide](portfolio.md) for real evidence capture. Release preparation
changes documentation only; application behavior and deployment remain unchanged.
