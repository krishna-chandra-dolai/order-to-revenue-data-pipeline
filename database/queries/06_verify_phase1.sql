-- Read-only Phase 1 verification for the persistent PostgreSQL database.
\pset pager off

SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL SELECT 'products', COUNT(*) FROM products
UNION ALL SELECT 'orders', COUNT(*) FROM orders
UNION ALL SELECT 'payments', COUNT(*) FROM payments
ORDER BY table_name;

WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
)
SELECT
    COUNT(*) AS confirmed_order_count,
    SUM(lp.payment_amount) AS confirmed_revenue
FROM orders o
INNER JOIN customers c ON c.customer_id = o.customer_id
INNER JOIN products pr ON pr.product_id = o.product_id
INNER JOIN latest_payment lp
    ON lp.order_id = o.order_id
   AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price;

WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
)
SELECT 'invalid_quantity' AS scenario_type, COUNT(*) AS scenario_count
FROM orders WHERE quantity <= 0
UNION ALL
SELECT 'missing_customer_reference', COUNT(*)
FROM orders WHERE customer_id IS NULL
UNION ALL
SELECT 'missing_product_reference', COUNT(*)
FROM orders WHERE product_id IS NULL
UNION ALL
SELECT 'payment_amount_mismatch', COUNT(*)
FROM orders o
INNER JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.customer_id IS NOT NULL
  AND o.product_id IS NOT NULL
  AND lp.payment_status = 'successful'
  AND lp.payment_amount <> o.quantity * o.unit_price
UNION ALL
SELECT 'failed_payment', COUNT(*)
FROM latest_payment WHERE payment_version = 1 AND payment_status = 'failed'
UNION ALL
SELECT 'late_arriving_payment', COUNT(*)
FROM orders o
INNER JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE lp.payment_status = 'successful'
  AND lp.payment_time >= o.created_at + INTERVAL '8 days'
UNION ALL
SELECT 'missing_payment', COUNT(*)
FROM orders o
LEFT JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE lp.payment_id IS NULL
ORDER BY scenario_type;

SELECT
    tc.table_name,
    kcu.column_name,
    ccu.table_name AS referenced_table,
    ccu.column_name AS referenced_column,
    tc.constraint_name
FROM information_schema.table_constraints tc
INNER JOIN information_schema.key_column_usage kcu
    ON kcu.constraint_name = tc.constraint_name
   AND kcu.constraint_schema = tc.constraint_schema
INNER JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name = tc.constraint_name
   AND ccu.constraint_schema = tc.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
  AND tc.table_name IN ('orders', 'payments')
ORDER BY tc.table_name, kcu.column_name;

SELECT
    column_name,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'orders'
  AND column_name IN ('customer_id', 'product_id')
ORDER BY column_name;

SELECT
    COUNT(*) FILTER (
        WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL
    ) AS invalid_non_null_customer_references,
    COUNT(*) FILTER (
        WHERE o.product_id IS NOT NULL AND pr.product_id IS NULL
    ) AS invalid_non_null_product_references
FROM orders o
LEFT JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN products pr ON pr.product_id = o.product_id;

WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
), monthly_revenue AS (
    SELECT
        DATE_TRUNC('month', o.created_at AT TIME ZONE 'UTC')::date AS revenue_month,
        SUM(lp.payment_amount) AS confirmed_revenue
    FROM orders o
    INNER JOIN customers c ON c.customer_id = o.customer_id
    INNER JOIN products pr ON pr.product_id = o.product_id
    INNER JOIN latest_payment lp
        ON lp.order_id = o.order_id AND lp.payment_version = 1
    WHERE o.quantity > 0
      AND o.order_status = 'completed'
      AND lp.payment_status = 'successful'
      AND lp.payment_amount = o.quantity * o.unit_price
    GROUP BY DATE_TRUNC('month', o.created_at AT TIME ZONE 'UTC')::date
)
SELECT
    COUNT(*) AS revenue_month_count,
    MIN(revenue_month) AS first_revenue_month,
    MAX(revenue_month) AS last_revenue_month,
    SUM(confirmed_revenue) AS revenue_across_months
FROM monthly_revenue;

WITH product_revenue AS (
    SELECT
        pr.category,
        pr.product_name,
        SUM(p.payment_amount) AS confirmed_revenue
    FROM products pr
    INNER JOIN orders o ON o.product_id = pr.product_id
    INNER JOIN payments p ON p.order_id = o.order_id
    WHERE o.quantity > 0
      AND o.order_status = 'completed'
      AND p.payment_status = 'successful'
      AND p.payment_amount = o.quantity * o.unit_price
    GROUP BY pr.category, pr.product_name
)
SELECT category, product_name, confirmed_revenue
FROM product_revenue
ORDER BY confirmed_revenue DESC, product_name
LIMIT 5;

SELECT
    COALESCE(p.payment_status, 'no_payment') AS payment_outcome,
    COUNT(*) AS order_count
FROM orders o
LEFT JOIN payments p ON p.order_id = o.order_id
GROUP BY COALESCE(p.payment_status, 'no_payment')
ORDER BY order_count DESC;

