"""Validate and publish only the approved incremental sink-path remediation."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADF_ROOT = REPO_ROOT / "azure" / "adf"
SNAPSHOT_ROOT = REPO_ROOT / "azure" / "deployment" / "snapshots"
SUBSCRIPTION_ID = "699ac1b5-f0fd-40eb-b865-b4b22eaf3dec"
RESOURCE_GROUP = "rg-order-revenue-dev"
FACTORY = "adf-order-revenue-26081401"
PIPELINE = "PL_Incremental_Load"
API_VERSION = "2018-06-01"
BASE_URL = (
    f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.DataFactory"
    f"/factories/{FACTORY}"
)
TABLES = ("Customers", "Products", "Orders", "Payments")
FORBIDDEN = re.compile(
    r"(?i)(defaultendpointsprotocol|accountkey\s*=|password\s*[=:]|client_secret|private key)"
)


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


def latest_snapshot() -> Path:
    candidates = sorted(SNAPSHOT_ROOT.glob("pre-deployment-*"))
    if not candidates:
        raise FileNotFoundError("No pre-deployment ADF snapshot exists")
    return candidates[-1]


def comparable_properties(payload: dict) -> dict:
    properties = copy.deepcopy(payload["properties"])
    properties.pop("lastPublishTime", None)
    return properties


def differences(before: object, after: object, path: str = "") -> list[dict]:
    if type(before) is not type(after):
        return [{"path": path, "live": before, "local": after}]
    if isinstance(before, dict):
        result: list[dict] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}"
            if key not in before:
                result.append({"path": child, "live": "<MISSING>", "local": after[key]})
            elif key not in after:
                result.append({"path": child, "live": before[key], "local": "<MISSING>"})
            else:
                result.extend(differences(before[key], after[key], child))
        return result
    if isinstance(before, list):
        if len(before) != len(after):
            return [{"path": f"{path}/length", "live": len(before), "local": len(after)}]
        result = []
        for index, (old_item, new_item) in enumerate(zip(before, after)):
            result.extend(differences(old_item, new_item, f"{path}/{index}"))
        return result
    return [] if before == after else [{"path": path, "live": before, "local": after}]


def validate() -> tuple[dict, dict]:
    snapshot = latest_snapshot()
    local_path = ADF_ROOT / "pipelines" / f"{PIPELINE}.json"
    local_text = local_path.read_text(encoding="utf-8")
    if FORBIDDEN.search(local_text):
        raise ValueError(f"{local_path}: forbidden credential-like material")
    local = json.loads(local_text)
    if local.get("name") != PIPELINE:
        raise ValueError("Local pipeline name does not match its filename")

    all_differences: list[dict] = []
    for kind in ("datasets", "pipelines"):
        for local_resource in sorted((ADF_ROOT / kind).glob("*.json")):
            snapshot_resource = snapshot / kind / local_resource.name
            if not snapshot_resource.exists():
                raise ValueError(f"Local ADF resource is absent from live snapshot: {local_resource}")
            local_payload = json.loads(local_resource.read_text(encoding="utf-8"))
            live_payload = json.loads(snapshot_resource.read_text(encoding="utf-8"))
            for item in differences(
                comparable_properties(live_payload), comparable_properties(local_payload)
            ):
                item["resource"] = f"{kind}/{local_resource.name}"
                all_differences.append(item)

    live = json.loads(
        (snapshot / "pipelines" / f"{PIPELINE}.json").read_text(encoding="utf-8")
    )
    live_activities = {
        item["name"]: item for item in live["properties"]["activities"]
    }
    local_activities = {
        item["name"]: item for item in local["properties"]["activities"]
    }
    expected = []
    for table in TABLES:
        activity_name = f"Copy_{table}_Incremental_To_Raw"
        old_value = live_activities[activity_name]["outputs"][0]["parameters"][
            "watermarkPath"
        ]["value"]
        new_value = local_activities[activity_name]["outputs"][0]["parameters"][
            "watermarkPath"
        ]["value"]
        expected_old = (
            "@concat('watermark=', formatDateTime(activity('Lookup_"
            f"{table}_Window').output.firstRow.new_watermark, "
            "'yyyyMMddTHHmmssfffffffZ'))"
        )
        expected_new = expected_old[:-1] + ", '/run_id=', pipeline().RunId)"
        if old_value != expected_old:
            raise ValueError(f"Unexpected live sink path for {activity_name}: {old_value}")
        if new_value != expected_new:
            raise ValueError(f"Unexpected local sink path for {activity_name}: {new_value}")
        expected.append(
            {
                "activity": activity_name,
                "live": old_value,
                "approved": new_value,
            }
        )

    if len(all_differences) != 4:
        raise ValueError(
            f"Expected exactly four functional ADF differences, found {len(all_differences)}"
        )
    if {item["resource"] for item in all_differences} != {
        f"pipelines/{PIPELINE}.json"
    }:
        raise ValueError("A functional ADF change exists outside PL_Incremental_Load")
    approved_values = {item["approved"] for item in expected}
    if {item["local"] for item in all_differences} != approved_values:
        raise ValueError("Functional differences are not exactly the approved sink paths")

    result = {
        "status": "PASS",
        "validatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(snapshot),
        "pipeline": PIPELINE,
        "functionalDifferenceCount": len(all_differences),
        "onlyFunctionalAdfChange": True,
        "approvedSinkPaths": expected,
        "differences": all_differences,
        "publishScope": [f"pipeline:{PIPELINE}"],
    }
    return result, local


def publish(local: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="order-revenue-incremental-remediation-") as temp:
        body_path = Path(temp) / f"{PIPELINE}.json"
        body_path.write_text(
            json.dumps({"properties": local["properties"]}, indent=2) + "\n",
            encoding="utf-8",
        )
        url = f"{BASE_URL}/pipelines/{PIPELINE}?api-version={API_VERSION}"
        run_az(
            [
                "rest",
                "--method",
                "put",
                "--url",
                url,
                "--body",
                f"@{body_path}",
                "--headers",
                "Content-Type=application/json",
                "-o",
                "json",
            ]
        )
        deployed = run_az(["rest", "--method", "get", "--url", url, "-o", "json"])

    deployed_properties = comparable_properties(deployed)
    local_properties = comparable_properties(local)
    post_publish_differences = differences(local_properties, deployed_properties)
    if post_publish_differences:
        raise RuntimeError(
            f"Deployed pipeline does not match approved local definition: {post_publish_differences}"
        )
    return {
        "status": "PASS",
        "pipeline": PIPELINE,
        "retrievedAfterPut": deployed.get("name") == PIPELINE,
        "provisioningState": deployed.get("properties", {}).get("provisioningState"),
        "deployedMatchesApprovedDefinition": True,
        "postPublishDifferences": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-file", type=Path)
    args = parser.parse_args()

    validation, local = validate()
    result = {
        "mode": "APPLY" if args.apply else "VALIDATE_ONLY",
        "validation": validation,
    }
    if args.apply:
        result["deployment"] = publish(local)
    if args.output_file:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
