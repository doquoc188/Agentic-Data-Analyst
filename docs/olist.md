# Olist local dataset setup

Phase 6.1A prepares a separate local PostgreSQL database from the **Brazilian
E-Commerce Public Dataset by Olist**, published on
[Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce). The source
CSV files remain under Git-ignored `data/olist_raw/`; the importer reads them in
place and never edits them.

This phase creates only a local data foundation. Olist is not an Agent profile,
web dataset, MCP profile, benchmark, Neon database, or production feature yet.

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
