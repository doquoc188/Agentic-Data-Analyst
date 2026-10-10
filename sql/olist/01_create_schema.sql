-- Run as the owner against agentic_analyst_olist only.
-- Fresh database only: existing public objects cause a safe failure.
\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF current_database() <> 'agentic_analyst_olist' THEN
        RAISE EXCEPTION 'This migration requires agentic_analyst_olist.';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    ) THEN
        RAISE EXCEPTION 'Public schema is not empty. Existing objects are preserved.';
    END IF;
END $$;

CREATE TABLE public.customers (
    customer_id text PRIMARY KEY,
    customer_unique_id text NOT NULL,
    zip_code_prefix integer NOT NULL,
    city text NOT NULL,
    state char(2) NOT NULL
);

CREATE TABLE public.orders (
    order_id text PRIMARY KEY,
    customer_id text NOT NULL,
    status text NOT NULL,
    purchase_timestamp timestamp NOT NULL,
    approved_at timestamp,
    delivered_carrier_date timestamp,
    delivered_customer_date timestamp,
    estimated_delivery_date timestamp NOT NULL,
    CONSTRAINT orders_customer_id_fkey
        FOREIGN KEY (customer_id) REFERENCES public.customers (customer_id)
);

CREATE TABLE public.products (
    product_id text PRIMARY KEY,
    category_name text,
    name_length integer,
    description_length integer,
    photos_qty integer,
    weight_g integer,
    length_cm integer,
    height_cm integer,
    width_cm integer
);

CREATE TABLE public.sellers (
    seller_id text PRIMARY KEY,
    zip_code_prefix integer NOT NULL,
    city text NOT NULL,
    state char(2) NOT NULL
);

CREATE TABLE public.order_items (
    order_id text NOT NULL,
    order_item_id integer NOT NULL,
    product_id text NOT NULL,
    seller_id text NOT NULL,
    shipping_limit_date timestamp NOT NULL,
    price numeric(12,2) NOT NULL,
    freight_value numeric(12,2) NOT NULL,
    CONSTRAINT order_items_pkey PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT order_items_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES public.orders (order_id),
    CONSTRAINT order_items_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES public.products (product_id),
    CONSTRAINT order_items_seller_id_fkey
        FOREIGN KEY (seller_id) REFERENCES public.sellers (seller_id)
);

CREATE TABLE public.payments (
    order_id text NOT NULL,
    payment_sequential integer NOT NULL,
    payment_type text NOT NULL,
    payment_installments integer NOT NULL,
    payment_value numeric(12,2) NOT NULL,
    CONSTRAINT payments_pkey PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT payments_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES public.orders (order_id)
);

CREATE TABLE public.reviews (
    review_id text NOT NULL,
    order_id text NOT NULL,
    review_score smallint NOT NULL,
    review_comment_title text,
    review_comment_message text,
    review_creation_date timestamp NOT NULL,
    review_answer_timestamp timestamp NOT NULL,
    CONSTRAINT reviews_pkey PRIMARY KEY (review_id, order_id),
    CONSTRAINT reviews_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES public.orders (order_id),
    CONSTRAINT reviews_review_score_check CHECK (review_score BETWEEN 1 AND 5)
);

CREATE TABLE public.category_translation (
    category_name text PRIMARY KEY,
    category_name_english text NOT NULL
);

CREATE INDEX orders_customer_id_idx ON public.orders (customer_id);
CREATE INDEX orders_status_idx ON public.orders (status);
CREATE INDEX orders_purchase_timestamp_idx ON public.orders (purchase_timestamp);
CREATE INDEX customers_customer_unique_id_idx ON public.customers (customer_unique_id);
CREATE INDEX order_items_product_id_idx ON public.order_items (product_id);
CREATE INDEX order_items_seller_id_idx ON public.order_items (seller_id);
CREATE INDEX payments_payment_type_idx ON public.payments (payment_type);
CREATE INDEX reviews_order_id_idx ON public.reviews (order_id);
CREATE INDEX products_category_name_idx ON public.products (category_name);

COMMENT ON COLUMN public.customers.customer_id IS
    'Order-scoped customer record identifier.';
COMMENT ON COLUMN public.customers.customer_unique_id IS
    'Stable identifier for the same customer across multiple orders. Use this field for repeat-customer analysis.';
COMMENT ON COLUMN public.orders.status IS
    'Observed values include delivered, shipped, canceled, unavailable, invoiced, processing, created, and approved. Delivered is the closest equivalent to a completed order.';
COMMENT ON COLUMN public.orders.purchase_timestamp IS
    'Timestamp when the customer placed the order.';
COMMENT ON COLUMN public.orders.delivered_customer_date IS
    'Actual delivery timestamp. NULL when delivery was not recorded.';
COMMENT ON COLUMN public.order_items.price IS
    'Item price excluding freight_value.';
COMMENT ON COLUMN public.order_items.freight_value IS
    'Freight or shipping amount for this item.';
COMMENT ON COLUMN public.payments.payment_value IS
    'Payment amount for one payment record. An order can have multiple payment rows; sum payment_value by order_id for total payment amount.';
COMMENT ON COLUMN public.reviews.review_score IS
    'Customer review score from 1 to 5, where 5 is best.';
COMMENT ON COLUMN public.products.category_name IS
    'Portuguese product category name. English translations are available for most but not all categories.';
COMMENT ON TABLE public.category_translation IS
    'Maps Portuguese product category names to English where a translation is available. Some product categories have no matching row.';

COMMIT;
