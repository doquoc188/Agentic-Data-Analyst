-- Read-only verification; safe to run repeatedly as owner or analyst_agent.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
DO $$
BEGIN
    IF current_database() <> 'agentic_analyst_saas' THEN
        RAISE EXCEPTION 'Verification requires agentic_analyst_saas.';
    END IF;
END $$;

SELECT 'accounts' AS table_name, COUNT(*) AS rows FROM public.accounts
UNION ALL SELECT 'plans', COUNT(*) FROM public.plans
UNION ALL SELECT 'subscriptions', COUNT(*) FROM public.subscriptions
UNION ALL SELECT 'invoices', COUNT(*) FROM public.invoices
UNION ALL SELECT 'support_tickets', COUNT(*) FROM public.support_tickets;

SELECT c.conrelid::regclass AS table_name, c.contype, pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid
JOIN pg_namespace n ON n.oid = r.relnamespace
WHERE n.nspname = 'public' AND c.contype IN ('p', 'f')
ORDER BY table_name, c.contype, c.conname;

SELECT t.table_name,
       has_table_privilege('analyst_agent', 'public.' || t.table_name, 'SELECT') AS can_select,
       has_table_privilege('analyst_agent', 'public.' || t.table_name, 'INSERT') AS can_insert,
       has_table_privilege('analyst_agent', 'public.' || t.table_name, 'UPDATE') AS can_update,
       has_table_privilege('analyst_agent', 'public.' || t.table_name, 'DELETE') AS can_delete,
       has_table_privilege('analyst_agent', 'public.' || t.table_name, 'TRUNCATE') AS can_truncate
FROM information_schema.tables t
WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE' ORDER BY t.table_name;

SELECT has_database_privilege('analyst_agent', current_database(), 'CONNECT') AS can_connect,
       has_database_privilege('analyst_agent', current_database(), 'CREATE') AS can_create_schema,
       has_database_privilege('analyst_agent', current_database(), 'TEMPORARY') AS can_create_temp,
       has_schema_privilege('analyst_agent', 'public', 'USAGE') AS can_use_public,
       has_schema_privilege('analyst_agent', 'public', 'CREATE') AS can_create_table;

SELECT r.relname AS table_name, a.attname AS column_name,
       col_description(r.oid, a.attnum) AS description
FROM pg_class r JOIN pg_namespace n ON n.oid = r.relnamespace
JOIN pg_attribute a ON a.attrelid = r.oid
WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
  AND col_description(r.oid, a.attnum) IS NOT NULL
ORDER BY r.relname, a.attnum;
COMMIT;
