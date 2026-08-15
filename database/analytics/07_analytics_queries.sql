-- Read-only order-to-revenue analytics against the verified public schema.
-- Confirmed revenue uses the latest payment attempt for each order.

-- 1. Executive KPI summary.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
), classified AS (
    SELECT
        o.order_id,
        CASE
            WHEN o.quantity > 0
             AND o.order_status = 'completed'
             AND c.customer_id IS NOT NULL
             AND pr.product_id IS NOT NULL
             AND lp.payment_status = 'successful'
             AND lp.payment_amount = o.quantity * o.unit_price
            THEN lp.payment_amount
            ELSE 0::numeric
        END AS confirmed_revenue
    FROM public.orders o
    LEFT JOIN public.customers c ON c.customer_id = o.customer_id
    LEFT JOIN public.products pr ON pr.product_id = o.product_id
    LEFT JOIN latest_payment lp
      ON lp.order_id = o.order_id AND lp.payment_version = 1
)
SELECT
    COUNT(*) AS total_orders,
    COUNT(*) FILTER (WHERE confirmed_revenue > 0) AS confirmed_orders,
    COALESCE(SUM(confirmed_revenue), 0) AS total_confirmed_revenue,
    ROUND(
        COALESCE(SUM(confirmed_revenue), 0)
        / NULLIF(COUNT(*) FILTER (WHERE confirmed_revenue > 0), 0),
        2
    ) AS average_confirmed_order_value
FROM classified;

-- 2. Daily confirmed revenue.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
)
SELECT
    (o.created_at AT TIME ZONE 'UTC')::date AS order_date,
    COUNT(*) AS confirmed_orders,
    SUM(lp.payment_amount) AS confirmed_revenue
FROM public.orders o
JOIN public.customers c ON c.customer_id = o.customer_id
JOIN public.products pr ON pr.product_id = o.product_id
JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price
GROUP BY (o.created_at AT TIME ZONE 'UTC')::date
ORDER BY order_date;

-- 3. Product and category performance.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
)
SELECT
    pr.category,
    pr.product_id,
    pr.product_name,
    COUNT(*) AS confirmed_orders,
    SUM(o.quantity) AS units_sold,
    SUM(lp.payment_amount) AS confirmed_revenue,
    DENSE_RANK() OVER (
        PARTITION BY pr.category
        ORDER BY SUM(lp.payment_amount) DESC
    ) AS category_revenue_rank
FROM public.products pr
JOIN public.orders o ON o.product_id = pr.product_id
JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price
GROUP BY pr.category, pr.product_id, pr.product_name
ORDER BY pr.category, category_revenue_rank, pr.product_id;

-- 4. Customer and location performance.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
)
SELECT
    c.customer_id,
    c.customer_name,
    c.city,
    c.state,
    COUNT(*) AS confirmed_orders,
    SUM(lp.payment_amount) AS confirmed_revenue,
    DENSE_RANK() OVER (ORDER BY SUM(lp.payment_amount) DESC) AS customer_revenue_rank
FROM public.customers c
JOIN public.orders o ON o.customer_id = c.customer_id
JOIN public.products pr ON pr.product_id = o.product_id
JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price
GROUP BY c.customer_id, c.customer_name, c.city, c.state
ORDER BY customer_revenue_rank, c.customer_id;

-- 5. Order-status distribution and nominal order value.
SELECT
    order_status,
    COUNT(*) AS order_count,
    SUM(quantity * unit_price) FILTER (WHERE quantity > 0) AS nominal_order_value
FROM public.orders
GROUP BY order_status
ORDER BY order_count DESC, order_status;

-- 6. Latest payment-status distribution.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
)
SELECT
    COALESCE(lp.payment_status, 'no_payment') AS payment_status,
    COUNT(*) AS order_count,
    COALESCE(SUM(lp.payment_amount), 0) AS recorded_amount
FROM public.orders o
LEFT JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
GROUP BY COALESCE(lp.payment_status, 'no_payment')
ORDER BY order_count DESC, payment_status;

-- 7. Outstanding/unpaid orders. A failed, pending, or missing latest payment is
-- operationally outstanding; cancelled/returned orders are excluded.
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM public.payments p
)
SELECT
    o.order_id,
    o.order_status,
    o.quantity * o.unit_price AS expected_amount,
    COALESCE(lp.payment_status, 'no_payment') AS latest_payment_status,
    lp.payment_amount AS latest_payment_amount,
    o.created_at
FROM public.orders o
LEFT JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.order_status NOT IN ('cancelled', 'returned')
  AND (lp.payment_id IS NULL OR lp.payment_status IN ('failed', 'pending'))
ORDER BY o.created_at, o.order_id;
