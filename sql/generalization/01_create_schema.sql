-- Run as the owner against agentic_analyst_saas, never the sales database.
-- Fresh fixture only: rerunning refuses existing public objects, without dropping data.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL TIME ZONE 'UTC';

DO $$
BEGIN
    IF current_database() <> 'agentic_analyst_saas' THEN
        RAISE EXCEPTION 'This migration requires agentic_analyst_saas.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    ) THEN
        RAISE EXCEPTION 'Public schema is not empty. Migration stopped; existing objects are preserved.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_agent') THEN
        RAISE EXCEPTION 'The existing analyst_agent role is required.';
    END IF;
END $$;

CREATE TABLE public.accounts (
    account_id integer PRIMARY KEY,
    account_name varchar(100) NOT NULL UNIQUE,
    country varchar(50) NOT NULL,
    signup_date date NOT NULL
);

CREATE TABLE public.plans (
    plan_id integer PRIMARY KEY,
    plan_name varchar(50) NOT NULL UNIQUE,
    plan_tier varchar(30) NOT NULL,
    monthly_price_per_seat numeric(10,2) NOT NULL CHECK (monthly_price_per_seat > 0)
);

CREATE TABLE public.subscriptions (
    subscription_id integer PRIMARY KEY,
    account_id integer NOT NULL REFERENCES public.accounts(account_id),
    plan_id integer NOT NULL REFERENCES public.plans(plan_id),
    start_date date NOT NULL,
    end_date date,
    status varchar(20) NOT NULL CHECK (status IN ('active', 'paused', 'cancelled')),
    seats integer NOT NULL CHECK (seats > 0),
    CHECK (end_date IS NULL OR end_date >= start_date),
    CHECK ((status = 'cancelled') = (end_date IS NOT NULL))
);

CREATE TABLE public.invoices (
    invoice_id integer PRIMARY KEY,
    subscription_id integer NOT NULL REFERENCES public.subscriptions(subscription_id),
    invoice_date date NOT NULL,
    amount numeric(12,2) NOT NULL CHECK (amount >= 0),
    status varchar(20) NOT NULL CHECK (status IN ('paid', 'open', 'void'))
);

CREATE TABLE public.support_tickets (
    ticket_id integer PRIMARY KEY,
    account_id integer NOT NULL REFERENCES public.accounts(account_id),
    opened_at timestamptz NOT NULL,
    closed_at timestamptz,
    priority varchar(20) NOT NULL CHECK (priority IN ('low', 'normal', 'high')),
    status varchar(20) NOT NULL CHECK (status IN ('open', 'pending', 'resolved')),
    CHECK (closed_at IS NULL OR closed_at >= opened_at),
    CHECK ((status = 'resolved') = (closed_at IS NOT NULL))
);

COMMENT ON COLUMN plans.monthly_price_per_seat IS
    'Monthly recurring price in USD per active seat. Current subscription MRR is seats multiplied by monthly_price_per_seat. Invoice payments are a separate measure.';
COMMENT ON COLUMN subscriptions.status IS
    'Current subscription snapshot: active contributes seats and MRR; paused and cancelled do not. Determine current activity from this status, not today''s date.';
COMMENT ON COLUMN subscriptions.end_date IS
    'Cancellation end date; NULL for active or paused subscriptions. NULL alone does not mean active.';
COMMENT ON COLUMN invoices.amount IS
    'Amount billed in USD, including billing adjustments. Paid invoice revenue is the sum of amount for paid invoices, independently of current subscription status.';
COMMENT ON COLUMN invoices.status IS
    'paid means payment received; open means unpaid; void means cancelled billing. Only paid invoices contribute paid invoice revenue.';
COMMENT ON COLUMN support_tickets.closed_at IS
    'Resolution timestamp in UTC; NULL means unresolved. Resolved duration is closed_at minus opened_at; exclude NULL values from resolved-duration averages.';
