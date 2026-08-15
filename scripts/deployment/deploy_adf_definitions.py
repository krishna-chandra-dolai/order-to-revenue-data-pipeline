"""Validate and optionally publish the approved sanitized ADF definitions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADF_ROOT = REPO_ROOT / "azure" / "adf"
SNAPSHOT_ROOT = REPO_ROOT / "azure" / "deployment" / "snapshots"
SUBSCRIPTION_ID = "699ac1b5-f0fd-40eb-b865-b4b22eaf3dec"
RESOURCE_GROUP = "rg-order-revenue-dev"
FACTORY = "adf-order-revenue-26081401"
API_VERSION = "2018-06-01"
BASE_URL = (
    f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
    f"/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.DataFactory"
    f"/factories/{FACTORY}"
)
NEW_DATASETS = [
    "DS_PG_Products",
    "DS_PG_Orders",
    "DS_PG_Payments",
    "DS_ADLS_Products_Raw",
    "DS_ADLS_Orders_Raw",
    "DS_ADLS_Payments_Raw",
    "DS_PG_Query",
    "DS_ADLS_Incremental_Raw",
    "DS_ADLS_Watermark_Control",
]
PIPELINES = ["PL_Initial_Full_Load", "PL_Incremental_Load"]
EXISTING_DATASETS = ["DS_PG_Customers", "DS_ADLS_Customers_Raw"]
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


def load_definition(kind: str, name: str) -> dict:
    path = ADF_ROOT / kind / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("name") != name:
        raise ValueError(f"{path}: internal name does not match filename")
    if FORBIDDEN.search(path.read_text(encoding="utf-8")):
        raise ValueError(f"{path}: forbidden credential-like material")
    return payload


def validate_local() -> dict:
    snapshot = latest_snapshot()
    snapshot_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    live_dataset_names = {item["name"] for item in snapshot_manifest["datasets"]}
    conflicts = sorted(live_dataset_names.intersection(NEW_DATASETS))
    if conflicts:
        raise ValueError(f"Proposed new datasets conflict with snapshot: {conflicts}")

    datasets = {
        name: load_definition("datasets", name)
        for name in [*EXISTING_DATASETS, *NEW_DATASETS]
    }
    pipelines = {name: load_definition("pipelines", name) for name in PIPELINES}

    snapshot_pipeline = json.loads(
        (snapshot / "pipelines" / "PL_Initial_Full_Load.json").read_text(encoding="utf-8")
    )
    live_customer = next(
        activity
        for activity in snapshot_pipeline["properties"]["activities"]
        if activity["name"] == "Copy_Customers_To_Raw"
    )
    generated_customer = next(
        activity
        for activity in pipelines["PL_Initial_Full_Load"]["properties"]["activities"]
        if activity["name"] == "Copy_Customers_To_Raw"
    )
    if live_customer != generated_customer:
        raise ValueError("Generated Copy_Customers_To_Raw differs from the snapshot")

    for name in EXISTING_DATASETS:
        snapshot_dataset = json.loads(
            (snapshot / "datasets" / f"{name}.json").read_text(encoding="utf-8")
        )
        if snapshot_dataset["properties"] != datasets[name]["properties"]:
            raise ValueError(f"Generated {name} differs from the snapshot")

    dataset_names = set(datasets)
    linked_services = {"LS_AzurePostgreSQL_OrderRevenue", "LS_ADLS_OrderRevenue"}
    for payload in [*datasets.values(), *pipelines.values()]:
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("type") == "DatasetReference":
                    if value["referenceName"] not in dataset_names:
                        raise ValueError(f"Unknown dataset reference {value['referenceName']}")
                if value.get("type") == "LinkedServiceReference":
                    if value["referenceName"] not in linked_services:
                        raise ValueError(f"Unknown linked service {value['referenceName']}")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    return {
        "status": "PASS",
        "snapshot": str(snapshot),
        "newDatasets": NEW_DATASETS,
        "datasetConflicts": conflicts,
        "pipelines": PIPELINES,
        "customersActivityPreserved": True,
        "customersDatasetsPreserved": True,
        "linkedServicesReferencedOnly": sorted(linked_services),
    }


def publish_resource(kind: str, name: str, payload: dict, temp_dir: Path) -> dict:
    body_path = temp_dir / f"{kind}-{name}.json"
    body_path.write_text(
        json.dumps({"properties": payload["properties"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    url_kind = "datasets" if kind == "dataset" else "pipelines"
    url = f"{BASE_URL}/{url_kind}/{name}?api-version={API_VERSION}"
    run_az([
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
    ])
    deployed = run_az(["rest", "--method", "get", "--url", url, "-o", "json"])
    return {
        "name": name,
        "provisioningState": deployed.get("properties", {}).get("provisioningState"),
        "type": deployed.get("properties", {}).get("type"),
        "retrievedAfterPut": deployed.get("name") == name,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish the already approved definitions to the existing factory",
    )
    args = parser.parse_args()
    validation = validate_local()
    if not args.apply:
        print(json.dumps({"mode": "DRY_RUN", **validation}, indent=2))
        return

    results = {"mode": "APPLY", "validation": validation, "datasets": [], "pipelines": []}
    with tempfile.TemporaryDirectory(prefix="order-revenue-adf-deploy-") as temp:
        temp_dir = Path(temp)
        for name in NEW_DATASETS:
            results["datasets"].append(
                publish_resource("dataset", name, load_definition("datasets", name), temp_dir)
            )
        for name in PIPELINES:
            results["pipelines"].append(
                publish_resource("pipeline", name, load_definition("pipelines", name), temp_dir)
            )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
