-- 1. Source row counts
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL
SELECT 'products', COUNT(*) FROM products
UNION ALL
SELECT 'orders', COUNT(*) FROM orders
UNION ALL
SELECT 'payments', COUNT(*) FROM payments
ORDER BY table_name;

-- 2. Confirmed revenue by month and month-over-month change.
-- ROW_NUMBER makes the rule safe if an order later has more than one payment attempt.
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
        ON lp.order_id = o.order_id
       AND lp.payment_version = 1
    WHERE o.quantity > 0
      AND o.order_status = 'completed'
      AND lp.payment_status = 'successful'
      AND lp.payment_amount = o.quantity * o.unit_price
    GROUP BY DATE_TRUNC('month', o.created_at AT TIME ZONE 'UTC')::date
), revenue_with_previous AS (
    SELECT
        revenue_month,
        confirmed_revenue,
        LAG(confirmed_revenue) OVER (ORDER BY revenue_month) AS previous_month_revenue
    FROM monthly_revenue
)
SELECT
    revenue_month,
    confirmed_revenue,
    previous_month_revenue,
    ROUND(
        100.0 * (confirmed_revenue - previous_month_revenue)
        / NULLIF(previous_month_revenue, 0),
        2
    ) AS month_over_month_percent
FROM revenue_with_previous
ORDER BY revenue_month;

-- 3. Top products within each category by confirmed revenue.
WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
), product_revenue AS (
    SELECT
        pr.category,
        pr.product_id,
        pr.product_name,
        SUM(p.payment_amount) AS confirmed_revenue
    FROM products pr
    INNER JOIN orders o ON o.product_id = pr.product_id
    INNER JOIN latest_payment p
        ON p.order_id = o.order_id
       AND p.payment_version = 1
    WHERE o.quantity > 0
      AND o.order_status = 'completed'
      AND p.payment_status = 'successful'
      AND p.payment_amount = o.quantity * o.unit_price
    GROUP BY pr.category, pr.product_id, pr.product_name
), ranked_products AS (
    SELECT
        *,
        RANK() OVER (
            PARTITION BY category
            ORDER BY confirmed_revenue DESC
        ) AS revenue_rank
    FROM product_revenue
)
SELECT category, product_name, confirmed_revenue, revenue_rank
FROM ranked_products
WHERE revenue_rank <= 3
ORDER BY category, revenue_rank, product_name;

-- 4. Customer spending, including customers who have no confirmed revenue.
WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
), customer_spend AS (
    SELECT
        c.customer_id,
        c.customer_name,
        COUNT(p.payment_id) FILTER (
            WHERE o.order_status = 'completed'
              AND p.payment_status = 'successful'
              AND o.quantity > 0
              AND p.payment_amount = o.quantity * o.unit_price
        ) AS confirmed_order_count,
        COALESCE(SUM(
            CASE
                WHEN o.order_status = 'completed'
                 AND p.payment_status = 'successful'
                 AND o.quantity > 0
                 AND p.payment_amount = o.quantity * o.unit_price
                THEN p.payment_amount
                ELSE 0
            END
        ), 0) AS lifetime_spend
    FROM customers c
    LEFT JOIN orders o ON o.customer_id = c.customer_id
    LEFT JOIN latest_payment p
        ON p.order_id = o.order_id
       AND p.payment_version = 1
    GROUP BY c.customer_id, c.customer_name
)
SELECT
    customer_id,
    customer_name,
    confirmed_order_count,
    lifetime_spend,
    DENSE_RANK() OVER (ORDER BY lifetime_spend DESC) AS spending_rank
FROM customer_spend
ORDER BY spending_rank, customer_id
LIMIT 20;

-- 5. Payment and reconciliation summary.
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
    COALESCE(p.payment_status, 'no_payment') AS payment_outcome,
    COUNT(*) AS order_count,
    SUM(CASE WHEN p.payment_status = 'successful' THEN p.payment_amount ELSE 0 END) AS recorded_amount
FROM orders o
LEFT JOIN latest_payment p
    ON p.order_id = o.order_id
   AND p.payment_version = 1
GROUP BY COALESCE(p.payment_status, 'no_payment')
HAVING COUNT(*) > 0
ORDER BY order_count DESC;
