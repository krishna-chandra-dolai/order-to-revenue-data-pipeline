\pset tuples_only on
\pset format unaligned
WITH params AS (
  SELECT
    TIMESTAMPTZ '2024-03-28 04:31:20+00' AS customers_old,
    TIMESTAMPTZ '2023-05-29 00:00:00+00' AS products_old,
    TIMESTAMPTZ '2025-07-03 06:58:08+00' AS orders_old,
    TIMESTAMPTZ '2025-07-09 12:40:20+00' AS payments_old,
    TIMESTAMPTZ '2026-08-15 03:25:37.527343+00' AS test_ts
), stats AS (
  SELECT 'customers' AS table_name, COUNT(*) AS total_rows,
         MAX(updated_at) AS max_updated_at,
         COUNT(*) FILTER (WHERE updated_at > p.customers_old) AS recovery_rows,
         COUNT(*) FILTER (WHERE updated_at > p.customers_old AND customer_id = 2001 AND updated_at = p.test_ts) AS expected_rows,
         COALESCE(jsonb_agg(customer_id ORDER BY customer_id) FILTER (
           WHERE updated_at > p.customers_old AND NOT (customer_id = 2001 AND updated_at = p.test_ts)
         ), '[]'::jsonb) AS unexpected_ids
  FROM public.customers CROSS JOIN params p
  UNION ALL
  SELECT 'products', COUNT(*), MAX(updated_at),
         COUNT(*) FILTER (WHERE updated_at > p.products_old),
         COUNT(*) FILTER (WHERE updated_at > p.products_old AND product_id = 301 AND updated_at = p.test_ts),
         COALESCE(jsonb_agg(product_id ORDER BY product_id) FILTER (
           WHERE updated_at > p.products_old AND NOT (product_id = 301 AND updated_at = p.test_ts)
         ), '[]'::jsonb)
  FROM public.products CROSS JOIN params p
  UNION ALL
  SELECT 'orders', COUNT(*), MAX(updated_at),
         COUNT(*) FILTER (WHERE updated_at > p.orders_old),
         COUNT(*) FILTER (WHERE updated_at > p.orders_old AND order_id = 10001 AND updated_at = p.test_ts),
         COALESCE(jsonb_agg(order_id ORDER BY order_id) FILTER (
           WHERE updated_at > p.orders_old AND NOT (order_id = 10001 AND updated_at = p.test_ts)
         ), '[]'::jsonb)
  FROM public.orders CROSS JOIN params p
  UNION ALL
  SELECT 'payments', COUNT(*), MAX(updated_at),
         COUNT(*) FILTER (WHERE updated_at > p.payments_old),
         COUNT(*) FILTER (WHERE updated_at > p.payments_old AND payment_id = 9991 AND updated_at = p.test_ts),
         COALESCE(jsonb_agg(payment_id ORDER BY payment_id) FILTER (
           WHERE updated_at > p.payments_old AND NOT (payment_id = 9991 AND updated_at = p.test_ts)
         ), '[]'::jsonb)
  FROM public.payments CROSS JOIN params p
), source_rows AS (
  SELECT jsonb_build_object(
    'customer', (SELECT to_jsonb(c) FROM public.customers c WHERE customer_id = 2001),
    'product', (SELECT to_jsonb(pr) FROM public.products pr WHERE product_id = 301),
    'order', (SELECT to_jsonb(o) FROM public.orders o WHERE order_id = 10001),
    'payment', (SELECT to_jsonb(pay) FROM public.payments pay WHERE payment_id = 9991)
  ) AS rows
), latest_payment AS (
  SELECT pay.*,
         ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY payment_time DESC, payment_id DESC) AS payment_version
  FROM public.payments pay
), revenue AS (
  SELECT COUNT(*) AS confirmed_orders,
         COALESCE(SUM(lp.payment_amount), 0) AS confirmed_revenue
  FROM public.orders o
  JOIN public.customers c ON c.customer_id = o.customer_id
  JOIN public.products pr ON pr.product_id = o.product_id
  JOIN latest_payment lp ON lp.order_id = o.order_id AND lp.payment_version = 1
  WHERE o.quantity > 0
    AND o.order_status = 'completed'
    AND lp.payment_status = 'successful'
    AND lp.payment_amount = o.quantity * o.unit_price
)
SELECT jsonb_pretty(jsonb_build_object(
  'checked_at', clock_timestamp(),
  'documented_pre_test_watermarks', jsonb_build_object(
    'customers', (SELECT customers_old FROM params),
    'products', (SELECT products_old FROM params),
    'orders', (SELECT orders_old FROM params),
    'payments', (SELECT payments_old FROM params)
  ),
  'tables', (SELECT jsonb_object_agg(table_name, jsonb_build_object(
    'total_rows', total_rows,
    'max_updated_at', max_updated_at,
    'recovery_rows', recovery_rows,
    'expected_rows', expected_rows,
    'unexpected_ids', unexpected_ids
  )) FROM stats),
  'synthetic_rows', (SELECT rows FROM source_rows),
  'confirmed_orders', (SELECT confirmed_orders FROM revenue),
  'confirmed_revenue', (SELECT confirmed_revenue FROM revenue),
  'safe_to_reset', (SELECT bool_and(
    recovery_rows = 1 AND expected_rows = 1 AND jsonb_array_length(unexpected_ids) = 0
  ) FROM stats)
));
