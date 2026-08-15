-- One readable classification per order. The checks are intentionally ordered:
-- a structurally invalid order should not also be counted as an amount mismatch.
WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
), classified AS (
    SELECT
        o.order_id,
        CASE
            WHEN o.quantity <= 0 THEN 'INVALID_QUANTITY'
            WHEN c.customer_id IS NULL THEN 'MISSING_CUSTOMER_REFERENCE'
            WHEN pr.product_id IS NULL THEN 'MISSING_PRODUCT_REFERENCE'
            WHEN lp.payment_id IS NULL THEN 'NO_PAYMENT'
            WHEN lp.payment_status = 'failed' THEN 'FAILED_PAYMENT'
            WHEN lp.payment_status = 'pending' THEN 'PENDING_PAYMENT'
            WHEN lp.payment_status = 'refunded' THEN 'REFUNDED_PAYMENT'
            WHEN lp.payment_amount <> o.quantity * o.unit_price THEN 'PAYMENT_AMOUNT_MISMATCH'
            WHEN o.order_status <> 'completed' THEN 'ORDER_NOT_COMPLETED'
            ELSE 'CONFIRMED_REVENUE'
        END AS quality_result
    FROM orders o
    LEFT JOIN customers c ON c.customer_id = o.customer_id
    LEFT JOIN products pr ON pr.product_id = o.product_id
    LEFT JOIN latest_payment lp
        ON lp.order_id = o.order_id
       AND lp.payment_version = 1
)
SELECT quality_result, COUNT(*) AS order_count
FROM classified
GROUP BY quality_result
ORDER BY quality_result;

-- Detailed data-quality rejects retain a readable reason. Failed, pending,
-- refunded, missing, and late payments are business/reconciliation outcomes;
-- they are reported separately below instead of being called corrupt data.
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
    o.order_id,
    o.customer_id,
    o.product_id,
    o.quantity,
    o.unit_price,
    lp.payment_id,
    lp.payment_amount,
    CASE
        WHEN o.quantity <= 0 THEN 'quantity must be greater than zero'
        WHEN c.customer_id IS NULL THEN 'customer reference is missing'
        WHEN pr.product_id IS NULL THEN 'product reference is missing'
        WHEN lp.payment_status = 'successful'
         AND lp.payment_amount <> o.quantity * o.unit_price
            THEN 'successful payment amount does not match order amount'
        ELSE 'not rejected by a data-quality rule'
    END AS rejection_reason
FROM orders o
LEFT JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN products pr ON pr.product_id = o.product_id
LEFT JOIN latest_payment lp
    ON lp.order_id = o.order_id
   AND lp.payment_version = 1
WHERE o.quantity <= 0
   OR c.customer_id IS NULL
   OR pr.product_id IS NULL
   OR (
       lp.payment_status = 'successful'
       AND lp.payment_amount <> o.quantity * o.unit_price
   )
ORDER BY o.order_id;

-- Reconciliation outcomes that are valid events but do not represent confirmed
-- revenue, plus late successful payments that still can represent revenue.
WITH latest_payment AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (
            PARTITION BY p.order_id
            ORDER BY p.payment_time DESC, p.payment_id DESC
        ) AS payment_version
    FROM payments p
), reconciliation_cases AS (
    SELECT
        o.order_id,
        lp.payment_amount,
        CASE
            WHEN lp.payment_id IS NULL THEN 'payment has not arrived'
            WHEN lp.payment_status = 'failed' THEN 'failed payment; does not count as revenue'
            WHEN lp.payment_status = 'pending' THEN 'payment is still pending'
            WHEN lp.payment_status = 'refunded' THEN 'payment was refunded'
            WHEN lp.payment_time >= o.created_at + INTERVAL '8 days' THEN 'late successful payment'
        END AS reconciliation_note
    FROM orders o
    LEFT JOIN latest_payment lp
        ON lp.order_id = o.order_id
       AND lp.payment_version = 1
    WHERE lp.payment_id IS NULL
       OR lp.payment_status IN ('failed', 'pending', 'refunded')
       OR (
           lp.payment_status = 'successful'
           AND lp.payment_time >= o.created_at + INTERVAL '8 days'
       )
)
SELECT
    reconciliation_note,
    COUNT(*) AS order_count,
    COALESCE(SUM(payment_amount), 0) AS recorded_amount
FROM reconciliation_cases
GROUP BY reconciliation_note
ORDER BY reconciliation_note;

-- Operational primary keys should prevent these duplicates. Repeated versions
-- are expected only after incremental extracts are appended in the data lake.
SELECT order_id, COUNT(*) AS duplicate_count
FROM orders
GROUP BY order_id
HAVING COUNT(*) > 1;
