\set ON_ERROR_STOP on

BEGIN;

GRANT CONNECT ON DATABASE agentic_analyst_olist TO analyst_agent;

GRANT USAGE ON SCHEMA public TO analyst_agent;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_agent;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
GRANT SELECT ON TABLES TO analyst_agent;

COMMIT;