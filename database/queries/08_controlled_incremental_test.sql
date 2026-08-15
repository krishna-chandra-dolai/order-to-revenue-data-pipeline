-- One-time controlled non-zero incremental test approved on 2026-08-15.
-- Inserts and retains exactly one customer, product, order, and payment.
-- This script is intentionally fail-closed and cannot be rerun after success.

\set ON_ERROR_STOP on

BEGIN;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL statement_timeout = '30s';

DO $controlled_test$
DECLARE
    test_ts TIMESTAMPTZ := transaction_timestamp();
BEGIN
    IF (SELECT COUNT(*) FROM public.customers) <> 2000
       OR (SELECT COUNT(*) FROM public.products) <> 300
       OR (SELECT COUNT(*) FROM public.orders) <> 10000
       OR (SELECT COUNT(*) FROM public.payments) <> 9990 THEN
        RAISE EXCEPTION 'Source counts changed after approval precheck; aborting';
    END IF;

    IF EXISTS (SELECT 1 FROM public.customers WHERE customer_id = 2001)
       OR EXISTS (SELECT 1 FROM public.products WHERE product_id = 301)
       OR EXISTS (SELECT 1 FROM public.orders WHERE order_id = 10001)
       OR EXISTS (SELECT 1 FROM public.payments WHERE payment_id = 9991) THEN
        RAISE EXCEPTION 'One or more approved test IDs now exist; aborting';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.customers
        WHERE email = 'incremental.test.2001@example.invalid'
    ) OR EXISTS (
        SELECT 1 FROM public.products
        WHERE product_name = 'Incremental Test Product'
    ) THEN
        RAISE EXCEPTION 'Approved unique test values now conflict; aborting';
    END IF;

    INSERT INTO public.customers (
        customer_id, customer_name, email, city, state, created_at, updated_at
    ) VALUES (
        2001, 'Incremental Test Customer',
        'incremental.test.2001@example.invalid', 'Test City', 'Test State',
        test_ts, test_ts
    );

    INSERT INTO public.products (
        product_id, product_name, category, brand, unit_price,
        created_at, updated_at
    ) VALUES (
        301, 'Incremental Test Product', 'Test Category', 'Test Brand', 1.00,
        test_ts, test_ts
    );

    INSERT INTO public.orders (
        order_id, customer_id, product_id, quantity, unit_price,
        order_status, created_at, updated_at
    ) VALUES (
        10001, 2001, 301, 1, 1.00, 'pending', test_ts, test_ts
    );

    INSERT INTO public.payments (
        payment_id, order_id, payment_amount, payment_method,
        payment_status, payment_time, updated_at
    ) VALUES (
        9991, 10001, 1.00, 'UPI', 'pending', test_ts, test_ts
    );

    IF (SELECT COUNT(*) FROM public.customers) <> 2001
       OR (SELECT COUNT(*) FROM public.products) <> 301
       OR (SELECT COUNT(*) FROM public.orders) <> 10001
       OR (SELECT COUNT(*) FROM public.payments) <> 9991 THEN
        RAISE EXCEPTION 'Post-insert counts are not exact; rolling back';
    END IF;

    IF (SELECT COUNT(*) FROM public.customers
        WHERE customer_id = 2001 AND updated_at = test_ts) <> 1
       OR (SELECT COUNT(*) FROM public.products
           WHERE product_id = 301 AND updated_at = test_ts) <> 1
       OR (SELECT COUNT(*) FROM public.orders
           WHERE order_id = 10001 AND customer_id = 2001
             AND product_id = 301 AND order_status = 'pending'
             AND updated_at = test_ts) <> 1
       OR (SELECT COUNT(*) FROM public.payments
           WHERE payment_id = 9991 AND order_id = 10001
             AND payment_status = 'pending' AND updated_at = test_ts) <> 1 THEN
        RAISE EXCEPTION 'Inserted rows failed exact-value verification; rolling back';
    END IF;

    RAISE NOTICE 'Controlled test timestamp: %', test_ts;
END
$controlled_test$;

COMMIT;

WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM public.payments AS p
), revenue AS (
    SELECT
        COUNT(*) AS confirmed_orders,
        COALESCE(SUM(lp.payment_amount), 0) AS confirmed_revenue
    FROM public.orders AS o
    INNER JOIN public.customers AS c ON c.customer_id = o.customer_id
    INNER JOIN public.products AS pr ON pr.product_id = o.product_id
    INNER JOIN latest_payment AS lp
        ON lp.order_id = o.order_id AND lp.payment_version = 1
    WHERE o.quantity > 0
      AND o.order_status = 'completed'
      AND lp.payment_status = 'successful'
      AND lp.payment_amount = o.quantity * o.unit_price
)
SELECT json_build_object(
    'counts_after', json_build_object(
        'customers', (SELECT COUNT(*) FROM public.customers),
        'products', (SELECT COUNT(*) FROM public.products),
        'orders', (SELECT COUNT(*) FROM public.orders),
        'payments', (SELECT COUNT(*) FROM public.payments)
    ),
    'test_ts', (SELECT updated_at FROM public.customers WHERE customer_id = 2001),
    'same_timestamp', (
        (SELECT updated_at FROM public.customers WHERE customer_id = 2001)
            = (SELECT updated_at FROM public.products WHERE product_id = 301)
        AND (SELECT updated_at FROM public.customers WHERE customer_id = 2001)
            = (SELECT updated_at FROM public.orders WHERE order_id = 10001)
        AND (SELECT updated_at FROM public.customers WHERE customer_id = 2001)
            = (SELECT updated_at FROM public.payments WHERE payment_id = 9991)
    ),
    'relationships_valid', (
        (SELECT customer_id = 2001 AND product_id = 301
         FROM public.orders WHERE order_id = 10001)
        AND (SELECT order_id = 10001
             FROM public.payments WHERE payment_id = 9991)
    ),
    'confirmed_orders', (SELECT confirmed_orders FROM revenue),
    'confirmed_revenue', (SELECT confirmed_revenue FROM revenue)
) FROM revenue;
