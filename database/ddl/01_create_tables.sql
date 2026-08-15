BEGIN;

CREATE TABLE IF NOT EXISTS customers (
    customer_id BIGINT PRIMARY KEY,
    customer_name VARCHAR(120) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    city VARCHAR(80) NOT NULL,
    state VARCHAR(80) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT customers_updated_after_created CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS products (
    product_id BIGINT PRIMARY KEY,
    product_name VARCHAR(180) NOT NULL UNIQUE,
    category VARCHAR(60) NOT NULL,
    brand VARCHAR(60) NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT products_positive_price CHECK (unit_price > 0),
    CONSTRAINT products_updated_after_created CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NULL,
    product_id BIGINT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    order_status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT orders_product_fk FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT orders_positive_price CHECK (unit_price > 0),
    CONSTRAINT orders_status_allowed CHECK (order_status IN ('completed', 'pending', 'cancelled', 'returned')),
    CONSTRAINT orders_updated_after_created CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    payment_amount NUMERIC(12, 2) NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    payment_status VARCHAR(20) NOT NULL,
    payment_time TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT payments_order_fk FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT payments_positive_amount CHECK (payment_amount > 0),
    CONSTRAINT payments_method_allowed CHECK (payment_method IN ('UPI', 'Card', 'Net Banking', 'COD')),
    CONSTRAINT payments_status_allowed CHECK (payment_status IN ('successful', 'failed', 'pending', 'refunded')),
    CONSTRAINT payments_updated_after_payment CHECK (updated_at >= payment_time)
);

COMMIT;

