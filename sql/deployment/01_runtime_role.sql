-- MANUAL, ONE-TIME setup on each dedicated Neon demo database after restore.
-- Run as its owner with psql; this is not an application migration.
-- The role starts without a password. Set one afterward with \password.
-- Existing roles are refused rather than altered or silently reused.
\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF current_user = 'analyst_agent' THEN
        RAISE EXCEPTION 'Run runtime-role setup as the database owner.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_agent') THEN
        RAISE EXCEPTION 'analyst_agent already exists; inspect it manually before proceeding.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    ) THEN
        RAISE EXCEPTION 'Restore the demo tables before creating the runtime role.';
    END IF;
END $$;

-- SQL-created Neon roles avoid the admin memberships given by its role UI/API.
CREATE ROLE analyst_agent WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- PUBLIC privileges apply to every role, even one with NOINHERIT.
-- These revocations intentionally affect only this dedicated demo database.
DO $$
BEGIN
    EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO analyst_agent', current_database());
END $$;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO analyst_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_agent;

-- Applies only to future tables created by the owner running this script.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_agent;
COMMIT;
