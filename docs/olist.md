# Olist local dataset setup

This local extension uses the **Brazilian E-Commerce Public Dataset by Olist**,
published on
[Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce). The source
CSV files remain under Git-ignored `data/olist_raw/`; the importer reads them in
place and never edits them.

## Phase status and boundaries

### Phase 6.1A — local relational foundation

Phase 6.1A created and verified the separate local PostgreSQL database
`agentic_analyst_olist`, its deterministic CSV importer, read-only verifier, and
`analyst_agent` grants. The live local verification confirmed all eight tables,
550,759 imported rows, constraints, indexes, comments, source NULL counts,
categorical distributions, untranslated categories, and PostgreSQL write rejection.

For an existing Phase 6.1A database, install the additive monetary-metadata
migration as the database owner, then rerun the verifier:

```powershell
psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U postgres -W `
  -d agentic_analyst_olist -f sql/olist/04_monetary_metadata.sql

psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U postgres -W `
  -d agentic_analyst_olist -f sql/olist/02_verify.sql
```

### Phase 6.1C — local Agent profile integration

Phase 6.1C adds `olist` to the profiles that can be resolved for a local Agent
run. It reuses the existing manual Agent, scoped database context, and generic
`get_schema`, `describe_table`, and `execute_sql` tools. No Olist table names,
business rules, or SQL templates are added to the global Agent prompt or tools.

The local profile has this safe display metadata:

- ID: `olist`
- Name: `Olist E-Commerce`
- Description: `An anonymized real-world Brazilian e-commerce dataset.`

Olist remains local only. It is not selectable through the deployed API, not
listed by `GET /databases`, not available in the production frontend or MCP,
not hosted on Neon, and has no official Agent benchmark score. Existing Sales
22/24 (91.67%) and unseen SaaS 14/16 (87.50%) scores are unchanged.

## Imported data

Eight CSV datasets are imported into `public`:

| Table | Expected rows | Key |
| --- | ---: | --- |
| `customers` | 99,441 | `customer_id` |
| `orders` | 99,441 | `order_id` |
| `products` | 32,951 | `product_id` |
| `sellers` | 3,095 | `seller_id` |
| `order_items` | 112,650 | `(order_id, order_item_id)` |
| `payments` | 103,886 | `(order_id, payment_sequential)` |
| `reviews` | 99,224 | `(review_id, order_id)` |
| `category_translation` | 71 | `category_name` |

`olist_geolocation_dataset.csv` is validated as part of the downloaded source
set but intentionally not imported. Its one-million-row location model is
deferred. Products do not have a mandatory FK to `category_translation` because
`pc_gamer` and `portateis_cozinha_e_preparadores_de_alimentos` have no English
translation row.

## Local setup

Use a local PostgreSQL administrator/owner. These commands target the dedicated
database `agentic_analyst_olist`; the migration and importer both verify that name.
The schema script also refuses a non-empty public schema and never drops objects.

```powershell
conda activate llm
cd "D:\ReAct Agent"

psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U postgres -W `
  -d postgres -f sql/olist/00_create_database.sql

psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U postgres -W `
  -d agentic_analyst_olist -f sql/olist/01_create_schema.sql
```

The Python importer reuses the project's `DB_HOST`, `DB_PORT`, `DB_USER`, and
`DB_PASSWORD` settings, while forcing the database name to
`agentic_analyst_olist`. Use an owner-capable local credential for this one-time
import. The following keeps the password in the process environment only for the
import command and does not print it:

```powershell
$olistCredential = Get-Credential -UserName postgres -Message 'Local Olist database owner'
$previousDbUser = $env:DB_USER
$previousDbPassword = $env:DB_PASSWORD
try {
  $env:DB_USER = $olistCredential.UserName
  $env:DB_PASSWORD = $olistCredential.GetNetworkCredential().Password
  python -m sql.olist.import_olist
} finally {
  $env:DB_USER = $previousDbUser
  $env:DB_PASSWORD = $previousDbPassword
  Remove-Variable olistCredential, previousDbUser, previousDbPassword
}
```

