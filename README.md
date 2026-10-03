# Agentic Data Analyst

Agentic Data Analyst: React/HTTP/CLI → Gemini → the existing manual LangChain tool
loop → read-only PostgreSQL → tool observations → final answer. The backend
is cloud-provider agnostic. The selected deployment stack is Vercel Hobby,
Render Free Web Service, and two Neon Free PostgreSQL projects; deployment
has not been executed.

## Setup

Requires Python 3.10 or newer.

```powershell
conda activate llm
python -m pip install -r requirements.txt
```

Copy `.env.example` to a local `.env` and fill in your own settings. Git ignores
`.env`; never commit credentials. Hosted processes can supply environment
variables directly; those values take precedence over `.env`.

| Variables | Purpose |
| --- | --- |
| `GOOGLE_API_KEY` | Gemini key |
| `DATABASE_SALES_URL`, `DATABASE_SAAS_URL` | Optional server-configured PostgreSQL URLs, one per demo profile |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Existing local PostgreSQL fallback; `DB_NAME` remains the CLI/eval default |
| `ALLOWED_ORIGINS` | Comma-separated browser origins, e.g. `http://localhost:5173`; empty disables cross-origin access |
| `TRACE_ENABLED`, `TRACE_DIR` | Persistence toggle (`true`/`false`, default true) and directory (default `runs/`) |

Hosted URL format:

```text
postgresql://<user>:<password>@<host>:5432/<database>?sslmode=require
```

Psycopg consumes the URL directly, retaining SSL and other connection options.
Provision `analyst_agent` with read-only permissions separately; never use admin
credentials as application configuration.

## Run observability

Agent runs save local structured JSON traces under Git-ignored `runs/` by default.
`TRACE_ENABLED=false` disables file persistence without affecting answers or
in-memory traces. Relative `TRACE_DIR` paths resolve from the repository root;
absolute paths are supported. Keep a custom directory outside Git or ignore it.
An unwritable directory emits a safe warning without replacing the agent result.
Ephemeral hosting disks can lose traces; no durable cloud storage is implemented.
Inspect a saved run without calling Gemini:

```powershell
python -m app.trace --latest
python -m app.trace "D:\ReAct Agent\runs\<trace-file>.json"
```

See [the tracing guide](docs/tracing.md) for the schema, preview limits,
redaction, failure handling, and evaluation linkage.

## Public backend (Phase 4.2)

Start the service locally in the existing environment:

```powershell
uvicorn app.api:app --reload
```

Production-style startup (no provider-specific configuration):

```powershell
uvicorn app.api:app --host 0.0.0.0 --port 8000
# When the hosting platform supplies PORT:
uvicorn app.api:app --host 0.0.0.0 --port $env:PORT
```

On a POSIX shell, the port form is `--port "$PORT"`. No deployment is performed
by these repository changes; the $0/month infrastructure target is not verified.

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Process liveness, `{"status":"ok"}`; no Gemini/DB calls |
| `GET /ready` | Configuration presence and both profile resolutions; 200 ready or sanitized 503 |
| `GET /databases` | Only `id`, display `name`, and `description` for the two profiles |
| `POST /query` | Stateless question → existing agent → answer and trace linkage |

Readiness deliberately does not connect to PostgreSQL or call Gemini. It cannot
guarantee live connectivity, credentials, permissions, or model quota.

`POST /query` accepts a question and an allowed database profile:

| Profile | Resolution |
| --- | --- |
| `sales` | `DATABASE_SALES_URL`, otherwise local `agentic_analyst` |
| `saas` | `DATABASE_SAAS_URL`, otherwise local `agentic_analyst_saas` |

Connection configuration is internal and absent from `/databases`. Each run
uses an isolated ContextVar carrying its local name or configured hosted URL;
profile selection never changes `os.environ`. Existing CLI and eval calls
retain their normal `DB_*` configuration.

Example request:

```json
{
  "question": "Which product category generated the most revenue?",
  "database": "sales"
}
```

