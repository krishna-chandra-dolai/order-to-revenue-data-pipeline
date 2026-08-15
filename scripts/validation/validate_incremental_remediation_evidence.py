"""Cross-check the final incremental remediation evidence set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "docs" / "evidence"
TABLES = {
    "customers": "2001",
    "products": "301",
    "orders": "10001",
    "payments": "9991",
}
TEST_WATERMARK = "2026-08-15 03:25:37.527343+00"


def load(name: str) -> dict:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def activity_map(run: dict) -> dict[str, dict]:
    return {activity["activityName"]: activity for activity in run["activities"]}


def copy_counts(run: dict) -> dict[str, int]:
    activities = activity_map(run)
    return {
        table: activities[f"Copy_{table.title()}_Incremental_To_Raw"]["rowsCopied"]
        for table in TABLES
    }


def lookup_counts(run: dict) -> dict[str, int]:
    activities = activity_map(run)
    return {
        table: activities[f"Lookup_{table.title()}_Window"]["firstRow"]["expected_rows"]
        for table in TABLES
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()

    deployment = load("incremental-sink-remediation-deployment.json")
    first_run = load("incremental-remediation-first-run.json")
    second_run = load("incremental-remediation-second-run.json")
    first_batch = load("incremental-remediation-first-batch.json")
    first_after = load("incremental-remediation-first-batch-after-second.json")
    second_batch = load("incremental-remediation-second-batch.json")
    source = load("final-source-idempotency-check.json")

    failures: list[str] = []
    first_id = first_run["runId"]
    second_id = second_run["runId"]
    if deployment["deployment"]["status"] != "PASS":
        failures.append("deployment did not pass")
    if not deployment["validation"]["onlyFunctionalAdfChange"]:
        failures.append("deployment validation found another functional ADF change")
    if deployment["validation"]["functionalDifferenceCount"] != 4:
        failures.append("deployment did not contain exactly four sink-path changes")
    if first_run["status"] != "Succeeded" or second_run["status"] != "Succeeded":
        failures.append("one or both retest runs did not succeed")
    if first_id == second_id:
        failures.append("retest run IDs are not unique")
    if copy_counts(first_run) != {table: 1 for table in TABLES}:
        failures.append("first run copy counts are not all one")
    if lookup_counts(first_run) != {table: 1 for table in TABLES}:
        failures.append("first run source-window counts are not all one")
    if copy_counts(second_run) != {table: 0 for table in TABLES}:
        failures.append("second run copy counts are not all zero")
    if lookup_counts(second_run) != {table: 0 for table in TABLES}:
        failures.append("second run source-window counts are not all zero")

    unchanged = {}
    separated = {}
    for table, expected_key in TABLES.items():
        before = first_batch["tables"][table]
        after = first_after["tables"][table]
        second = second_batch["tables"][table]
        unchanged[table] = (
            before["blob"] == after["blob"]
            and before["bytes"] == after["bytes"]
            and before["sha256"] == after["sha256"]
            and before["primaryKeys"] == after["primaryKeys"] == [expected_key]
            and before["dataRows"] == after["dataRows"] == 1
        )
        separated[table] = (
            f"run_id={first_id}" in before["blob"]
            and f"run_id={second_id}" in second["blob"]
            and before["blob"] != second["blob"]
            and second["dataRows"] == 0
        )
        if not unchanged[table]:
            failures.append(f"{table}: first-run blob changed after second run")
        if not separated[table]:
            failures.append(f"{table}: second-run output is not separate")

    final_control = second_batch["currentWatermarks"]
    watermark_fields = [f"{table}_watermark" for table in TABLES]
    watermarks_correct = all(
        final_control[field] == TEST_WATERMARK for field in watermark_fields
    ) and final_control["last_successful_run_id"] == second_id
    if not watermarks_correct:
        failures.append("final watermarks or final successful run ID are incorrect")

    expected_totals = {
        "customers": 2001,
        "products": 301,
        "orders": 10001,
        "payments": 9991,
    }
    actual_totals = {
        table: source["tables"][table]["total_rows"] for table in TABLES
    }
    source_idempotent = actual_totals == expected_totals and all(
        source["tables"][table]["expected_rows"] == 1
        and source["tables"][table]["recovery_rows"] == 1
        and source["tables"][table]["unexpected_ids"] == []
        for table in TABLES
    )
    if not source_idempotent:
        failures.append("source counts or synthetic-row invariants changed")

    result = {
        "status": "PASS" if not failures else "FAIL",
        "firstRunId": first_id,
        "secondRunId": second_id,
        "firstCounts": copy_counts(first_run),
        "secondCounts": copy_counts(second_run),
        "runIdsUnique": first_id != second_id,
        "firstRunFilesUnchanged": unchanged,
        "secondRunFilesSeparate": separated,
        "appendOnlyRetention": all(unchanged.values()) and all(separated.values()),
        "watermarkIdempotency": watermarks_correct,
        "sourceIdempotency": source_idempotent,
        "finalSourceCounts": actual_totals,
        "confirmedOrders": source["confirmed_orders"],
        "confirmedRevenue": source["confirmed_revenue"],
        "failures": failures,
    }
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
