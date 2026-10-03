-- Run separately against postgres as a database administrator. No passwords here.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_agent') THEN
        RAISE EXCEPTION 'The existing analyst_agent role is required.';
    END IF;
END $$;

-- CREATE DATABASE cannot run inside a transaction. psql executes this statement
-- only if the target does not exist; an existing database is never replaced.
SELECT format('CREATE DATABASE %I', 'agentic_analyst_saas')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'agentic_analyst_saas')
\gexec

GRANT CONNECT ON DATABASE agentic_analyst_saas TO analyst_agent;