Send it from PowerShell (`POST /query` invokes Gemini):

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/databases
$queryBody = @{
    question = "Which product category generated the most revenue?"
    database = "sales"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query `
    -ContentType "application/json" -Body $queryBody
```

Success response shape (illustrative, not a recorded database answer):

```json
{
  "status": "success",
  "answer": "<final answer based on tool results>",
  "run_id": "<run UUID>",
  "trace_path": "<local path to saved trace JSON>",
  "database": "sales"
}
```

Questions must contain text and be at most 4,000 characters. Missing fields,
unknown profiles, malformed requests, and extra fields return HTTP 400 with
fixed validation feedback. Clients cannot supply database names or connection
strings. Provider/configuration/database-unavailable failures and the response
limit return HTTP 503; tool runtime and unexpected internal failures return
HTTP 500. There are no automatic retries. Failure bodies contain
`status`, `error_type`, `message`, `run_id`, and `trace_path`, without raw errors
or submitted invalid values. Validation failures have null trace linkage since
no agent run started.

API runs reuse the manual agent and existing local traces, with `source="api"`
and `database_profile`. The response links the trace rather than returning it.
If trace persistence fails, the answer remains available and `trace_path` is
null. Traces keep their existing bounded previews and secret redaction.

**Demo/security scope:** only the predefined synthetic sales and SaaS datasets
are exposed; connecting arbitrary user databases is forbidden. Configure exact
frontend origins with `ALLOWED_ORIGINS`; wildcards are rejected and CORS
credentials are disabled. Restart the service after changing CORS settings.
CORS is browser policy, not authentication. PostgreSQL permissions and READ ONLY
transactions remain the security boundary; existing SQL guardrails, 5-second
statement timeout, and 100-row cap remain. The connection timeout is 5 seconds.
There is no public trace-download endpoint, authentication, or sessions.

Run the full tests without calling Gemini:

```powershell
$env:RUN_GENERALIZATION_DB_TESTS = "1"
python -m unittest discover -s tests -v
```

API tests use TestClient, fake models, and fake connections; integration tests
also check local sales/SaaS PostgreSQL. No automated test calls Gemini.

## React frontend (Phase 4.3)

React, TypeScript, Vite, and Tailwind CSS power the separate `frontend/` app.
It is implemented for local use and is **not deployed**.

> Screenshot: to be added after a local demo capture.

Requires Node.js 24 LTS (minimum supported version: 22.12). Run two terminals:

**Terminal 1 — backend**, from the repository root:

```powershell
cd "D:\ReAct Agent"
conda activate llm
$env:ALLOWED_ORIGINS = "http://localhost:5173"
uvicorn app.api:app --reload
```

**Terminal 2 — frontend**:

```powershell
cd "D:\ReAct Agent\frontend"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
npm ci
npm run dev
```

Open `http://localhost:5173`. The Vite server uses this fixed port so it matches
the backend CORS origin. Configure `VITE_API_BASE_URL` in `frontend/.env` when
the backend URL changes; its default is `http://localhost:8000`. Restart Vite
after changing it, and rebuild when changing a production build's API URL.
Frontend environment values are public: never add Gemini keys, database URLs,
or credentials. The example file contains only the API base URL.

Choose a dataset loaded from `/databases`, select an example or write a question,
then click **Ask Agent**. Examples fill the input without submitting. Enter adds
a newline; Ctrl/Cmd+Enter submits. Questions must contain text and stay within
4,000 characters. Each question is independent; submitting invokes Gemini on
the backend. Answers show the question, dataset, completion status, and optional
run ID. Server trace paths and internal execution details are not displayed.

The responsive page supports system-default light/dark mode and a saved toggle.
Loading feedback is generic, with a slow-request hint after 10 seconds. Friendly
errors offer a manual retry; there are no automatic query retries. Lightweight
startup dataset/health checks have a 15-second timeout and manual refresh.
Health only reports API liveness, not model or database readiness.

Frontend verification (mocked API; no FastAPI, Gemini, or database calls):

```powershell
cd "D:\ReAct Agent\frontend"
npm run test
npm run build
```

The lockfile pins dependencies. The build checks TypeScript and writes `dist/`;
dependencies, build output, local frontend environment files, and coverage are
Git ignored. Manual cloud migration and deployment are the next step.

## Public Deployment (Phase 4.4A)

**Preparation complete; not deployed.** The selected stack targets $0/month
within provider free-tier limits: Vercel Hobby frontend, Render Free backend,
two Neon Free databases, GitHub, and the existing Gemini Developer API.

Follow [the manual deployment guide and checklist](docs/deployment.md) for
exact PowerShell commands and credential-safe prompts:

1. **Neon:** create separate `agentic-analyst-sales` and `agentic-analyst-saas`
   PostgreSQL 18 projects. Export each local public schema with `pg_dump` and
   restore into its empty target with `pg_restore --no-owner --no-acl`.
   Comments, indexes, data, and PK/FK constraints are retained.
2. **Restricted role:** run `sql/deployment/01_runtime_role.sql` as the owner,
   set its password interactively with `\password analyst_agent`, and run the
   metadata and permission verification scripts as `analyst_agent`. Use only
   this role's SSL-enabled connection URLs in application configuration.
3. **Render:** connect the GitHub repo and review/import `render.yaml`. It
   defines one Free Python backend, `/health`, the standard Uvicorn `$PORT`
   command, dashboard-supplied secrets/origins, and `TRACE_ENABLED=false`.
   `.python-version` pins the locally tested **3.10.20**. The Gemini model
   stays **`gemini-3.5-flash-lite`**. No Render database is configured.
4. **Vercel:** connect the same repository with Root Directory `frontend`,
   framework Vite, Node 24.x, install `npm ci`, build `npm run build`, output
   `dist`, and public `VITE_API_BASE_URL` set to the Render backend origin.
   The one-page frontend needs no routing configuration or `vercel.json`.
5. **CORS:** obtain the Vercel production origin, set Render `ALLOWED_ORIGINS`
   to that exact origin, and redeploy/restart the backend. Never use `*`.
6. **Smoke test:** check health/configuration/datasets, then manually test a
   sales and SaaS question in the browser when ready to consume Gemini quota.

Dump files and `.deployment-tmp/` are ignored. Never commit populated env files,
credentials, or hosted URLs. Render Free storage is ephemeral: production
traces are disabled, while local/eval tracing remains unchanged. Its cold starts
use the existing generic UI hint; no keep-alive pings are added.

## Folder structure

```text
ReAct Agent/
├── app/
│   ├── __init__.py
│   ├── agent.py
│   ├── api.py
│   ├── config.py
│   ├── profiles.py
│   ├── llm.py
│   ├── database.py
│   ├── tools.py
│   └── trace.py
├── tests/
│   ├── test_agent.py
│   ├── test_api.py
│   ├── test_public_backend.py
│   ├── test_tools.py
│   ├── test_eval_cases.py
│   ├── test_eval_runner.py
│   ├── test_trace.py
│   └── test_generalization.py
├── docs/
│   ├── tracing.md
│   └── deployment.md
├── frontend/
│   ├── src/
│   │   ├── api/            # Typed client, contracts, and API tests
│   │   ├── components/     # Header, dataset, question, examples, answer
│   │   ├── test/setup.ts
│   │   ├── App.tsx
│   │   ├── App.test.tsx
│   │   ├── main.tsx
│   │   ├── index.css
│   │   └── examples.ts
│   ├── public/favicon.svg
│   ├── .env.example
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── eval/
├── sql/
│   └── deployment/        # Manual owner role setup + runtime verification
├── runs/                 # Local generated traces; ignored by Git
├── .gitignore
├── .env.example
├── .python-version
├── render.yaml
├── requirements.txt
└── README.md
```
