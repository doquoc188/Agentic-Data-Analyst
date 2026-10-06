# v1.0.0 release checklist

**Release candidate: prepared for review, not committed, tagged, or published.**

Historical verification below comes from the maintainer's production report and
completed Phase 5.2 Inspector checks. Release preparation makes no fresh Gemini,
database, or deployed-service calls. Final manual checks remain the maintainer's
decision; submitting a live question consumes model quota.

| Check | Status | Evidence / final action |
| --- | --- | --- |
| Production frontend reachable | Verified historically; requires final manual check | [Live demo](https://agentic-data-analyst-nine.vercel.app) |
| Backend /health | Verified historically; requires final manual check | [Health](https://agentic-data-analyst-api.onrender.com/health): process liveness |
| Backend /ready | Verified historically; requires final manual check | [Readiness](https://agentic-data-analyst-api.onrender.com/ready): configuration only |
| Sales live query | Verified historically; requires final manual check | Maintainer verified production Sales end-to-end |
| SaaS live query | Verified historically; requires final manual check | Maintainer verified production SaaS end-to-end |
| Git working tree clean | Requires final manual check | Review this diff, then commit only approved files; do not tag yet |
| Python tests green | Verified historically; requires final manual check | Fresh offline result recorded below; live DB tests intentionally excluded |
| Frontend build green | Verified historically; requires final manual check | Fresh npm ci/build result recorded below |
| No secrets tracked | Requires final manual check | Current tracked-file audit below; review new docs and staged diff before commit |
| README links valid | Requires final manual check | Local paths/anchors checked offline; external link reachability not retested |
| MCP docs valid | Verified historically | Inspector 2.9.0: four tools, both schemas/descriptions, analyst_agent/on, 300/120 counts, rejected DELETE |
| Dockerfile present | Verified historically | File and strict build context reviewed; image/runtime verification remains pending and optional |
| Deployment docs current | Requires final manual check | Vercel + source-based Render + two Neon databases; no redeployment performed |
| Benchmarks unchanged | Verified historically | Sales 22/24 = 91.67%; SaaS unseen 14/16 = 87.50%; cases/results unchanged |

## Fresh offline release checks

Completed on October 6, 2026 in the existing Conda `llm` environment and Node
24.21.0. These checks do not substitute for current production availability or
database permission checks.

| Offline check | Result |
| --- | --- |
| Python pytest selection below | 158 passed, 19 live DB tests deselected, 186 subtests passed; one existing Starlette/HTTPX deprecation warning |
| Frontend npm ci | Passed; lockfile unchanged |
| Frontend npm run build | Passed: TypeScript check and Vite production build |
| Current tracked-file secret audit and new release docs | No suspected real secrets found; explicit fake redaction fixtures and placeholders retained; Git history not audited |
| Git hygiene | .env, runs/, database .dump/.backup exports, frontend env/build output, and portable tools are ignored |
| Local Markdown links | Paths and heading anchors resolve; external URLs not contacted |
| git diff --check | Passed |
| Protected application/configuration/evaluation files | Unchanged; documentation only |

Use Conda `llm`. Plain pytest includes live PostgreSQL tests in this repository;
for a release audit that forbids database access, use the explicit offline selection:

```powershell
conda activate llm
$env:RUN_GENERALIZATION_DB_TESTS='0'
python -m pytest -q tests -p no:cacheprovider -k 'not SchemaTests and not GeneralizationDatabaseTests and not test_reference_results_match_postgresql and not (DescribeTableTests and not test_composite_primary_key_keeps_database_column_order) and not (ExecuteSqlTests and not test_empty_query)'
cd frontend
npm ci
npm run build
cd ..
git diff --check
```

The excluded classes include live Sales and optional SaaS integration checks;
the selection retains mocked agent/API/MCP/evaluator/trace tests. Restore any prior
RUN_GENERALIZATION_DB_TESTS setting after the commands if appropriate.

## Final manual gates

1. Review the documentation diff and staged secret hygiene. No release commit,
   tag, or push is authorized by this preparation task.
2. Choose whether to refresh the historical frontend/health/ready and Sales/SaaS
   checks, then record actual results before announcing current availability.
3. Commit the approved release documentation and confirm a clean working tree.
4. Create v1.0.0 and publish only after explicit maintainer approval.

Optional screenshot capture is described in [portfolio.md](portfolio.md).
Pending Docker runtime checks do not block this source-deployed web release;
do not advertise a verified container runtime.
