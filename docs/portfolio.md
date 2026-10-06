# Portfolio evidence

Five real screenshots supplied by the maintainer are included unchanged under
`docs/assets/` and displayed in the [README](../README.md#demo). Visual review
found no visible credentials, connection URLs, or Inspector session tokens.
The [web walkthrough](demo.md) and [verified MCP walkthrough](mcp-demo.md) provide
the demonstration steps. Fresh web submissions call Gemini; capture them only
when the maintainer chooses to run live checks.

## Included captures

| File | Visible evidence |
| --- | --- |
| [web-overview.png](assets/web-overview.png) | Production interface with Sales Analytics selected |
| [sales-query.png](assets/sales-query.png) | Completed Sales answer for revenue by city |
| [saas-query.png](assets/saas-query.png) | Completed SaaS answer for current MRR by plan |
| [mcp-tools.png](assets/mcp-tools.png) | Connected Inspector, four tools, and sales/saas choices |
| [mcp-readonly-security.png](assets/mcp-readonly-security.png) | Query result showing analyst_agent and transaction_read_only=on |

The security capture does not show the selected profile or DELETE rejection;
do not caption it as evidence of both profiles or write denial. The user-verified
write rejection remains documented in [the MCP walkthrough](mcp-demo.md#verified-results).

## Recommended screenshot set

| Screenshot | What it demonstrates | Recommended caption | Exclude from the image |
| --- | --- | --- | --- |
| Main web UI, Sales Analytics selected | Dataset selection, question entry, examples, and the public interface | “Ask natural-language questions over the synthetic Sales dataset.” | Developer consoles, environment panels, private browser tabs, personal account details |
| Successful Sales answer | A real answer grounded in read-only PostgreSQL results | “Sales analytics answered through the manual LangChain tool-calling agent.” | Raw traces, server trace paths, credentials, connection URLs, hidden reasoning |
| Successful SaaS answer | The same agent working with the second relational schema | “The same agent discovers and queries the synthetic SaaS schema.” | Raw traces, server trace paths, credentials, connection URLs, hidden reasoning |
| MCP Inspector tool list | Independent stdio interoperability and exactly four exposed tools | “MCP Inspector 2.9.0 discovers four local read-only tools.” | Inspector session tokens, startup URLs, environment/connection panels, terminal command history |
| MCP read-only verification and write rejection | Both profiles report analyst_agent/on; DELETE is rejected | “Both profiles use analyst_agent in READ ONLY transactions; the write request is rejected before execution.” | Database URLs/passwords, tokens, environment panels, private host details; crop to safe tool arguments/results |

For the final screenshot, use a small composite of actual role/transaction results
and the rejection if needed. Label each profile clearly. Do not imply that a
rejected request independently proves all database privileges: role permissions
are a separate protection, and this DELETE request never reaches PostgreSQL.

## Evidence labels

- Record capture date, selected dataset, and the actual question/result.
- Show complete enough output to support the caption; do not invent or edit an
  answer, tool list, count, or success indicator.
- Do not present illustrative JSON from documentation as a captured client result.
- The UI displays an answer and safe run details, not internal SQL/tool traces.
- Keep official scores **Sales 22/24 (91.67%)** and **SaaS unseen 14/16 (87.50%)**
  separate from individual successful screenshots.
- The web path uses FastAPI and the manual LangChain agent. MCP is a separate
  local stdio interface over the same safe data layer, with no Gemini requirement.

Review every image before publishing. Keep raw screenshots or exports containing
private information outside the repository; screenshot collection is optional
for future presentation updates.
