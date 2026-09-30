-- Run after 01_normalize_sales.sql. This script only reads the database.
-- psql -X -v ON_ERROR_STOP=1 -h localhost -U postgres -d agentic_analyst -f sql/02_verify_normalization.sql

-- A. Row counts
SELECT 'sales' AS table_name, COUNT(*) AS rows FROM public.sales
UNION ALL SELECT 'customers', COUNT(*) FROM public.customers
UNION ALL SELECT 'products', COUNT(*) FROM public.products
UNION ALL SELECT 'orders', COUNT(*) FROM public.orders
UNION ALL SELECT 'order_items', COUNT(*) FROM public.order_items;

-- B. Primary and foreign keys
SELECT c.relname AS table_name, con.conname AS constraint_name,
       CASE con.contype WHEN 'p' THEN 'PRIMARY KEY' WHEN 'f' THEN 'FOREIGN KEY' END AS kind,
       pg_catalog.pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('customers', 'products', 'orders', 'order_items')
  AND con.contype IN ('p', 'f')
ORDER BY c.relname, con.conname;

-- C. A sample four-table JOIN
SELECT o.order_id, c.customer_name, p.product_name, oi.quantity,
       oi.unit_price, oi.discount_pct
FROM public.customers AS c
JOIN public.orders AS o ON o.customer_id = c.customer_id
JOIN public.order_items AS oi ON oi.order_id = o.order_id
JOIN public.products AS p ON p.product_id = oi.product_id
ORDER BY o.order_id
LIMIT 5;

-- D and E. Completed-order revenue must agree with the original sales rows.
WITH source AS (
    SELECT SUM(quantity * unit_price * (1 - discount_pct)) AS revenue
    FROM public.sales
    WHERE status = 'Completed'
), normalized AS (
    SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) AS revenue
    FROM public.orders AS o
    JOIN public.order_items AS oi ON oi.order_id = o.order_id
    WHERE o.status = 'Completed'
)
SELECT source.revenue AS sales_revenue,
       normalized.revenue AS normalized_revenue,
       source.revenue = normalized.revenue AS totals_match
FROM source CROSS JOIN normalized;

-- Confirm that each original row has a matching normalized row.
SELECT COUNT(*) AS matching_sales_rows
FROM public.sales AS s
WHERE EXISTS (
    SELECT 1
    FROM public.orders AS o
    JOIN public.customers AS c ON c.customer_id = o.customer_id
    JOIN public.order_items AS oi ON oi.order_id = o.order_id
    JOIN public.products AS p ON p.product_id = oi.product_id
    WHERE o.order_id = s.order_id
      AND o.order_date = s.order_date
      AND o.payment_method = s.payment_method
      AND o.status = s.status
      AND c.customer_name = s.customer_name
      AND c.city = s.city
      AND p.product_name = s.product_name
      AND p.category = s.category
      AND p.unit_price = s.unit_price
      AND oi.quantity = s.quantity
      AND oi.unit_price = s.unit_price
      AND oi.discount_pct = s.discount_pct
);

-- The application role can read these tables but cannot write to them.
SELECT table_name,
       has_table_privilege('analyst_agent', 'public.' || table_name, 'SELECT') AS can_select,
       has_table_privilege('analyst_agent', 'public.' || table_name, 'INSERT') AS can_insert
FROM (VALUES ('customers'), ('products'), ('orders'), ('order_items')) AS t(table_name);
