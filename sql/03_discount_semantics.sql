-- Phase 3.4.2: trusted business metadata, readable by analyst_agent.
-- Run separately as the table owner (postgres); application credentials stay read-only.
-- Re-running sets the same comments. No rows, types, constraints, or grants change.
BEGIN;

COMMENT ON COLUMN public.sales.discount_pct IS
    'Fractional discount rate: 0.10 means a 10% discount. The revenue multiplier is (1 - discount_pct), without dividing by 100.';

COMMENT ON COLUMN public.order_items.discount_pct IS
    'Fractional discount rate: 0.10 means a 10% discount. The revenue multiplier is (1 - discount_pct), without dividing by 100.';

COMMIT;
