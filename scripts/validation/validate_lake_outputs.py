"""Validate raw/curated/rejected CSV counts and output schemas.

The command prints a concise JSON result and returns a non-zero exit code for
missing files, schema mismatches, duplicate curated keys, or reconciliation
failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transformation.process_raw_to_curated import TABLE_FIELDS, locate_raw_file


PRIMARY_KEYS = {
    "customers": "customer_id",
    "products": "product_id",
    "orders": "order_id",
    "payments": "payment_id",
}


def inspect_csv(path: Path, expected_fields: list[str]) -> tuple[int, list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_fields:
            raise ValueError(
                f"{path}: expected header {expected_fields}, found {reader.fieldnames}"
            )
        rows = list(reader)
    return len(rows), rows


def validate(raw_dir: Path, curated_dir: Path, rejected_dir: Path) -> dict:
    failures: list[str] = []
    tables: dict[str, dict] = {}
    for table_name, fields in TABLE_FIELDS.items():
        raw_path = locate_raw_file(raw_dir, table_name)
        curated_path = curated_dir / f"{table_name}.csv"
        rejected_path = rejected_dir / f"{table_name}.csv"
        raw_count, _ = inspect_csv(raw_path, fields)
        curated_count, curated_rows = inspect_csv(curated_path, fields)
        rejected_count, rejected_rows = inspect_csv(
            rejected_path, [*fields, "rejection_reason"]
        )
        reconciled = raw_count == curated_count + rejected_count
        if not reconciled:
            failures.append(
                f"{table_name}: raw {raw_count} != curated {curated_count} + rejected {rejected_count}"
            )

        primary_key = PRIMARY_KEYS[table_name]
        curated_keys = [row[primary_key] for row in curated_rows]
        duplicate_curated_keys = len(curated_keys) - len(set(curated_keys))
        if duplicate_curated_keys:
            failures.append(
                f"{table_name}: {duplicate_curated_keys} duplicate curated primary keys"
            )
        missing_reasons = sum(not row["rejection_reason"].strip() for row in rejected_rows)
        if missing_reasons:
            failures.append(
                f"{table_name}: {missing_reasons} rejected rows have no reason"
            )
        tables[table_name] = {
            "raw_rows": raw_count,
            "curated_rows": curated_count,
            "rejected_rows": rejected_count,
            "reconciled": reconciled,
            "duplicate_curated_primary_keys": duplicate_curated_keys,
            "rejected_rows_without_reason": missing_reasons,
        }
    return {
        "status": "PASS" if not failures else "FAIL",
        "tables": tables,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--curated-dir", type=Path, required=True)
    parser.add_argument("--rejected-dir", type=Path, required=True)
    args = parser.parse_args()
    result = validate(
        args.raw_dir.resolve(),
        args.curated_dir.resolve(),
        args.rejected_dir.resolve(),
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
