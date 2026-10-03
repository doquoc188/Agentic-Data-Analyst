# Public deployment preparation (Phase 4.4A)

Prepared October 3, 2026. **No deployment or cloud migration has been executed.**
Selected stack: Vercel Hobby (static frontend), Render Free Web Service (FastAPI),
two Neon Free PostgreSQL projects, GitHub, and the existing Gemini Developer API.
The target is $0/month **within free-tier limits**, not an unconditional cost
guarantee. Confirm dashboard plan/usage limits before provisioning. No paid
compute, Render database, keep-alive pings, or cloud trace storage is configured.

## 1. Versions and unchanged application

- Render Python: `.python-version` pins **3.10.20**, matching the tested Conda
  `llm` environment and installed dependencies. Do not set a conflicting
  `PYTHON_VERSION` in Render; it takes precedence over the file.
- Vercel Node: select **24.x**, matching the local frontend's Node 24 major.
- Gemini model remains **`gemini-3.5-flash-lite`**, temperature 0.
- `requirements.txt` remains unpinned; inspect the eventual Render build log
  for resolved dependencies. Passing local tests is not a hosted build check.
- Agent loop, tools, SQL protections, local tracing, and profiles are unchanged.

## 2. Create two Neon projects manually

Create Free projects `agentic-analyst-sales` and `agentic-analyst-saas`, selecting
**PostgreSQL 18** to match the local source. Use one dedicated database per
project; the default name `neondb` is fine. The app uses the database in each
hosted URL, so hosted names need not match the local names.

Record each project's database name, owner role, and **direct/unpooled** host
privately. Use direct connections for these tiny demos and migration commands;
no connection pool is needed. Keep the owner's credentials only for migration,
never in Render or the frontend. Do not restore into a database with unrelated
objects. Source databases are `agentic_analyst` and `agentic_analyst_saas`.

## 3. Export local public schemas manually

These commands create archives only when **you** run them. Use an authorized
local owner account and enter its password at each prompt. No password is placed
in shell history, an argument, or this repository. Do not enable shell transcript
capture during credential-sensitive deployment steps.

```powershell
cd "D:\ReAct Agent"
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$dumpDir = Join-Path (Get-Location) '.deployment-tmp'
New-Item -ItemType Directory -Force -Path $dumpDir | Out-Null

& "$pgBin\pg_dump.exe" --format=custom --schema=public --no-owner --no-acl `
    --host=localhost --port=5432 --username=postgres --password `
    --dbname=agentic_analyst --file="$dumpDir\sales.dump"
if ($LASTEXITCODE -ne 0) { throw 'Sales export failed; stop.' }

& "$pgBin\pg_dump.exe" --format=custom --schema=public --no-owner --no-acl `
    --host=localhost --port=5432 --username=postgres --password `
    --dbname=agentic_analyst_saas --file="$dumpDir\saas.dump"
if ($LASTEXITCODE -ne 0) { throw 'SaaS export failed; stop.' }
```

The custom archives preserve public tables, rows, sequence values, indexes,
PK/FK constraints, and table/column comments. Ownership is reassigned during
restore and local ACLs are omitted. Cluster roles/passwords are not exported;
do not use `pg_dumpall`. Both archives and `.deployment-tmp/` are Git ignored.
These are still database exports: keep them private and delete them manually
when no longer needed. The original `sales` creation is not scripted, so export
the working database rather than attempting to recreate it from migrations.

## 4. Restore and provision each Neon runtime role manually

Run this block **twice**, once per profile, using the matching Neon project.
Do not paste a connection URL into the host prompt. Owner and runtime passwords
are entered directly into PostgreSQL tool prompts; never put them in `.env` or
commands. `PGSSLMODE` and `PGCHANNELBINDING` temporarily enforce transport options
and are restored afterward. Retain the Neon connection dialog's SSL options in
the final application URLs as well.

