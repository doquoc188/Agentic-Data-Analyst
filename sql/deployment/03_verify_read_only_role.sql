-- MANUAL privilege verification. Connect DIRECTLY as analyst_agent, never owner.
-- Deliberately READ WRITE: permission failures must come from the role itself.
-- Every probe has a subtransaction; unexpected success raises and rolls back.
\set ON_ERROR_STOP on
BEGIN READ WRITE;
SET LOCAL statement_timeout = '5000ms';
SET LOCAL lock_timeout = '1000ms';

DO $$
DECLARE
    runtime_role record;
    item record;
    probe_table text;
    probe_column text;
    all_tables text;
    operation text;
BEGIN
    IF current_user <> 'analyst_agent' OR session_user <> 'analyst_agent' THEN
        RAISE EXCEPTION 'Connect directly as analyst_agent; do not use SET ROLE.';
    END IF;
    SELECT * INTO runtime_role FROM pg_roles WHERE rolname = current_user;
    IF NOT runtime_role.rolcanlogin OR runtime_role.rolinherit OR runtime_role.rolsuper
       OR runtime_role.rolcreatedb OR runtime_role.rolcreaterole
       OR runtime_role.rolreplication OR runtime_role.rolbypassrls
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = runtime_role.oid) THEN
        RAISE EXCEPTION 'Unexpected runtime role attributes or role memberships.';
    END IF;
    IF NOT has_database_privilege(current_database(), 'CONNECT')
       OR has_database_privilege(current_database(), 'CREATE')
       OR has_database_privilege(current_database(), 'TEMPORARY')
       OR NOT has_schema_privilege('public', 'USAGE')
       OR has_schema_privilege('public', 'CREATE')
       OR EXISTS (SELECT 1 FROM pg_database WHERE datname = current_database() AND datdba = runtime_role.oid)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'public' AND nspowner = runtime_role.oid) THEN
        RAISE EXCEPTION 'Unexpected database/schema privileges or ownership.';
    END IF;
    FOR item IN
        SELECT c.oid, c.relname, c.relowner FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname
    LOOP
        IF NOT has_table_privilege(item.oid, 'SELECT')
           OR has_table_privilege(item.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
           OR has_any_column_privilege(item.oid, 'INSERT,UPDATE,REFERENCES')
           OR has_table_privilege(item.oid, 'SELECT WITH GRANT OPTION')
           OR item.relowner = runtime_role.oid THEN
            RAISE EXCEPTION 'Unexpected privileges or ownership for %.', item.relname;
        END IF;
        EXECUTE format('SELECT 1 FROM public.%I LIMIT 1', item.relname);
        RAISE NOTICE '%: SELECT allowed; write/DDL/grant privileges absent.', item.relname;
    END LOOP;

    SELECT c.relname, a.attname INTO probe_table, probe_column
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND a.attnum > 0 AND NOT a.attisdropped ORDER BY c.relname, a.attnum LIMIT 1;
    SELECT string_agg(format('public.%I', c.relname), ', ' ORDER BY c.relname) INTO all_tables
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');
    IF probe_table IS NULL THEN
        RAISE EXCEPTION 'No public demo tables found.';
    END IF;

    FOR operation IN SELECT unnest(ARRAY[
        -- Zero rows avoid invoking identity/sequence defaults even if allowed.
        format('INSERT INTO public.%I (%I) SELECT %I FROM public.%I WHERE false',
               probe_table, probe_column, probe_column, probe_table),
        format('UPDATE public.%I SET %I = %I WHERE false', probe_table, probe_column, probe_column),
        format('DELETE FROM public.%I WHERE false', probe_table),
        'TRUNCATE TABLE ' || all_tables,
        'CREATE TABLE public.analyst_permission_probe (id integer)',
        'CREATE TEMP TABLE analyst_permission_probe (id integer)'
    ])
    LOOP
        BEGIN
            EXECUTE operation;
            RAISE EXCEPTION 'Runtime operation unexpectedly allowed: %.', operation;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'Denied as expected: %.', operation;
        END;
    END LOOP;
END $$;
ROLLBACK;