COMMENT ON COLUMN support_tickets.priority IS
    'Ticket priority levels: low, normal, high. Priority is independent of resolution status.';

-- Fixed IDs, arithmetic, and dates make every fresh fixture identical.
INSERT INTO public.accounts
SELECT id, 'Synthetic Account ' || lpad(id::text, 3, '0'),
       (ARRAY['Canada','Germany','Japan','United Kingdom','United States','Vietnam'])[1 + (id - 1) % 6],
       DATE '2024-01-01' + id * 5
FROM generate_series(1, 60) AS g(id);

INSERT INTO public.plans VALUES
    (1, 'Launch', 'Starter', 12.00),
    (2, 'Growth', 'Business', 29.00),
    (3, 'Scale', 'Business', 59.00),
    (4, 'Enterprise', 'Enterprise', 99.00);

INSERT INTO public.subscriptions
SELECT id, 1 + (id - 1) % 60,
       CASE WHEN id > 60 AND (1 + (id - 1) % 60) % 4 = 0
            THEN CASE WHEN 1 + (id - 1) % 60 <= 30 THEN 1 ELSE 2 END
            ELSE 1 + (id - 1) / 30 END,
       DATE '2024-11-01' + id % 28,
       CASE WHEN id % 5 = 0 THEN DATE '2025-04-01' + id % 28 END,
       CASE id % 5 WHEN 0 THEN 'cancelled' WHEN 1 THEN 'paused' ELSE 'active' END,
       1 + id * 7 % 25
FROM generate_series(1, 120) AS g(id);

-- Three billing months per subscription. Some whole accounts have no paid invoices.
INSERT INTO public.invoices
SELECT (s.subscription_id - 1) * 3 + month,
       s.subscription_id,
       (DATE '2025-01-01' + (month - 1) * INTERVAL '1 month')::date,
       s.seats * p.monthly_price_per_seat + ((s.subscription_id + month) % 7) * 3.50,
       CASE WHEN s.account_id % 11 = 0 THEN 'open'
            WHEN (s.subscription_id + month) % 7 = 0 THEN 'void'
            WHEN (s.subscription_id + month) % 4 = 0 THEN 'open'
            ELSE 'paid' END
FROM public.subscriptions s JOIN public.plans p ON p.plan_id = s.plan_id
CROSS JOIN generate_series(1, 3) AS g(month);

-- Only accounts 1..50 receive tickets; ten accounts intentionally have none.
INSERT INTO public.support_tickets
SELECT id, 1 + id * 17 % 50,
       TIMESTAMPTZ '2025-01-01 00:00:00+00' + (id % 90) * INTERVAL '1 day' + (id % 24) * INTERVAL '1 hour',
       CASE WHEN id % 4 <> 0 THEN
           TIMESTAMPTZ '2025-01-01 00:00:00+00' + (id % 90) * INTERVAL '1 day'
           + (id % 24 + id % 72 + 2) * INTERVAL '1 hour' END,
       (ARRAY['low','normal','high'])[1 + id % 3],
       CASE WHEN id % 8 = 0 THEN 'open' WHEN id % 4 = 0 THEN 'pending' ELSE 'resolved' END
FROM generate_series(1, 240) AS g(id);

-- Permissions affect only this new database. No runtime role credentials change.
REVOKE CREATE, TEMPORARY ON DATABASE agentic_analyst_saas FROM PUBLIC, analyst_agent;
GRANT CONNECT ON DATABASE agentic_analyst_saas TO analyst_agent;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, analyst_agent;
GRANT USAGE ON SCHEMA public TO analyst_agent;
REVOKE ALL ON public.accounts, public.plans, public.subscriptions, public.invoices,
    public.support_tickets FROM PUBLIC, analyst_agent;
GRANT SELECT ON public.accounts, public.plans, public.subscriptions, public.invoices,
    public.support_tickets TO analyst_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC, analyst_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_agent;

COMMIT;