```powershell
# Continue in the same terminal, with $pgBin and $dumpDir from section 3.
$profile = Read-Host 'Profile to restore (sales or saas)'
if ($profile -notin @('sales', 'saas')) { throw 'Invalid profile; stop.' }
$neonHost = Read-Host 'Matching Neon direct host (hostname only)'
$neonDatabase = Read-Host 'Target database name (usually neondb)'
$neonOwner = Read-Host 'Target owner role (usually neondb_owner)'
$previousSslMode = $env:PGSSLMODE
$previousChannelBinding = $env:PGCHANNELBINDING
try {
    $env:PGSSLMODE = 'require'
    $env:PGCHANNELBINDING = 'require'
    $ownerArgs = @('-h', $neonHost, '-p', '5432', '-U', $neonOwner, '-d', $neonDatabase, '-W')

    # Refuse a populated target. This check does not drop or alter objects.
    $emptyCheck = @'
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f'))
       OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                  WHERE n.nspname = 'public') THEN
        RAISE EXCEPTION 'Target public schema is not empty; stop and inspect.';
    END IF;
END $$;
'@
    & "$pgBin\psql.exe" -X -v ON_ERROR_STOP=1 @ownerArgs -c $emptyCheck
    if ($LASTEXITCODE -ne 0) { throw 'Target check failed; stop.' }

    & "$pgBin\pg_restore.exe" --host=$neonHost --port=5432 --username=$neonOwner `
        --dbname=$neonDatabase --password --schema=public --no-owner --no-acl `
        --exit-on-error --single-transaction "$dumpDir\$profile.dump"
    if ($LASTEXITCODE -ne 0) { throw 'Restore failed; stop before role setup.' }

    & "$pgBin\psql.exe" -X -v ON_ERROR_STOP=1 @ownerArgs `
        -f sql/deployment/01_runtime_role.sql
    if ($LASTEXITCODE -ne 0) { throw 'Role setup failed; inspect manually.' }

    # Interactive owner session. At its psql prompt, type:
    # \password analyst_agent
    # Enter a NEW runtime password twice, then type \q.
    & "$pgBin\psql.exe" -X @ownerArgs
    if ($LASTEXITCODE -ne 0) { throw 'Password-setting session failed; stop.' }

    $runtimeArgs = @('-h', $neonHost, '-p', '5432', '-U', 'analyst_agent', '-d', $neonDatabase, '-W')
    & "$pgBin\psql.exe" -X -v ON_ERROR_STOP=1 @runtimeArgs -v "profile=$profile" `
        -f sql/deployment/02_verify_metadata.sql
    if ($LASTEXITCODE -ne 0) { throw 'Metadata verification failed; stop.' }
    & "$pgBin\psql.exe" -X -v ON_ERROR_STOP=1 @runtimeArgs `
        -f sql/deployment/03_verify_read_only_role.sql
    if ($LASTEXITCODE -ne 0) { throw 'Read-only verification failed; stop.' }
} finally {
    $env:PGSSLMODE = $previousSslMode
    $env:PGCHANNELBINDING = $previousChannelBinding
}
```

Restore has no `--clean` or `--create`: errors abort its single transaction.
Retry only after understanding the error; never silently overwrite a target.
Role creation is a separate transaction and refuses an existing `analyst_agent`.
If a later step fails after restore, inspect the existing state and resume the
appropriate step manually, rather than repeating the whole restore block.

**Create the runtime role with SQL, not Neon's Add Role button/API/CLI.** Those
interfaces grant `neon_superuser` membership. The supplied SQL creates a fresh
LOGIN role without memberships, ownership, superuser, create-role/create-database,
replication, or bypass-RLS powers. It revokes PUBLIC database/schema/table/sequence
privileges in this **dedicated** database, then grants only database CONNECT,
public USAGE, and table SELECT. It grants no sequence access or grant options.
Default table privileges apply only to the owner executing the script; repeat
that policy deliberately if a different owner creates future tables.

Never add admin memberships to this role. Keep both Neon owner passwords private
and separate from the two runtime passwords. Neither the role script nor the
application stores a runtime password in source.

## 5. Review verification results before publishing

`02_verify_metadata.sql` runs READ ONLY as the runtime role and asserts these
fixture counts; it prints PK/FK definitions, indexes, and column comments.

| Sales table | Rows | SaaS table | Rows |
| --- | ---: | --- | ---: |
| sales | 300 | accounts | 60 |
| customers | 20 | plans | 4 |
| products | 12 | subscriptions | 120 |
| orders | 300 | invoices | 360 |
| order_items | 300 | support_tickets | 240 |

Check all normalized PKs and these FKs in the printed output:

- Sales: `orders.customer_id → customers.customer_id`;
  `order_items.order_id → orders.order_id`;
  `order_items.product_id → products.product_id`.
- SaaS: `subscriptions.account_id → accounts.account_id`;
  `subscriptions.plan_id → plans.plan_id`;
  `invoices.subscription_id → subscriptions.subscription_id`;
  `support_tickets.account_id → accounts.account_id`.

Compare comments with `sql/03_discount_semantics.sql` (both discount columns)
and `sql/generalization/01_create_schema.sql` (seven SaaS business comments).
Compare the index/constraint definitions with the corresponding local source
reports if there are any differences; do not assume restore succeeded just
because row counts match. Counts reflect the current fixture; investigate any
intentional dataset changes instead of weakening assertions automatically.

`03_verify_read_only_role.sql` must connect **directly as analyst_agent**.
It checks flags, memberships, ownership, CONNECT/USAGE, SELECT, grant options,
and forbidden write/CREATE/TEMP privileges. It attempts INSERT, UPDATE, DELETE,
TRUNCATE, CREATE TABLE, and CREATE TEMP TABLE in a READ WRITE transaction so
failures demonstrate role permissions rather than an unrelated READ ONLY rule.
Each denial is caught in a subtransaction. Unexpected success raises an error
and rolls back; the outer transaction also ends with ROLLBACK. No changes are
retained. Run this only as the runtime role on the dedicated demo database.

## 6. Configure restricted connection URLs

Use each project's connection dialog to select `analyst_agent`, the correct
database and direct host. Supply the password set with `\password` privately;
retain Neon's SSL/channel-binding query options. URL-encode password special
characters if constructing a URL manually. Only a placeholder belongs in docs:

```text
postgresql://analyst_agent:<URL-encoded-runtime-password>@<direct-neon-host>:5432/<database>?sslmode=require&channel_binding=require
```

Paste the sales URL into Render's `DATABASE_SALES_URL` and the SaaS URL into
`DATABASE_SAAS_URL`, never GitHub, frontend environment variables, or logs. Verify
the URL role is `analyst_agent`; never substitute the owner to bypass a failure.
Keep the local `.env` unchanged. No `DB_*` fallback values are needed on Render
when both hosted URLs are configured.

## 7. Render backend — manual dashboard deployment

After Neon verification, commit/review only intended files and push to GitHub
without dumps, traces, secrets, or generated builds. Connect that repository in
Render and import root `render.yaml` as a Blueprint. Review that it creates
**one Python Web Service on Free**, with no Render database or disk:

| Setting | Value |
| --- | --- |
| Root directory | Repository root (leave blank) |
| Build | `pip install -r requirements.txt` |
| Start | `uvicorn app.api:app --host 0.0.0.0 --port $PORT` |
| Python | `3.10.20` from `.python-version` |
| Health check | `/health` |
| Automatic deploy trigger | Off; subsequent deploys are manual |

Provide `GOOGLE_API_KEY` and the two **restricted** hosted URLs at the dashboard
prompts. Set initial `ALLOWED_ORIGINS` empty if permitted; otherwise use only
`http://localhost:5173` temporarily. Replace it with the exact Vercel production
origin after frontend deployment. `sync: false` prevents values being committed;
new keys added to an existing Blueprint must be entered manually in Render.

