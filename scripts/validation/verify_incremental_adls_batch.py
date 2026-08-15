"""Verify one timestamped incremental ADLS batch using Azure RBAC."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transformation.process_raw_to_curated import TABLE_FIELDS
from scripts.validation.inspect_adls_counts import access_token, read_blob


PRIMARY_KEYS = {
    "customers": "customer_id",
    "products": "product_id",
    "orders": "order_id",
    "payments": "payment_id",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watermark-path", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument(
        "--expected-keys",
        help="Comma-separated table=key pairs, for example customers=2001",
    )
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    expected_keys = {}
    if args.expected_keys:
        expected_keys = dict(
            item.split("=", 1) for item in args.expected_keys.split(",")
        )
    token = access_token()
    tables: dict[str, dict] = {}
    failures: list[str] = []

    for table_name, fields in TABLE_FIELDS.items():
        name = (
            f"{table_name}/incremental/{args.watermark_path}/"
            f"{table_name}.csv"
        )
        content = read_blob(token, "raw", name)
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline="")))
        header = list(rows[0].keys()) if rows else list(
            csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        )[0]
        primary_key = PRIMARY_KEYS[table_name]
        keys = [row[primary_key] for row in rows]
        updated_values = [row["updated_at"] for row in rows]
        if header != fields:
            failures.append(f"{table_name}: unexpected header")
        if len(rows) != args.expected_rows:
            failures.append(
                f"{table_name}: expected {args.expected_rows} rows, found {len(rows)}"
            )
        if table_name in expected_keys and keys != [str(expected_keys[table_name])]:
            failures.append(
                f"{table_name}: expected key {expected_keys[table_name]}, found {keys}"
            )
        tables[table_name] = {
            "blob": name,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "dataRows": len(rows),
            "primaryKeys": keys,
            "updatedAt": updated_values,
        }

    control_content = read_blob(token, "raw", "_control/watermarks.csv")
    control_rows = list(
        csv.DictReader(
            io.StringIO(control_content.decode("utf-8-sig"), newline="")
        )
    )
    if len(control_rows) != 1:
        failures.append(f"control: expected one row, found {len(control_rows)}")
        control = {}
    else:
        control = control_rows[0]
        if control.get("last_successful_run_id") != args.expected_run_id:
            failures.append(
                "control: last_successful_run_id does not match expected run"
            )

    result = {
        "status": "PASS" if not failures else "FAIL",
        "authentication": "Azure RBAC",
        "watermarkPath": args.watermark_path,
        "expectedRunId": args.expected_run_id,
        "tables": tables,
        "currentWatermarks": control,
        "failures": failures,
    }
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
