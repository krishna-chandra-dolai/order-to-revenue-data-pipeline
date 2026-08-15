"""Apply and verify the deterministic incremental batch in one transaction.

Authentication is delegated to psql. No password is read or stored by Python.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "generated"
QUERY_DIR = REPO_ROOT / "database" / "queries"
PIPELINE_NAME = "order_payment_incremental"


def psql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def count_operations(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            counts[row["operation"]] = counts.get(row["operation"], 0) + 1
    return counts


def build_sql(summary: dict) -> str:
    old_watermark = summary["old_watermark"]
    planned_new_watermark = summary["planned_new_watermark"]
    order_path = psql_path(DATA_DIR / "incremental_orders.csv")
    payment_path = psql_path(DATA_DIR / "incremental_payments.csv")
    watermark_sql_path = psql_path(QUERY_DIR / "05_incremental_load_queries.sql")

    return f"""\
\\set ON_ERROR_STOP on
SET TIME ZONE 'UTC';
BEGIN;

-- With no watermark variables set, this include creates only the control table.
\\i '{watermark_sql_path}'

INSERT INTO pipeline_watermark (
    pipeline_name,
    last_successful_watermark,
    last_run_completed_at,
    last_order_rows,
    last_payment_rows
)
VALUES (
    '{PIPELINE_NAME}',
    TIMESTAMPTZ '{old_watermark}',
    NULL,
    10000,
    9990
)
ON CONFLICT (pipeline_name) DO NOTHING;

DO $check$
DECLARE
    actual_watermark TIMESTAMPTZ;
    actual_orders BIGINT;
    actual_payments BIGINT;
BEGIN
    SELECT last_successful_watermark INTO actual_watermark
    FROM pipeline_watermark
    WHERE pipeline_name = '{PIPELINE_NAME}';

    SELECT COUNT(*) INTO actual_orders FROM orders;
    SELECT COUNT(*) INTO actual_payments FROM payments;

    IF actual_watermark <> TIMESTAMPTZ '{old_watermark}' THEN
        RAISE EXCEPTION 'Expected old watermark %, found %. Batch may already be processed.',
            TIMESTAMPTZ '{old_watermark}', actual_watermark;
    END IF;
    IF actual_orders <> 10000 OR actual_payments <> 9990 THEN
        RAISE EXCEPTION 'Expected Phase 1 counts 10000/9990, found %/%',
            actual_orders, actual_payments;
    END IF;
END
$check$;

SELECT last_successful_watermark AS old_watermark
FROM pipeline_watermark
WHERE pipeline_name = '{PIPELINE_NAME}'
\\gset

CREATE TEMP TABLE revenue_before AS
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM payments p
)
SELECT
    COUNT(*) AS confirmed_order_count,
    COALESCE(SUM(lp.payment_amount), 0) AS confirmed_revenue
FROM orders o
INNER JOIN customers c ON c.customer_id = o.customer_id
INNER JOIN products pr ON pr.product_id = o.product_id
INNER JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price;

