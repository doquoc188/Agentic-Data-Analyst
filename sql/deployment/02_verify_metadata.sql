-- MANUAL read-only verification, connected as analyst_agent.
-- psql ... -v profile=sales|saas -f sql/deployment/02_verify_metadata.sql
\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5000ms';
SELECT set_config('analyst_verification.profile', :'profile', true);

DO $$
DECLARE
    profile text := current_setting('analyst_verification.profile');
    expected jsonb;
    item record;
    actual_count bigint;
BEGIN
    IF current_user <> 'analyst_agent' THEN
        RAISE EXCEPTION 'Connect directly as analyst_agent to verify runtime metadata access.';
    END IF;
    CASE profile
        WHEN 'sales' THEN expected := '{"sales":300,"customers":20,"products":12,"orders":300,"order_items":300}';
        WHEN 'saas' THEN expected := '{"accounts":60,"plans":4,"subscriptions":120,"invoices":360,"support_tickets":240}';
        ELSE RAISE EXCEPTION 'profile must be sales or saas.';
    END CASE;
    IF ARRAY(SELECT c.relname::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname)
       <> ARRAY(SELECT key FROM jsonb_each_text(expected) ORDER BY key) THEN
        RAISE EXCEPTION 'Public tables do not match the selected demo profile.';
    END IF;
    FOR item IN SELECT key AS table_name, value::bigint AS row_count FROM jsonb_each_text(expected)
    LOOP
        EXECUTE format('SELECT COUNT(*) FROM public.%I', item.table_name) INTO actual_count;
        IF actual_count <> item.row_count THEN
            RAISE EXCEPTION 'Unexpected row count for %: expected %, found %.',
                item.table_name, item.row_count, actual_count;
        END IF;
        -- The original sales definition was not scripted; report its PK below.
        IF item.table_name <> 'sales' AND NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conrelid = format('public.%I', item.table_name)::regclass
                AND contype = 'p'
        ) THEN
            RAISE EXCEPTION 'Primary key missing for %.', item.table_name;
        END IF;
        RAISE NOTICE '%: % rows; runtime SELECT verified.', item.table_name, actual_count;
    END LOOP;
END $$;

-- Real PK/FK and index definitions visible to the runtime role.
SELECT c.conrelid::regclass AS table_name, c.conname, c.contype,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND c.contype IN ('p', 'f')
ORDER BY table_name, c.contype, c.conname;

SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' ORDER BY tablename, indexname;

-- Compare these comments with sql/03_discount_semantics.sql (sales) and
-- sql/generalization/01_create_schema.sql (SaaS). pg_dump preserves them.
SELECT t.relname AS table_name, a.attname AS column_name,
       col_description(t.oid, a.attnum) AS description
FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_attribute a ON a.attrelid = t.oid
WHERE n.nspname = 'public' AND t.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND col_description(t.oid, a.attnum) IS NOT NULL
ORDER BY t.relname, a.attnum;
ROLLBACK;
