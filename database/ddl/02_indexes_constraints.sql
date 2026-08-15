-- Primary keys, foreign keys, uniqueness, and value checks are defined with
-- their tables in 01_create_tables.sql. These indexes support incremental
-- extraction and the joins used by quality and reconciliation queries.

CREATE INDEX IF NOT EXISTS idx_customers_updated_at
    ON customers (updated_at, customer_id);

CREATE INDEX IF NOT EXISTS idx_products_updated_at
    ON products (updated_at, product_id);

CREATE INDEX IF NOT EXISTS idx_orders_updated_at
    ON orders (updated_at, order_id);

CREATE INDEX IF NOT EXISTS idx_orders_customer_id
    ON orders (customer_id);

CREATE INDEX IF NOT EXISTS idx_orders_product_id
    ON orders (product_id);

CREATE INDEX IF NOT EXISTS idx_payments_updated_at
    ON payments (updated_at, payment_id);

CREATE INDEX IF NOT EXISTS idx_payments_order_time
    ON payments (order_id, payment_time DESC, payment_id DESC);

