"""Validate raw order-to-revenue CSVs into curated and rejected layers.

The processor uses only rules supported by the live PostgreSQL schema and the
repository's existing confirmed-revenue definition. It never silently drops a
row: each raw row is written to either curated or rejected output.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable


TABLE_FIELDS = {
    "customers": [
        "customer_id", "customer_name", "email", "city", "state",
        "created_at", "updated_at",
    ],
    "products": [
        "product_id", "product_name", "category", "brand", "unit_price",
        "created_at", "updated_at",
    ],
    "orders": [
        "order_id", "customer_id", "product_id", "quantity", "unit_price",
        "order_status", "created_at", "updated_at",
    ],
    "payments": [
        "payment_id", "order_id", "payment_amount", "payment_method",
        "payment_status", "payment_time", "updated_at",
    ],
}

ALLOWED_ORDER_STATUSES = {"completed", "pending", "cancelled", "returned"}
ALLOWED_PAYMENT_METHODS = {"UPI", "Card", "Net Banking", "COD"}
ALLOWED_PAYMENT_STATUSES = {"successful", "failed", "pending", "refunded"}


@dataclass
class Record:
    row: dict[str, str]
    sequence: int
    reasons: list[str] = field(default_factory=list)
    typed: dict[str, object] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)


def require_text(record: Record, field_name: str) -> str | None:
    value = record.row[field_name].strip()
    if not value:
        record.reject(f"MISSING_REQUIRED_{field_name.upper()}")
        return None
    return value


def parse_int(record: Record, field_name: str, *, required: bool = True) -> int | None:
    raw = record.row[field_name].strip()
    if not raw:
        if required:
            record.reject(f"MISSING_REQUIRED_{field_name.upper()}")
        return None
    try:
        return int(raw)
    except ValueError:
        record.reject(f"INVALID_INTEGER_{field_name.upper()}")
        return None


def parse_decimal(record: Record, field_name: str) -> Decimal | None:
    raw = record.row[field_name].strip()
    if not raw:
        record.reject(f"MISSING_REQUIRED_{field_name.upper()}")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        record.reject(f"INVALID_DECIMAL_{field_name.upper()}")
        return None
    if not value.is_finite():
        record.reject(f"INVALID_DECIMAL_{field_name.upper()}")
        return None
    return value


def parse_timestamp(record: Record, field_name: str) -> datetime | None:
    raw = record.row[field_name].strip()
    if not raw:
        record.reject(f"MISSING_REQUIRED_{field_name.upper()}")
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        record.reject(f"INVALID_TIMESTAMP_{field_name.upper()}")
        return None
    # ADF's verified PostgreSQL-to-delimited-text conversion emits UTC source
    # timestamptz values without an offset (for example, seven fractional
    # digits). The source catalog and extraction session are UTC, so restore the
    # lost offset rather than rejecting every otherwise valid Azure raw row.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def locate_raw_file(raw_dir: Path, table_name: str) -> Path:
    candidates = [
        raw_dir / f"{table_name}.csv",
        raw_dir / table_name / f"{table_name}.csv",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Missing raw {table_name} file; checked: "
        + ", ".join(str(path) for path in candidates)
    )


def read_records(path: Path, table_name: str) -> list[Record]:
    expected = TABLE_FIELDS[table_name]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected:
            raise ValueError(
                f"{path}: expected header {expected}, found {reader.fieldnames}"
            )
        return [Record(dict(row), sequence) for sequence, row in enumerate(reader, 1)]


def validate_customers(records: list[Record]) -> None:
    for record in records:
        customer_id = parse_int(record, "customer_id")
        for field_name in ("customer_name", "email", "city", "state"):
            require_text(record, field_name)
        created_at = parse_timestamp(record, "created_at")
        updated_at = parse_timestamp(record, "updated_at")
        if customer_id is not None and customer_id <= 0:
            record.reject("INVALID_CUSTOMER_ID")
        if created_at and updated_at and updated_at < created_at:
            record.reject("UPDATED_AT_BEFORE_CREATED_AT")
        record.typed.update(
            customer_id=customer_id, created_at=created_at, updated_at=updated_at
        )


def validate_products(records: list[Record]) -> None:
    for record in records:
        product_id = parse_int(record, "product_id")
        for field_name in ("product_name", "category", "brand"):
            require_text(record, field_name)
        unit_price = parse_decimal(record, "unit_price")
        created_at = parse_timestamp(record, "created_at")
        updated_at = parse_timestamp(record, "updated_at")
        if product_id is not None and product_id <= 0:
            record.reject("INVALID_PRODUCT_ID")
        if unit_price is not None and unit_price <= 0:
            record.reject("NON_POSITIVE_UNIT_PRICE")
        if created_at and updated_at and updated_at < created_at:
            record.reject("UPDATED_AT_BEFORE_CREATED_AT")
        record.typed.update(
            product_id=product_id,
            unit_price=unit_price,
            created_at=created_at,
            updated_at=updated_at,
        )


def validate_orders(records: list[Record]) -> None:
    for record in records:
        order_id = parse_int(record, "order_id")
        customer_id = parse_int(record, "customer_id", required=False)
        product_id = parse_int(record, "product_id", required=False)
        quantity = parse_int(record, "quantity")
        unit_price = parse_decimal(record, "unit_price")
        status = require_text(record, "order_status")
        created_at = parse_timestamp(record, "created_at")
        updated_at = parse_timestamp(record, "updated_at")
        if order_id is not None and order_id <= 0:
            record.reject("INVALID_ORDER_ID")
        if customer_id is None:
            record.reject("MISSING_CUSTOMER_REFERENCE")
        if product_id is None:
            record.reject("MISSING_PRODUCT_REFERENCE")
        if quantity is not None and quantity <= 0:
            record.reject("INVALID_QUANTITY")
        if unit_price is not None and unit_price <= 0:
            record.reject("NON_POSITIVE_UNIT_PRICE")
        if status and status not in ALLOWED_ORDER_STATUSES:
            record.reject("INVALID_ORDER_STATUS")
        if created_at and updated_at and updated_at < created_at:
            record.reject("UPDATED_AT_BEFORE_CREATED_AT")
        record.typed.update(
            order_id=order_id,
            customer_id=customer_id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            order_status=status,
            created_at=created_at,
            updated_at=updated_at,
        )


def validate_payments(records: list[Record]) -> None:
    for record in records:
        payment_id = parse_int(record, "payment_id")
        order_id = parse_int(record, "order_id")
        amount = parse_decimal(record, "payment_amount")
        method = require_text(record, "payment_method")
        status = require_text(record, "payment_status")
        payment_time = parse_timestamp(record, "payment_time")
        updated_at = parse_timestamp(record, "updated_at")
        if payment_id is not None and payment_id <= 0:
            record.reject("INVALID_PAYMENT_ID")
        if order_id is not None and order_id <= 0:
            record.reject("INVALID_ORDER_REFERENCE")
        if amount is not None and amount <= 0:
            record.reject("NON_POSITIVE_PAYMENT_AMOUNT")
        if method and method not in ALLOWED_PAYMENT_METHODS:
            record.reject("INVALID_PAYMENT_METHOD")
        if status and status not in ALLOWED_PAYMENT_STATUSES:
            record.reject("INVALID_PAYMENT_STATUS")
        if payment_time and updated_at and updated_at < payment_time:
            record.reject("UPDATED_AT_BEFORE_PAYMENT_TIME")
        record.typed.update(
            payment_id=payment_id,
            order_id=order_id,
            payment_amount=amount,
            payment_method=method,
            payment_status=status,
            payment_time=payment_time,
            updated_at=updated_at,
        )


def reject_superseded(
    records: list[Record], id_field: str, timestamp_field: str
) -> None:
    groups: dict[int, list[Record]] = defaultdict(list)
    for record in records:
        record_id = record.typed.get(id_field)
        if isinstance(record_id, int):
            groups[record_id].append(record)
    for versions in groups.values():
        if len(versions) < 2:
            continue
        winner = max(
            versions,
            key=lambda item: (
                timestamp_score(item.typed.get(timestamp_field)),
                item.sequence,
            ),
        )
        for version in versions:
            if version is not winner:
                version.reject("SUPERSEDED_BY_LATER_VERSION")


def timestamp_score(value: object) -> float:
    return value.timestamp() if isinstance(value, datetime) else float("-inf")


def valid_id_set(records: list[Record], field_name: str) -> set[int]:
    return {
        value
        for record in records
        if not record.reasons
        if isinstance((value := record.typed.get(field_name)), int)
    }


def apply_relationship_rules(tables: dict[str, list[Record]]) -> None:
    customer_ids = valid_id_set(tables["customers"], "customer_id")
    product_ids = valid_id_set(tables["products"], "product_id")

    for order in tables["orders"]:
        customer_id = order.typed.get("customer_id")
        product_id = order.typed.get("product_id")
        if isinstance(customer_id, int) and customer_id not in customer_ids:
            order.reject("UNKNOWN_CUSTOMER_REFERENCE")
        if isinstance(product_id, int) and product_id not in product_ids:
            order.reject("UNKNOWN_PRODUCT_REFERENCE")

    raw_order_ids = {
        value
        for order in tables["orders"]
        if isinstance((value := order.typed.get("order_id")), int)
    }
    for payment in tables["payments"]:
        order_id = payment.typed.get("order_id")
        if isinstance(order_id, int) and order_id not in raw_order_ids:
            payment.reject("UNKNOWN_ORDER_REFERENCE")

    candidate_orders = {
        order.typed["order_id"]: order
        for order in tables["orders"]
        if not order.reasons and isinstance(order.typed.get("order_id"), int)
    }
    payments_by_order: dict[int, list[Record]] = defaultdict(list)
    for payment in tables["payments"]:
        order_id = payment.typed.get("order_id")
        if not payment.reasons and isinstance(order_id, int):
            payments_by_order[order_id].append(payment)

    for order_id, order in candidate_orders.items():
        attempts = payments_by_order.get(order_id, [])
        if not attempts:
            continue
        latest = max(
            attempts,
            key=lambda item: (
                timestamp_score(item.typed.get("payment_time")),
                item.typed.get("payment_id") or -1,
            ),
        )
        if latest.typed.get("payment_status") != "successful":
            continue
        amount = latest.typed.get("payment_amount")
        unit_price = order.typed.get("unit_price")
        quantity = order.typed.get("quantity")
        if (
            isinstance(amount, Decimal)
            and isinstance(unit_price, Decimal)
            and isinstance(quantity, int)
            and amount != unit_price * quantity
        ):
            order.reject("PAYMENT_AMOUNT_MISMATCH")
            latest.reject("PAYMENT_AMOUNT_MISMATCH")

    curated_order_ids = valid_id_set(tables["orders"], "order_id")
    for payment in tables["payments"]:
        order_id = payment.typed.get("order_id")
        if isinstance(order_id, int) and order_id not in curated_order_ids:
            payment.reject("ORDER_NOT_CURATED")


def write_table_outputs(
    table_name: str,
    records: list[Record],
    curated_dir: Path,
    rejected_dir: Path,
    *,
    overwrite: bool,
) -> dict:
    fields = TABLE_FIELDS[table_name]
    curated = [record for record in records if not record.reasons]
    rejected = [record for record in records if record.reasons]

    curated_path = curated_dir / f"{table_name}.csv"
    rejected_path = rejected_dir / f"{table_name}.csv"
    for output_path in (curated_path, rejected_path):
        if output_path.exists() and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite {output_path}; use --overwrite only for a known rerun target"
            )
    with curated_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in curated:
            normalized = dict(record.row)
            for field_name in fields:
                typed_value = record.typed.get(field_name)
                if isinstance(typed_value, datetime):
                    normalized[field_name] = typed_value.astimezone(timezone.utc).isoformat(
                        timespec="microseconds"
                    )
            writer.writerow(normalized)

    with rejected_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*fields, "rejection_reason"])
        writer.writeheader()
        for record in rejected:
            writer.writerow(
                {**record.row, "rejection_reason": ";".join(record.reasons)}
            )

    reason_counts: Counter[str] = Counter()
    for record in rejected:
        reason_counts.update(record.reasons)
    return {
        "raw_rows": len(records),
        "curated_rows": len(curated),
        "rejected_rows": len(rejected),
        "reconciled": len(records) == len(curated) + len(rejected),
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
    }


def process(
    raw_dir: Path,
    curated_dir: Path,
    rejected_dir: Path,
    *,
    overwrite: bool = False,
) -> dict:
    raw_dir = raw_dir.resolve()
    curated_dir = curated_dir.resolve()
    rejected_dir = rejected_dir.resolve()
    if len({raw_dir, curated_dir, rejected_dir}) != 3:
        raise ValueError("raw, curated, and rejected directories must be distinct")
    curated_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    tables = {
        table_name: read_records(locate_raw_file(raw_dir, table_name), table_name)
        for table_name in TABLE_FIELDS
    }
    validators: dict[str, Callable[[list[Record]], None]] = {
        "customers": validate_customers,
        "products": validate_products,
        "orders": validate_orders,
        "payments": validate_payments,
    }
    id_fields = {
        "customers": ("customer_id", "updated_at"),
        "products": ("product_id", "updated_at"),
        "orders": ("order_id", "updated_at"),
        "payments": ("payment_id", "updated_at"),
    }
    for table_name, records in tables.items():
        validators[table_name](records)
        reject_superseded(records, *id_fields[table_name])

    apply_relationship_rules(tables)
    table_summaries = {
        table_name: write_table_outputs(
            table_name,
            records,
            curated_dir,
            rejected_dir,
            overwrite=overwrite,
        )
        for table_name, records in tables.items()
    }
    status = "PASS" if all(item["reconciled"] for item in table_summaries.values()) else "FAIL"
    return {"status": status, "tables": table_summaries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--curated-dir", type=Path, required=True)
    parser.add_argument("--rejected-dir", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing files in the explicitly supplied output directories",
    )
    args = parser.parse_args()

    summary = process(
        args.raw_dir,
        args.curated_dir,
        args.rejected_dir,
        overwrite=args.overwrite,
    )
    rendered = json.dumps(summary, indent=2) + "\n"
    if args.summary_file:
        args.summary_file.parent.mkdir(parents=True, exist_ok=True)
        args.summary_file.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
