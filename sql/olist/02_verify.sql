-- Read-only verification for the local Olist database. Safe to repeat.
\set ON_ERROR_STOP on
BEGIN READ ONLY;

DO $$
DECLARE
    actual integer;
BEGIN
    IF current_database() <> 'agentic_analyst_olist' THEN
        RAISE EXCEPTION 'Verification requires agentic_analyst_olist.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'geolocation'
    ) THEN
        RAISE EXCEPTION 'Geolocation must not be imported in Phase 6.1A.';
    END IF;

    SELECT COUNT(*) INTO actual
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
    IF actual <> 8 THEN
        RAISE EXCEPTION 'Expected exactly eight public base tables; found %.', actual;
    END IF;

    IF (SELECT COUNT(*) FROM public.customers) <> 99441
       OR (SELECT COUNT(*) FROM public.orders) <> 99441
       OR (SELECT COUNT(*) FROM public.products) <> 32951
       OR (SELECT COUNT(*) FROM public.sellers) <> 3095
       OR (SELECT COUNT(*) FROM public.order_items) <> 112650
       OR (SELECT COUNT(*) FROM public.payments) <> 103886
       OR (SELECT COUNT(*) FROM public.reviews) <> 99224
       OR (SELECT COUNT(*) FROM public.category_translation) <> 71 THEN
        RAISE EXCEPTION 'One or more Olist table counts differ from the source files.';
    END IF;

    IF EXISTS (
        WITH expected(table_name, key_columns) AS (VALUES
            ('customers', ARRAY['customer_id']::text[]),
            ('orders', ARRAY['order_id']::text[]),
            ('products', ARRAY['product_id']::text[]),
            ('sellers', ARRAY['seller_id']::text[]),
            ('order_items', ARRAY['order_id', 'order_item_id']::text[]),
            ('payments', ARRAY['order_id', 'payment_sequential']::text[]),
            ('reviews', ARRAY['review_id', 'order_id']::text[]),
            ('category_translation', ARRAY['category_name']::text[])
        ), actual AS (
            SELECT c.relname AS table_name,
                   array_agg(a.attname ORDER BY k.ordinality)::text[] AS key_columns
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ordinality)
            JOIN pg_catalog.pg_attribute AS a
              ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE n.nspname = 'public' AND con.contype = 'p'
            GROUP BY c.relname
        )
        SELECT 1 FROM expected FULL JOIN actual USING (table_name)
        WHERE expected.key_columns IS DISTINCT FROM actual.key_columns
    ) THEN
        RAISE EXCEPTION 'Expected primary keys are missing.';
    END IF;

    IF EXISTS (
        WITH expected(table_name, constraint_name, source_column,
                      referenced_table, referenced_column) AS (VALUES
            ('orders', 'orders_customer_id_fkey', 'customer_id', 'customers', 'customer_id'),
            ('order_items', 'order_items_order_id_fkey', 'order_id', 'orders', 'order_id'),
            ('order_items', 'order_items_product_id_fkey', 'product_id', 'products', 'product_id'),
            ('order_items', 'order_items_seller_id_fkey', 'seller_id', 'sellers', 'seller_id'),
            ('payments', 'payments_order_id_fkey', 'order_id', 'orders', 'order_id'),
            ('reviews', 'reviews_order_id_fkey', 'order_id', 'orders', 'order_id')
        ), actual AS (
            SELECT source.relname AS table_name, con.conname AS constraint_name,
                   source_column.attname AS source_column,
                   target.relname AS referenced_table,
                   target_column.attname AS referenced_column
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS source ON source.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = source.relnamespace
            JOIN pg_catalog.pg_class AS target ON target.oid = con.confrelid
            JOIN pg_catalog.pg_attribute AS source_column
              ON source_column.attrelid = con.conrelid AND source_column.attnum = con.conkey[1]
            JOIN pg_catalog.pg_attribute AS target_column
              ON target_column.attrelid = con.confrelid AND target_column.attnum = con.confkey[1]
            WHERE n.nspname = 'public' AND con.contype = 'f'
              AND cardinality(con.conkey) = 1
        )
        SELECT 1 FROM expected FULL JOIN actual
          USING (table_name, constraint_name, source_column, referenced_table, referenced_column)
        WHERE expected.table_name IS NULL OR actual.table_name IS NULL
    ) THEN
        RAISE EXCEPTION 'Expected foreign keys are missing.';
    END IF;

    IF (SELECT COUNT(*) FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public'
          AND indexname IN ('orders_customer_id_idx', 'orders_status_idx',
                            'orders_purchase_timestamp_idx', 'customers_customer_unique_id_idx',
                            'order_items_product_id_idx', 'order_items_seller_id_idx',
                            'payments_payment_type_idx', 'reviews_order_id_idx',
                            'products_category_name_idx')) <> 9 THEN
        RAISE EXCEPTION 'Expected analytical indexes are missing.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'reviews'
          AND con.conname = 'reviews_review_score_check'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%review_score%1%5%'
    ) THEN
        RAISE EXCEPTION 'reviews.review_score check constraint is missing.';
    END IF;

    IF (SELECT COUNT(*) FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
        WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
          AND col_description(c.oid, a.attnum) IS NOT NULL
          AND (c.relname, a.attname) IN (
              ('customers', 'customer_id'), ('customers', 'customer_unique_id'),
              ('orders', 'status'), ('order_items', 'price'),
              ('order_items', 'freight_value'), ('payments', 'payment_value'),
              ('reviews', 'review_score'), ('products', 'category_name')
          )) <> 8
       OR obj_description('public.category_translation'::regclass, 'pg_class') IS NULL THEN
        RAISE EXCEPTION 'Required semantic comments are missing.';
    END IF;

    IF (SELECT COUNT(*) FROM public.reviews WHERE review_comment_title IS NULL) <> 87656
       OR (SELECT COUNT(*) FROM public.reviews WHERE review_comment_message IS NULL) <> 58247
       OR (SELECT COUNT(*) FROM public.orders WHERE approved_at IS NULL) <> 160
       OR (SELECT COUNT(*) FROM public.orders WHERE delivered_carrier_date IS NULL) <> 1783
       OR (SELECT COUNT(*) FROM public.orders WHERE delivered_customer_date IS NULL) <> 2965
       OR (SELECT COUNT(*) FROM public.products WHERE category_name IS NULL) <> 610
       OR (SELECT COUNT(*) FROM public.products WHERE name_length IS NULL) <> 610
       OR (SELECT COUNT(*) FROM public.products WHERE description_length IS NULL) <> 610
       OR (SELECT COUNT(*) FROM public.products WHERE photos_qty IS NULL) <> 610
       OR (SELECT COUNT(*) FROM public.products WHERE weight_g IS NULL) <> 2
       OR (SELECT COUNT(*) FROM public.products WHERE length_cm IS NULL) <> 2
       OR (SELECT COUNT(*) FROM public.products WHERE height_cm IS NULL) <> 2
       OR (SELECT COUNT(*) FROM public.products WHERE width_cm IS NULL) <> 2 THEN
        RAISE EXCEPTION 'Nullable-field counts differ from the source CSV files.';
    END IF;

    IF EXISTS (
        WITH expected(status, rows) AS (VALUES
            ('delivered', 96478::bigint), ('shipped', 1107), ('canceled', 625),
            ('unavailable', 609), ('invoiced', 314), ('processing', 301),
            ('created', 5), ('approved', 2)
        ), actual AS (
            SELECT status, COUNT(*) AS rows FROM public.orders GROUP BY status
        )
        SELECT 1 FROM expected FULL JOIN actual USING (status)
        WHERE expected.rows IS DISTINCT FROM actual.rows
    ) THEN
        RAISE EXCEPTION 'Order-status distribution differs from the source.';
    END IF;

    IF EXISTS (
        WITH expected(payment_type, rows) AS (VALUES
            ('credit_card', 76795::bigint), ('boleto', 19784), ('voucher', 5775),
            ('debit_card', 1529), ('not_defined', 3)
        ), actual AS (
            SELECT payment_type, COUNT(*) AS rows FROM public.payments GROUP BY payment_type
        )
        SELECT 1 FROM expected FULL JOIN actual USING (payment_type)
        WHERE expected.rows IS DISTINCT FROM actual.rows
    ) THEN
        RAISE EXCEPTION 'Payment-type distribution differs from the source.';
    END IF;

    IF EXISTS (
        WITH expected(review_score, rows) AS (VALUES
            (1::smallint, 11424::bigint), (2, 3151), (3, 8179), (4, 19142), (5, 57328)
        ), actual AS (
            SELECT review_score, COUNT(*) AS rows FROM public.reviews GROUP BY review_score
        )
        SELECT 1 FROM expected FULL JOIN actual USING (review_score)
        WHERE expected.rows IS DISTINCT FROM actual.rows
    ) THEN
        RAISE EXCEPTION 'Review-score distribution differs from the source.';
    END IF;

    IF (SELECT array_agg(category_name ORDER BY category_name)
        FROM (SELECT DISTINCT p.category_name
              FROM public.products AS p
              LEFT JOIN public.category_translation AS t USING (category_name)
              WHERE p.category_name IS NOT NULL AND t.category_name IS NULL) AS missing)
       IS DISTINCT FROM ARRAY['pc_gamer', 'portateis_cozinha_e_preparadores_de_alimentos']::text[] THEN
        RAISE EXCEPTION 'The untranslated-category set differs from the verified source.';
    END IF;
END $$;

SELECT 'customers' AS table_name, COUNT(*) AS rows FROM public.customers
UNION ALL SELECT 'orders', COUNT(*) FROM public.orders
UNION ALL SELECT 'products', COUNT(*) FROM public.products
UNION ALL SELECT 'sellers', COUNT(*) FROM public.sellers
UNION ALL SELECT 'order_items', COUNT(*) FROM public.order_items
UNION ALL SELECT 'payments', COUNT(*) FROM public.payments
UNION ALL SELECT 'reviews', COUNT(*) FROM public.reviews
UNION ALL SELECT 'category_translation', COUNT(*) FROM public.category_translation
ORDER BY table_name;

SELECT c.relname AS table_name, con.conname AS constraint_name,
       CASE con.contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'f' THEN 'FOREIGN KEY'
            WHEN 'c' THEN 'CHECK' END AS kind,
       pg_catalog.pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND con.contype IN ('p', 'f', 'c')
ORDER BY c.relname, con.conname;

SELECT tablename, indexname, indexdef
FROM pg_catalog.pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

COMMIT;