CREATE TEMP TABLE stage_orders (
    operation VARCHAR(10) NOT NULL,
    order_id BIGINT NOT NULL,
    customer_id BIGINT NULL,
    product_id BIGINT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    order_status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TEMP TABLE stage_payments (
    operation VARCHAR(10) NOT NULL,
    payment_id BIGINT NOT NULL,
    order_id BIGINT NOT NULL,
    payment_amount NUMERIC(12, 2) NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    payment_status VARCHAR(20) NOT NULL,
    payment_time TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

\\copy stage_orders FROM '{order_path}' WITH (FORMAT CSV, HEADER TRUE, NULL '')
\\copy stage_payments FROM '{payment_path}' WITH (FORMAT CSV, HEADER TRUE)

DO $check$
BEGIN
    IF (SELECT COUNT(*) FROM stage_orders WHERE operation = 'new') <> 100
       OR (SELECT COUNT(*) FROM stage_orders WHERE operation = 'update') <> 20 THEN
        RAISE EXCEPTION 'Unexpected staged order operation counts';
    END IF;
    IF (SELECT COUNT(*) FROM stage_payments WHERE operation = 'new') <> 101
       OR (SELECT COUNT(*) FROM stage_payments WHERE operation = 'update') <> 20 THEN
        RAISE EXCEPTION 'Unexpected staged payment operation counts';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stage_orders s
        INNER JOIN orders o ON o.order_id = s.order_id
        WHERE s.operation = 'new'
    ) OR EXISTS (
        SELECT 1 FROM stage_orders s
        LEFT JOIN orders o ON o.order_id = s.order_id
        WHERE s.operation = 'update' AND o.order_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Order new/update operation does not match source state';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stage_payments s
        INNER JOIN payments p ON p.payment_id = s.payment_id
        WHERE s.operation = 'new'
    ) OR EXISTS (
        SELECT 1 FROM stage_payments s
        LEFT JOIN payments p ON p.payment_id = s.payment_id
        WHERE s.operation = 'update' AND p.payment_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Payment new/update operation does not match source state';
    END IF;
END
$check$;

-- Preserve the previous versions to model append-only raw lake history.
CREATE TEMP TABLE previous_order_versions AS
SELECT o.*
FROM orders o
INNER JOIN stage_orders s ON s.order_id = o.order_id
WHERE s.operation = 'update';

CREATE TEMP TABLE previous_payment_versions AS
SELECT p.*
FROM payments p
INNER JOIN stage_payments s ON s.payment_id = p.payment_id
WHERE s.operation = 'update';

INSERT INTO orders (
    order_id, customer_id, product_id, quantity, unit_price,
    order_status, created_at, updated_at
)
SELECT
    order_id, customer_id, product_id, quantity, unit_price,
    order_status, created_at, updated_at
FROM stage_orders
ON CONFLICT (order_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id,
    product_id = EXCLUDED.product_id,
    quantity = EXCLUDED.quantity,
    unit_price = EXCLUDED.unit_price,
    order_status = EXCLUDED.order_status,
    created_at = EXCLUDED.created_at,
    updated_at = EXCLUDED.updated_at;

INSERT INTO payments (
    payment_id, order_id, payment_amount, payment_method,
    payment_status, payment_time, updated_at
)
SELECT
    payment_id, order_id, payment_amount, payment_method,
    payment_status, payment_time, updated_at
FROM stage_payments
ON CONFLICT (payment_id) DO UPDATE SET
    order_id = EXCLUDED.order_id,
    payment_amount = EXCLUDED.payment_amount,
    payment_method = EXCLUDED.payment_method,
    payment_status = EXCLUDED.payment_status,
    payment_time = EXCLUDED.payment_time,
    updated_at = EXCLUDED.updated_at;

SELECT GREATEST(
    (SELECT MAX(updated_at) FROM orders),
    (SELECT MAX(updated_at) FROM payments)
) AS new_watermark
\\gset

-- A false condition deliberately produces division by zero and rolls back.
SELECT 1 / CASE
    WHEN :'new_watermark'::timestamptz = TIMESTAMPTZ '{planned_new_watermark}' THEN 1
    ELSE 0
END AS new_watermark_is_expected;

-- With both variables present, this creates the two bounded incremental sets.
\\i '{watermark_sql_path}'

DO $check$
BEGIN
    IF (SELECT COUNT(*) FROM incremental_orders) <> 120
       OR (SELECT COUNT(*) FROM incremental_payments) <> 121 THEN
        RAISE EXCEPTION 'Incremental extraction counts do not match 120/121';
    END IF;
    IF (SELECT COUNT(*) FROM incremental_orders) >= (SELECT COUNT(*) FROM orders) THEN
        RAISE EXCEPTION 'Order extraction unexpectedly selected the full source';
    END IF;
END
$check$;

CREATE TEMP TABLE raw_order_versions AS
SELECT * FROM previous_order_versions
UNION ALL
SELECT * FROM incremental_orders;

CREATE TEMP TABLE ranked_order_versions AS
SELECT
    r.*,
    ROW_NUMBER() OVER (
        PARTITION BY r.order_id
        ORDER BY r.updated_at DESC
    ) AS version_rank
FROM raw_order_versions r;

CREATE TEMP TABLE latest_incremental_orders AS
SELECT
    order_id, customer_id, product_id, quantity, unit_price,
    order_status, created_at, updated_at
FROM ranked_order_versions
WHERE version_rank = 1;

CREATE TEMP TABLE raw_payment_versions AS
SELECT * FROM previous_payment_versions
UNION ALL
SELECT * FROM incremental_payments;

CREATE TEMP TABLE ranked_payment_versions AS
SELECT
    r.*,
    ROW_NUMBER() OVER (
        PARTITION BY r.payment_id
        ORDER BY r.updated_at DESC
    ) AS version_rank
FROM raw_payment_versions r;

CREATE TEMP TABLE latest_incremental_payments AS
SELECT
    payment_id, order_id, payment_amount, payment_method,
    payment_status, payment_time, updated_at
FROM ranked_payment_versions
WHERE version_rank = 1;

CREATE TEMP TABLE affected_order_ids AS
SELECT order_id FROM incremental_orders
UNION
SELECT order_id FROM incremental_payments;

CREATE TEMP TABLE incremental_reconciliation AS
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
    a.order_id,
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
    END AS reconciliation_result
FROM affected_order_ids a
INNER JOIN orders o ON o.order_id = a.order_id
LEFT JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN products pr ON pr.product_id = o.product_id
LEFT JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1;

CREATE TEMP TABLE revenue_after AS
WITH latest_payment AS (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.order_id
               ORDER BY p.payment_time DESC, p.payment_id DESC
           ) AS payment_version
    FROM payments p
)
SELECT
    COUNT(*) AS confirmed_order_count,
    COALESCE(SUM(lp.payment_amount), 0) AS confirmed_revenue
FROM orders o
INNER JOIN customers c ON c.customer_id = o.customer_id
INNER JOIN products pr ON pr.product_id = o.product_id
INNER JOIN latest_payment lp
    ON lp.order_id = o.order_id AND lp.payment_version = 1
WHERE o.quantity > 0
  AND o.order_status = 'completed'
  AND lp.payment_status = 'successful'
  AND lp.payment_amount = o.quantity * o.unit_price;

UPDATE pipeline_watermark
SET last_successful_watermark = :'new_watermark'::timestamptz,
    last_run_completed_at = clock_timestamp(),
    last_order_rows = (SELECT COUNT(*) FROM incremental_orders),
    last_payment_rows = (SELECT COUNT(*) FROM incremental_payments)
WHERE pipeline_name = '{PIPELINE_NAME}';

COMMIT;

SELECT :'old_watermark'::timestamptz AS old_watermark,
       :'new_watermark'::timestamptz AS new_watermark;

SELECT
    COUNT(*) FILTER (WHERE operation = 'new') AS new_orders,
    COUNT(*) FILTER (WHERE operation = 'update') AS updated_orders,
    (SELECT COUNT(*) FROM incremental_orders) AS incremental_orders_selected
FROM stage_orders;

SELECT
    COUNT(*) FILTER (WHERE operation = 'new') AS new_payments,
    COUNT(*) FILTER (WHERE operation = 'update') AS updated_payments,
    (SELECT COUNT(*) FROM incremental_payments) AS incremental_payments_selected
FROM stage_payments;

SELECT
    (SELECT confirmed_order_count FROM revenue_before) AS confirmed_orders_before,
    (SELECT confirmed_order_count FROM revenue_after) AS confirmed_orders_after,
    (SELECT confirmed_order_count FROM revenue_after)
        - (SELECT confirmed_order_count FROM revenue_before) AS confirmed_order_change,
    (SELECT confirmed_revenue FROM revenue_before) AS confirmed_revenue_before,
    (SELECT confirmed_revenue FROM revenue_after) AS confirmed_revenue_after,
    (SELECT confirmed_revenue FROM revenue_after)
        - (SELECT confirmed_revenue FROM revenue_before) AS confirmed_revenue_change;

SELECT reconciliation_result, COUNT(*) AS affected_order_count
FROM incremental_reconciliation
GROUP BY reconciliation_result
ORDER BY reconciliation_result;

SELECT
    (SELECT COUNT(*) FROM raw_order_versions) AS raw_order_versions,
    (SELECT COUNT(*) FROM latest_incremental_orders) AS latest_order_versions,
    (SELECT COUNT(*) FROM (
        SELECT order_id FROM raw_order_versions GROUP BY order_id HAVING COUNT(*) > 1
    ) duplicates) AS updated_order_ids_with_two_versions,
    (SELECT COUNT(*) FROM raw_payment_versions) AS raw_payment_versions,
    (SELECT COUNT(*) FROM latest_incremental_payments) AS latest_payment_versions,
    (SELECT COUNT(*) FROM (
        SELECT payment_id FROM raw_payment_versions GROUP BY payment_id HAVING COUNT(*) > 1
    ) duplicates) AS updated_payment_ids_with_two_versions;

SELECT
    COUNT(*) AS late_payments_reconciling_existing_orders
FROM stage_payments
WHERE operation = 'new'
  AND order_id <= 10000;

SELECT
    pipeline_name,
    last_successful_watermark,
    last_order_rows,
    last_payment_rows
FROM pipeline_watermark
WHERE pipeline_name = '{PIPELINE_NAME}';
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-file", type=Path, help="Optional non-sensitive psql result file")
    parser.add_argument(
        "--allow-source-mutation",
        action="store_true",
        help="Acknowledge that this legacy simulation upserts source orders/payments",
    )
    args = parser.parse_args()

    if not shutil.which("psql"):
        raise RuntimeError("psql must be available on PATH")
    summary_path = DATA_DIR / "incremental_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            "Run python -m scripts.ingestion.add_incremental_batch first"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if count_operations(DATA_DIR / "incremental_orders.csv") != {"update": 20, "new": 100}:
        raise ValueError("Incremental order CSV operation counts are invalid")
    if count_operations(DATA_DIR / "incremental_payments.csv") != {"update": 20, "new": 101}:
        raise ValueError("Incremental payment CSV operation counts are invalid")

    database = os.getenv("PGDATABASE", "order_revenue_db")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise ValueError("PGDATABASE must be a simple PostgreSQL identifier")
    host = os.getenv("PGHOST", "localhost")
    if host.lower() not in {"localhost", "127.0.0.1", "::1"} and not args.allow_source_mutation:
        raise RuntimeError(
            "Refusing to mutate a remote source. The deployment design uses ADF read-only extracts; "
            "use --allow-source-mutation only with separate explicit authorization."
        )
    command = [
        "psql",
        "-h", host,
        "-p", os.getenv("PGPORT", "5432"),
        "-U", os.getenv("PGUSER", "postgres"),
        "-d", database,
        "-X",
        "-v", "ON_ERROR_STOP=1",
    ]
    if args.output_file:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["-o", str(args.output_file.resolve())])
    subprocess.run(command, input=build_sql(summary), text=True, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(
            "Incremental run failed and its transaction was rolled back. Authenticate only "
            "through psql or a user-level environment mechanism; never commit a password.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode) from exc