`TRACE_ENABLED=false` is fixed in the Blueprint. Render Free storage is
ephemeral; API answers still have run IDs but `trace_path` is null. Local/eval
tracing remains enabled by its normal configuration. Do not add a persistent
disk or rely on `runs/` surviving production restarts. `/health` is liveness;
`/ready` checks configuration only, not DB connectivity or Gemini quotas.

Deploy manually and record its HTTPS backend origin privately for frontend
configuration. Render supplies `PORT`; do not use PowerShell `$env:PORT` in
the Linux start command. Free-service sleep is expected: leave the existing
generic slow-server hint, and do not add artificial keep-alive traffic.

## 8. Vercel frontend and exact CORS order

1. Deploy the Render backend initially and obtain its HTTPS origin.
2. Import the same GitHub repository into Vercel on Hobby. Set **Root Directory
   `frontend`**, **Framework Vite**, **Node 24.x**, **Install `npm ci`**,
   **Build `npm run build`**, **Output `dist`**.
3. Set production `VITE_API_BASE_URL` to the Render backend origin, then deploy.
   Vite embeds this public URL at build time; changes require a fresh build.
4. Obtain the final Vercel production origin (`https://<project>.vercel.app`,
   without a path or trailing slash).
5. Set Render `ALLOWED_ORIGINS` to that **exact** origin. For multiple approved
   origins, use an explicit comma-separated list; never use `*` or a wildcard
   preview-domain pattern. Preview origins are not automatically trusted.