The importer fails before connecting if any of the nine expected CSV files is
missing. It checks exact headers, parses integer/decimal/timestamp values,
preserves empty nullable fields as PostgreSQL NULL, imports in FK-safe order,
checks every source and target row count, and commits all eight tables in one
transaction. Any failure rolls the transaction back; an existing populated
target is not overwritten.

## Verification

Run the read-only SQL verifier after import:

```powershell
psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U postgres -W `
  -d agentic_analyst_olist -f sql/olist/02_verify.sql
```

It fails on count drift, missing PK/FK/index/check constraints, missing semantic
comments, changed NULL counts, changed categorical distributions, unexpected
translated-category coverage, an extra table, or a geolocation table. It prints
the verified counts, constraints, and indexes after all assertions pass.

Offline importer tests require no database connection:

```powershell
python -m unittest tests.test_olist_import -v
```

## Local Agent profile configuration

The normal local fallback uses the existing `DB_HOST`, `DB_PORT`, `DB_USER`, and
`DB_PASSWORD` settings with the fixed database name `agentic_analyst_olist`.
Keep `DB_USER=analyst_agent`. The primary `DB_NAME` remains unchanged for normal
CLI and evaluation behavior.

`DATABASE_OLIST_URL` is an optional private override for local Agent use. When it
is present, `resolve_profile("olist")` uses that URL; otherwise it uses the local
fallback above. Never commit, print, or expose this value. Olist configuration
does not alter Sales or SaaS resolution.

## Manual Agent smoke test

Manual Agent verification passed all 6 selected questions:

- 99,441 orders
- `delivered` as the most common order status
- `credit_card` as the most common payment type
- `health_beauty` as the top item-revenue category, with 1,258,681.34
- seller `4869f7a5dfa277a7dca6462dcf3b52b2` as the top seller, with 229,472.63
- 2,997 repeat customers, correctly using `customer_unique_id` rather than the
  order-scoped `customer_id`

The source metadata does not explicitly declare a currency unit for item price,
freight, or payment values. Agent answers and other consumers must therefore
report those values without inferring a currency symbol unless metadata explicitly
provides one.

Further checks remain manual because they call Gemini. Review the profile and
configuration first, then run one question at a time from the repository root:

```powershell
$env:OLIST_QUESTION = Read-Host 'Olist question'
try {
  python -c "import os; from app.agent import run_agent; from app.profiles import resolve_profile; from app.trace import AgentTrace; target = resolve_profile('olist'); run_agent(os.environ['OLIST_QUESTION'], trace=AgentTrace(source='cli', database_profile='olist'), database_name=target.database_name, database_url=target.url)"
} finally {
  Remove-Item Env:OLIST_QUESTION
}
```

Suggested questions:

1. **Easy:** How many orders are in the dataset? Known database fact: 99,441.
2. **Easy:** Which order status occurs most often? Known database fact: `delivered`.
3. **Easy:** Which payment type is used most often? Known database fact: `credit_card`.
4. **Medium:** What is the average review score?
5. **Medium:** Which state has the most customers?
6. **Medium:** What percentage of orders were delivered?
7. **Harder JOIN:** Which product category generated the most item revenue?
8. **Harder JOIN:** Which seller generated the highest item revenue?
9. **Harder JOIN:** Which product categories have the highest average freight value?
10. **Harder generalization:** How many unique customers placed more than one order?

For question 10, the correct customer identity field is discoverable from the
PostgreSQL comments returned by `describe_table`; it is intentionally absent from
the global prompt. No reference SQL is supplied to the Agent.

## Profile tests

Offline profile/API/MCP isolation checks:

```powershell
python -m pytest -q tests/test_olist_profile.py -p no:cacheprovider
```

Optional local database checks are disabled by default and never call Gemini:

```powershell
$env:RUN_OLIST_DB_TESTS = "1"
try {
  python -m pytest -q tests/test_olist_profile.py -p no:cacheprovider
} finally {
  Remove-Item Env:RUN_OLIST_DB_TESTS
}
```

The optional test refuses a configured Olist URL or non-local host. It verifies
the local database/role identity, exactly eight discovered tables, absence of
geolocation, semantic comments, read-only query execution, and write rejection.
