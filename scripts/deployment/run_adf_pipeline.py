"""Trigger an approved ADF pipeline run, poll it, and save safe run evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


SUBSCRIPTION_ID = "699ac1b5-f0fd-40eb-b865-b4b22eaf3dec"
RESOURCE_GROUP = "rg-order-revenue-dev"
FACTORY = "adf-order-revenue-26081401"
API_VERSION = "2018-06-01"
BASE_URL = (
    f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.DataFactory"
    f"/factories/{FACTORY}"
)
TERMINAL = {"Succeeded", "Failed", "Cancelled"}


def run_az(arguments: list[str]) -> dict:
    command = subprocess.list2cmdline(["az", *arguments, "--only-show-errors"])
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        shell=True,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def query_activity_runs(run_id: str, after: datetime, before: datetime) -> list[dict]:
    body = {"lastUpdatedAfter": iso(after), "lastUpdatedBefore": iso(before)}
    with tempfile.TemporaryDirectory(prefix="order-revenue-adf-query-") as temp:
        body_path = Path(temp) / "body.json"
        body_path.write_text(json.dumps(body), encoding="utf-8")
        result = run_az([
            "rest",
            "--method",
            "post",
            "--url",
            f"{BASE_URL}/pipelineruns/{run_id}/queryActivityRuns?api-version={API_VERSION}",
            "--body",
            f"@{body_path}",
            "--headers",
            "Content-Type=application/json",
            "-o",
            "json",
        ])
    safe = []
    for item in result.get("value", []):
        output = item.get("output") or {}
        error = item.get("error") or {}
        safe.append({
            "activityName": item.get("activityName"),
            "activityType": item.get("activityType"),
            "status": item.get("status"),
            "activityRunStart": item.get("activityRunStart"),
            "activityRunEnd": item.get("activityRunEnd"),
            "durationInMs": item.get("durationInMs"),
            "rowsRead": output.get("rowsRead"),
            "rowsCopied": output.get("rowsCopied"),
            "filesWritten": output.get("filesWritten"),
            "dataRead": output.get("dataRead"),
            "dataWritten": output.get("dataWritten"),
            "firstRow": output.get("firstRow"),
            "errorCode": error.get("errorCode"),
            "errorMessage": error.get("message"),
        })
    return sorted(safe, key=lambda item: item["activityRunStart"] or "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", choices=["PL_Initial_Full_Load", "PL_Incremental_Load"])
    parser.add_argument("--approve-run", action="store_true")
    parser.add_argument("--run-id", help="Monitor an existing run without triggering another")
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if not args.run_id and not args.approve_run:
        raise RuntimeError("Pipeline execution requires --approve-run")

    requested_at = datetime.now(timezone.utc)
    if args.run_id:
        run_id = args.run_id
        print(json.dumps({"pipeline": args.pipeline, "runId": run_id, "status": "Monitoring"}), flush=True)
    else:
        created = run_az([
            "rest",
            "--method",
            "post",
            "--url",
            f"{BASE_URL}/pipelines/{args.pipeline}/createRun?api-version={API_VERSION}",
            "-o",
            "json",
        ])
        run_id = created.get("runId")
        if not run_id:
            raise RuntimeError("ADF did not return a pipeline run ID")
        print(json.dumps({"pipeline": args.pipeline, "runId": run_id, "status": "Queued"}), flush=True)

    deadline = time.monotonic() + args.timeout_seconds
    last_status = None
    details: dict = {}
    while time.monotonic() < deadline:
        details = run_az([
            "rest",
            "--method",
            "get",
            "--url",
            f"{BASE_URL}/pipelineruns/{run_id}?api-version={API_VERSION}",
            "-o",
            "json",
        ])
        status = details.get("status")
        if status != last_status:
            print(json.dumps({"pipeline": args.pipeline, "runId": run_id, "status": status}), flush=True)
            last_status = status
        if status in TERMINAL:
            break
        time.sleep(5)
    else:
        raise TimeoutError(f"Pipeline {run_id} did not finish within {args.timeout_seconds}s")

    finished_at = datetime.now(timezone.utc)
    evidence = {
        "pipeline": args.pipeline,
        "runId": run_id,
        "status": details.get("status"),
        "runStart": details.get("runStart"),
        "runEnd": details.get("runEnd"),
        "durationInMs": details.get("durationInMs"),
        "message": details.get("message"),
        "activities": query_activity_runs(
            run_id,
            requested_at - timedelta(minutes=2),
            finished_at + timedelta(minutes=2),
        ),
    }
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    if evidence["status"] != "Succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