6. Manually redeploy/restart the backend so its CORS configuration is refreshed.
7. Test from the production frontend in the browser, including CORS preflight.

The frontend has one page with hash anchors and no React Router paths.
No `vercel.json`, route fallback, serverless functions, or router is needed.
Only `VITE_API_BASE_URL` belongs in Vercel's frontend environment. Do not import
backend env files or expose Gemini keys, hosted DB URLs, or owner credentials.

## 9. Manual smoke test and release checklist

- [ ] Both Neon metadata and role scripts pass under `analyst_agent`; comments
      and required PK/FKs/indexes are reviewed.
- [ ] Render is Free, uses pinned Python, restricted URLs, exact origins, and
      disabled production file traces; logs contain no credential URLs.
- [ ] `GET /health` returns 200, `/ready` returns 200, and `/databases` contains
      only public sales/saas display metadata. These checks make no Gemini calls.
- [ ] Vercel production is built with the correct HTTPS API URL; inspect the
      bundle/env settings for server credentials before sharing the demo.
- [ ] Open the public frontend: both datasets load; light/dark/mobile UI works.
- [ ] **When you choose to run live checks**, submit one sales question and one
      SaaS question. These POST requests send synthetic query results to Gemini
      and consume quota. Check real answers and successful status/run IDs.
- [ ] Verify exact-origin CORS, friendly error/retry behavior, and the cold-start
      hint. No server trace paths, raw errors, or fake execution data appear.
- [ ] Record actual hosting URLs and live smoke-test results privately. Share
      the public frontend only after the checks pass; do not publish owner URLs.

Preparation checks (no Gemini calls):

```powershell
conda activate llm
$env:RUN_GENERALIZATION_DB_TESTS = '1'
python -m unittest discover -s tests -v
cd frontend
npm run test
npm run build
```

Provider provisioning, restore, privilege probes, and live public smoke tests
are pending manual actions. No rate limiter/authentication/streaming or provider
retry policy is added in this phase.

## Official references

- [Render Blueprint fields](https://render.com/docs/blueprint-spec),
  [Python versions](https://render.com/docs/python-version),
  [Free service lifecycle](https://render.com/docs/free).
- [Vercel Vite](https://vercel.com/docs/frameworks/frontend/vite),
  [Node versions](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions).
- [Neon role security](https://neon.com/docs/manage/roles),
  [Neon migration tooling](https://neon.com/tools).
- [PostgreSQL 18 pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html),
  [pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html).
