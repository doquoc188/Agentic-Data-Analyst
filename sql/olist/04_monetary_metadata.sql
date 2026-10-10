\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
    IF current_database() <> 'agentic_analyst_olist' THEN
        RAISE EXCEPTION 'Run this migration only on agentic_analyst_olist.';
    END IF;

    IF to_regclass('public.order_items') IS NULL
       OR to_regclass('public.payments') IS NULL THEN
        RAISE EXCEPTION 'Olist tables are missing; install the schema first.';
    END IF;
END
$$;

COMMENT ON COLUMN public.order_items.price IS
    'Item price excluding freight_value. The source metadata does not explicitly declare a currency unit; consumers must not infer or display a currency symbol.';

COMMENT ON COLUMN public.order_items.freight_value IS
    'Freight or shipping amount for this item. The source metadata does not explicitly declare a currency unit; consumers must not infer or display a currency symbol.';

COMMENT ON COLUMN public.payments.payment_value IS
    'Amount for one payment row. An order can have multiple payment rows; sum payment_value by order_id for total payment amount. The source metadata does not explicitly declare a currency unit; consumers must not infer or display a currency symbol.';

COMMIT;
