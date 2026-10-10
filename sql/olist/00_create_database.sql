-- Run separately against postgres as a local database administrator.
-- Existing databases are preserved; this script never drops or replaces one.
\set ON_ERROR_STOP on

SELECT format('CREATE DATABASE %I', 'agentic_analyst_olist')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_database
    WHERE datname = 'agentic_analyst_olist'
)
\gexec
