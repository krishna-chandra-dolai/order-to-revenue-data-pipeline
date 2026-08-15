"""Run lightweight, dependency-free checks against generated CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ingestion.generate_data import EXPECTED_ANOMALIES


EXPECTED_ROWS = {
    "customers": 2_000,
    "products": 300,
    "orders": 10_000,
    "payments": 9_990,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    default_dir = Path(__file__).resolve().parents[2] / "data" / "generated"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_dir)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()

    customers = read_csv(data_dir / "customers.csv")
    products = read_csv(data_dir / "products.csv")
    orders = read_csv(data_dir / "orders.csv")
    payments = read_csv(data_dir / "payments.csv")
    manifest = read_csv(data_dir / "anomaly_manifest.csv")
    summary = json.loads((data_dir / "generation_summary.json").read_text(encoding="utf-8"))

    tables = {"customers": customers, "products": products, "orders": orders, "payments": payments}
    for table_name, expected in EXPECTED_ROWS.items():
        require(len(tables[table_name]) == expected, f"{table_name}: expected {expected} rows")

    require(summary["synthetic_data"] is True, "summary must label the data synthetic")
    require(summary["row_counts"] == EXPECTED_ROWS, "summary row counts do not match")
    require(len({row["customer_id"] for row in customers}) == len(customers), "duplicate customer_id")
    require(len({row["email"] for row in customers}) == len(customers), "duplicate customer email")
    require(len({row["product_id"] for row in products}) == len(products), "duplicate product_id")
    require(len({row["product_name"] for row in products}) == len(products), "duplicate product name")
    require(len({row["order_id"] for row in orders}) == len(orders), "duplicate order_id")
    require(len({row["payment_id"] for row in payments}) == len(payments), "duplicate payment_id")

    customer_ids = {row["customer_id"] for row in customers}
    product_ids = {row["product_id"] for row in products}
    order_by_id = {row["order_id"]: row for row in orders}
    payment_by_order = {row["order_id"]: row for row in payments}
    require(all(not row["customer_id"] or row["customer_id"] in customer_ids for row in orders),
            "non-null customer reference does not exist")
    require(all(not row["product_id"] or row["product_id"] in product_ids for row in orders),
            "non-null product reference does not exist")
    require(all(row["order_id"] in order_by_id for row in payments), "payment references unknown order")

    manifest_counts = Counter(row["scenario_type"] for row in manifest)
    require(dict(manifest_counts) == EXPECTED_ANOMALIES, "anomaly manifest counts do not match")
    require(len(manifest) == 92, "expected 92 controlled scenarios")

    for entry in manifest:
        scenario = entry["scenario_type"]
        order = order_by_id[entry["order_id"]]
        payment = payment_by_order.get(entry["order_id"])
        if scenario == "invalid_quantity":
            require(int(order["quantity"]) <= 0, f"invalid quantity case failed for {entry['order_id']}")
        elif scenario == "missing_customer_reference":
            require(order["customer_id"] == "", f"missing customer case failed for {entry['order_id']}")
        elif scenario == "missing_product_reference":
            require(order["product_id"] == "", f"missing product case failed for {entry['order_id']}")
        elif scenario == "payment_amount_mismatch":
            expected = Decimal(order["unit_price"]) * Decimal(order["quantity"])
            require(payment is not None and Decimal(payment["payment_amount"]) != expected,
                    f"amount mismatch case failed for {entry['order_id']}")
        elif scenario == "failed_payment":
            require(payment is not None and payment["payment_status"] == "failed",
                    f"failed payment case failed for {entry['order_id']}")
        elif scenario == "late_arriving_payment":
            delay = datetime.fromisoformat(payment["payment_time"]) - datetime.fromisoformat(order["created_at"])
            require(delay >= timedelta(days=8),
                    f"late payment case failed for {entry['order_id']}")
        elif scenario == "missing_payment":
            require(payment is None, f"missing payment case failed for {entry['order_id']}")

    confirmed_orders = 0
    confirmed_revenue = Decimal("0")
    for order in orders:
        payment = payment_by_order.get(order["order_id"])
        expected_amount = Decimal(order["unit_price"]) * Decimal(order["quantity"])
        is_confirmed = (
            bool(order["customer_id"])
            and bool(order["product_id"])
            and int(order["quantity"]) > 0
            and order["order_status"] == "completed"
            and payment is not None
            and payment["payment_status"] == "successful"
            and Decimal(payment["payment_amount"]) == expected_amount
        )
        if is_confirmed:
            confirmed_orders += 1
            confirmed_revenue += Decimal(payment["payment_amount"])

    result = {
        "status": "PASS",
        "row_counts": EXPECTED_ROWS,
        "controlled_scenarios": dict(sorted(manifest_counts.items())),
        "confirmed_orders": confirmed_orders,
        "confirmed_revenue": f"{confirmed_revenue:.2f}",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
