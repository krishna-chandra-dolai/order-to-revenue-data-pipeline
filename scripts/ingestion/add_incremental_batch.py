"""Generate a deterministic second source batch from the Phase 1 CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


SEED = 20260815
NEW_ORDER_COUNT = 100
UPDATED_ORDER_COUNT = 20
LATE_PAYMENTS_FOR_EXISTING_ORDERS = 4

NEW_SCENARIOS = {
    "invalid_quantity": {10001, 10002},
    "missing_customer_reference": {10003, 10004},
    "missing_product_reference": {10005, 10006},
    "payment_amount_mismatch": {10007, 10008, 10009},
    "failed_payment": {10010, 10011, 10012},
    "missing_payment": {10013, 10014, 10015},
    "late_arriving_payment": {10016, 10017},
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def money(value: Decimal | str | int) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"


def generate(data_dir: Path) -> dict:
    rng = random.Random(SEED)
    customers = read_csv(data_dir / "customers.csv")
    products = read_csv(data_dir / "products.csv")
    orders = read_csv(data_dir / "orders.csv")
    payments = read_csv(data_dir / "payments.csv")
    initial_manifest = read_csv(data_dir / "anomaly_manifest.csv")

    order_by_id = {int(row["order_id"]): row for row in orders}
    payment_by_order = {int(row["order_id"]): row for row in payments}
    product_by_id = {int(row["product_id"]): row for row in products}
    baseline_watermark = max(
        datetime.fromisoformat(row["updated_at"])
        for row in orders + payments
    )
    batch_start = baseline_watermark + timedelta(days=1)

    initial_mismatch_ids = sorted(
        int(row["order_id"])
        for row in initial_manifest
        if row["scenario_type"] == "payment_amount_mismatch"
    )
    pending_candidates = [
        int(row["order_id"])
        for row in orders
        if row["order_status"] == "pending"
        and row["customer_id"]
        and row["product_id"]
        and int(row["order_id"]) in payment_by_order
        and payment_by_order[int(row["order_id"])]["payment_status"] == "pending"
    ]
    pending_updates = sorted(rng.sample(pending_candidates, 10))
    mismatch_updates = sorted(rng.sample(initial_mismatch_ids, 5))
    excluded = set(pending_updates) | set(mismatch_updates)
    valid_candidates = [
        int(row["order_id"])
        for row in orders
        if int(row["order_id"]) not in excluded
        and row["customer_id"]
        and row["product_id"]
        and int(row["quantity"]) > 0
        and row["order_status"] == "completed"
        and int(row["order_id"]) in payment_by_order
        and payment_by_order[int(row["order_id"])]["payment_status"] == "successful"
        and Decimal(payment_by_order[int(row["order_id"])]["payment_amount"])
            == Decimal(row["unit_price"]) * int(row["quantity"])
    ]
    customer_corrections = sorted(rng.sample(valid_candidates, 5))

    incremental_orders: list[dict] = []
    incremental_payments: list[dict] = []
    manifest: list[dict] = []

    methods = ["UPI", "Card", "Net Banking", "COD"]
    update_groups = [
        (pending_updates, "pending_to_completed"),
        (customer_corrections, "customer_assignment_correction"),
        (mismatch_updates, "quantity_and_payment_correction"),
    ]
    update_sequence = 0
    for order_ids, change_type in update_groups:
        for order_id in order_ids:
            update_sequence += 1
            order = dict(order_by_id[order_id])
            payment = dict(payment_by_order[order_id])
            changed_at = batch_start + timedelta(minutes=update_sequence * 10)
            if change_type == "pending_to_completed":
                order["order_status"] = "completed"
                payment["payment_status"] = "successful"
            elif change_type == "customer_assignment_correction":
                old_customer = int(order["customer_id"])
                order["customer_id"] = (old_customer % len(customers)) + 1
                payment["payment_method"] = methods[(methods.index(payment["payment_method"]) + 1) % len(methods)]
            else:
                order["quantity"] = int(order["quantity"]) + 1
                payment["payment_amount"] = money(
                    Decimal(order["unit_price"]) * int(order["quantity"])
                )
            order["updated_at"] = iso(changed_at)
            payment["updated_at"] = iso(changed_at + timedelta(minutes=1))
            incremental_orders.append({"operation": "update", **order})
            incremental_payments.append({"operation": "update", **payment})
            manifest.append({
                "record_type": "order_and_payment_update",
                "record_id": order_id,
                "scenario_type": change_type,
                "description": "Existing order and payment receive a later updated_at version.",
            })

    next_payment_id = max(int(row["payment_id"]) for row in payments) + 1
    new_order_ids = list(range(10_001, 10_001 + NEW_ORDER_COUNT))
    forced_completed = set().union(*NEW_SCENARIOS.values())
    for offset, order_id in enumerate(new_order_ids):
        customer_id = rng.randint(1, len(customers))
        product_id = rng.randint(1, len(products))
        product_price = Decimal(product_by_id[product_id]["unit_price"])
        quantity = rng.choices([1, 2, 3, 4], weights=[56, 27, 12, 5], k=1)[0]
        if order_id in NEW_SCENARIOS["invalid_quantity"]:
            quantity = 0 if order_id % 2 else -1

        customer_value: int | str = customer_id
        product_value: int | str = product_id
        if order_id in NEW_SCENARIOS["missing_customer_reference"]:
            customer_value = ""
        if order_id in NEW_SCENARIOS["missing_product_reference"]:
            product_value = ""

        status = "completed" if order_id in forced_completed else rng.choices(
            ["completed", "pending", "cancelled", "returned"],
            weights=[90, 3, 4, 3],
            k=1,
        )[0]
        created_at = batch_start + timedelta(minutes=offset * 45)
        order_updated_at = created_at + timedelta(minutes=5 + offset % 20)
        order = {
            "order_id": order_id,
            "customer_id": customer_value,
            "product_id": product_value,
            "quantity": quantity,
            "unit_price": money(product_price),
            "order_status": status,
            "created_at": iso(created_at),
            "updated_at": iso(order_updated_at),
        }
        incremental_orders.append({"operation": "new", **order})

        scenario = next(
            (name for name, ids in NEW_SCENARIOS.items() if order_id in ids),
            "standard_new_order",
        )
        if scenario != "standard_new_order":
            manifest.append({
                "record_type": "new_order",
                "record_id": order_id,
                "scenario_type": scenario,
                "description": "Controlled scenario introduced in the incremental batch.",
            })

        if order_id in NEW_SCENARIOS["missing_payment"]:
            continue

        if order_id in NEW_SCENARIOS["failed_payment"]:
            payment_status = "failed"
        elif status in {"cancelled", "returned"}:
            payment_status = "refunded"
        elif status == "pending":
            payment_status = "pending"
        else:
            payment_status = "successful"

        expected_amount = product_price * max(quantity, 1)
        payment_amount = expected_amount
        if order_id in NEW_SCENARIOS["payment_amount_mismatch"]:
            payment_amount += Decimal(75 + (order_id % 4) * 25)

        if order_id in NEW_SCENARIOS["late_arriving_payment"]:
            payment_time = created_at + timedelta(days=8 + order_id % 2, hours=2)
        else:
            payment_time = created_at + timedelta(hours=1 + offset % 24)
        payment = {
            "payment_id": next_payment_id,
            "order_id": order_id,
            "payment_amount": money(payment_amount),
            "payment_method": methods[offset % len(methods)],
            "payment_status": payment_status,
            "payment_time": iso(payment_time),
            "updated_at": iso(payment_time + timedelta(minutes=10)),
        }
        incremental_payments.append({"operation": "new", **payment})
        next_payment_id += 1

    missing_payment_ids = sorted(set(order_by_id) - set(payment_by_order))
    for offset, order_id in enumerate(missing_payment_ids[:LATE_PAYMENTS_FOR_EXISTING_ORDERS]):
        order = order_by_id[order_id]
        payment_time = batch_start + timedelta(days=2, hours=offset)
        incremental_payments.append({
            "operation": "new",
            "payment_id": next_payment_id,
            "order_id": order_id,
            "payment_amount": money(Decimal(order["unit_price"]) * int(order["quantity"])),
            "payment_method": methods[(offset + 1) % len(methods)],
            "payment_status": "successful",
            "payment_time": iso(payment_time),
            "updated_at": iso(payment_time + timedelta(minutes=10)),
        })
        manifest.append({
            "record_type": "new_payment_for_existing_order",
            "record_id": order_id,
            "scenario_type": "late_arriving_payment_reconciled",
            "description": "Previously missing payment arrives and makes the old order reconcilable.",
        })
        next_payment_id += 1

    incremental_orders.sort(key=lambda row: int(row["order_id"]))
    incremental_payments.sort(key=lambda row: int(row["payment_id"]))
    manifest.sort(key=lambda row: (row["scenario_type"], int(row["record_id"])))

    order_fields = ["operation", "order_id", "customer_id", "product_id", "quantity",
                    "unit_price", "order_status", "created_at", "updated_at"]
    payment_fields = ["operation", "payment_id", "order_id", "payment_amount",
                      "payment_method", "payment_status", "payment_time", "updated_at"]
    write_csv(data_dir / "incremental_orders.csv", order_fields, incremental_orders)
    write_csv(data_dir / "incremental_payments.csv", payment_fields, incremental_payments)
    write_csv(
        data_dir / "incremental_manifest.csv",
        ["record_type", "record_id", "scenario_type", "description"],
        manifest,
    )

    new_watermark = max(
        datetime.fromisoformat(row["updated_at"])
        for row in incremental_orders + incremental_payments
    )
    operation_counts = {
        "orders": dict(Counter(row["operation"] for row in incremental_orders)),
        "payments": dict(Counter(row["operation"] for row in incremental_payments)),
    }
    summary = {
        "synthetic_data": True,
        "seed": SEED,
        "old_watermark": iso(baseline_watermark),
        "planned_new_watermark": iso(new_watermark),
        "incremental_order_rows": len(incremental_orders),
        "incremental_payment_rows": len(incremental_payments),
        "operation_counts": operation_counts,
        "new_scenario_counts": {name: len(ids) for name, ids in NEW_SCENARIOS.items()},
        "late_arrivals_resolving_prior_missing_payments": LATE_PAYMENTS_FOR_EXISTING_ORDERS,
        "updated_order_change_counts": {
            "pending_to_completed": 10,
            "customer_assignment_correction": 5,
            "quantity_and_payment_correction": 5,
        },
    }

    assert len(incremental_orders) == 120
    assert operation_counts["orders"] == {"update": 20, "new": 100}
    assert operation_counts["payments"] == {"update": 20, "new": 101}
    assert all(datetime.fromisoformat(row["updated_at"]) > baseline_watermark
               for row in incremental_orders + incremental_payments)
    (data_dir / "incremental_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    default_dir = Path(__file__).resolve().parents[2] / "data" / "generated"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_dir)
    args = parser.parse_args()
    summary = generate(args.data_dir.resolve())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
