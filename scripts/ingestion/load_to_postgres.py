"""Create/load order_revenue_db with psql without storing credentials."""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "database" / "ddl"
DATA_DIR = REPO_ROOT / "data" / "generated"


def connection_args(database: str) -> list[str]:
    return [
        "-h", os.getenv("PGHOST", "localhost"),
        "-p", os.getenv("PGPORT", "5432"),
        "-U", os.getenv("PGUSER", "postgres"),
        "-d", database,
    ]


def validate_csv_headers() -> None:
    expected_headers = {
        "customers.csv": ["customer_id", "customer_name", "email", "city", "state", "created_at", "updated_at"],
        "products.csv": ["product_id", "product_name", "category", "brand", "unit_price", "created_at", "updated_at"],
        "orders.csv": ["order_id", "customer_id", "product_id", "quantity", "unit_price", "order_status", "created_at", "updated_at"],
        "payments.csv": ["payment_id", "order_id", "payment_amount", "payment_method", "payment_status", "payment_time", "updated_at"],
    }
    for filename, expected in expected_headers.items():
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Run python -m scripts.ingestion.generate_data first."
            )
        with path.open(newline="", encoding="utf-8") as handle:
            actual = next(csv.reader(handle))
        if actual != expected:
            raise ValueError(f"Unexpected header in {filename}: {actual}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-destructive-reset",
        action="store_true",
        help="Acknowledge that the local development load truncates four tables",
    )
    args = parser.parse_args()
    if not shutil.which("psql"):
        raise RuntimeError("psql must be available on PATH")
    host = os.getenv("PGHOST", "localhost")
    if host.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            "Destructive reset is restricted to localhost; this script must not target Azure PostgreSQL"
        )
    if not args.allow_destructive_reset:
        raise RuntimeError(
            "This local loader truncates customers, products, orders, and payments. "
            "Rerun with --allow-destructive-reset only for a disposable local database."
        )
    validate_csv_headers()

    target_database = os.getenv("PGDATABASE", "order_revenue_db")
    maintenance_database = os.getenv("PGMAINTENANCE_DB", "postgres")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target_database):
        raise ValueError("PGDATABASE must be a simple PostgreSQL identifier")

    def psql_path(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "''")

    # One psql session means interactive users enter their password at most once.
    # \gexec creates the database only when it does not already exist.
    load_sql = f"""\
\\set ON_ERROR_STOP on
SELECT 'CREATE DATABASE "{target_database}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '{target_database}')
\\gexec
\\connect "{target_database}"
\\i '{psql_path(DDL_DIR / '01_create_tables.sql')}'
\\i '{psql_path(DDL_DIR / '02_indexes_constraints.sql')}'
BEGIN;
TRUNCATE TABLE payments, orders, products, customers;
\\copy customers (customer_id, customer_name, email, city, state, created_at, updated_at) FROM '{psql_path(DATA_DIR / 'customers.csv')}' WITH (FORMAT CSV, HEADER TRUE)
\\copy products (product_id, product_name, category, brand, unit_price, created_at, updated_at) FROM '{psql_path(DATA_DIR / 'products.csv')}' WITH (FORMAT CSV, HEADER TRUE)
\\copy orders (order_id, customer_id, product_id, quantity, unit_price, order_status, created_at, updated_at) FROM '{psql_path(DATA_DIR / 'orders.csv')}' WITH (FORMAT CSV, HEADER TRUE, NULL '')
\\copy payments (payment_id, order_id, payment_amount, payment_method, payment_status, payment_time, updated_at) FROM '{psql_path(DATA_DIR / 'payments.csv')}' WITH (FORMAT CSV, HEADER TRUE)
COMMIT;
ANALYZE customers;
ANALYZE products;
ANALYZE orders;
ANALYZE payments;
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL SELECT 'products', COUNT(*) FROM products
UNION ALL SELECT 'orders', COUNT(*) FROM orders
UNION ALL SELECT 'payments', COUNT(*) FROM payments
ORDER BY table_name;
"""
    subprocess.run(
        ["psql", *connection_args(maintenance_database), "-X", "-v", "ON_ERROR_STOP=1"],
        input=load_sql,
        text=True,
        check=True,
    )
    print(f"PostgreSQL load verification completed for {target_database}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(
            "PostgreSQL command failed. Authenticate interactively or configure a user-level "
            "pgpass file; do not add a password to this repository.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode) from exc
