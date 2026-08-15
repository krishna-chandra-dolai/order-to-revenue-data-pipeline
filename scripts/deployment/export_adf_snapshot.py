"""Export a pre-deployment ADF snapshot without persisting secret values."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
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
SENSITIVE_KEY = re.compile(
    r"(?i)(connection|string|password|secret|token|credential|accountkey|sas|privatekey)"
)


def az_get(url: str) -> dict:
    command = subprocess.list2cmdline(
        ["az", "rest", "--method", "get", "--url", url, "-o", "json", "--only-show-errors"]
    )
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        shell=True,
    )
    return json.loads(completed.stdout)


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize(value: object, key: str = "") -> object:
    if key and SENSITIVE_KEY.search(key):
        return {"redacted": True, "originalType": type(value).__name__}
    if isinstance(value, dict):
        return {item_key: sanitize(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    default_root = (
        Path(__file__).resolve().parents[2] / "azure" / "deployment" / "snapshots"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=default_root)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = args.output_root.resolve() / f"pre-deployment-{stamp}"
    if snapshot_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing snapshot {snapshot_dir}")

    factory = az_get(f"{BASE_URL}?api-version={API_VERSION}")
    pipeline_list = az_get(f"{BASE_URL}/pipelines?api-version={API_VERSION}").get("value", [])
    dataset_list = az_get(f"{BASE_URL}/datasets?api-version={API_VERSION}").get("value", [])
    linked_list = az_get(f"{BASE_URL}/linkedservices?api-version={API_VERSION}").get("value", [])

    manifest = {
        "capturedAtUtc": stamp,
        "resourceGroup": RESOURCE_GROUP,
        "factory": FACTORY,
        "pipelines": [],
        "datasets": [],
        "linkedServices": [],
        "secretValuesPersisted": False,
    }
    write_json(
        snapshot_dir / "factory.sanitized.json",
        {
            "name": factory.get("name"),
            "location": factory.get("location"),
            "identityType": (factory.get("identity") or {}).get("type"),
            "properties": sanitize(factory.get("properties", {})),
        },
    )

    for summary in pipeline_list:
        name = summary["name"]
        payload = az_get(f"{BASE_URL}/pipelines/{name}?api-version={API_VERSION}")
        write_json(snapshot_dir / "pipelines" / f"{name}.json", payload)
        manifest["pipelines"].append(
            {"name": name, "propertiesSha256": canonical_hash(payload.get("properties", {}))}
        )

    for summary in dataset_list:
        name = summary["name"]
        payload = az_get(f"{BASE_URL}/datasets/{name}?api-version={API_VERSION}")
        write_json(snapshot_dir / "datasets" / f"{name}.json", payload)
        manifest["datasets"].append(
            {"name": name, "propertiesSha256": canonical_hash(payload.get("properties", {}))}
        )

    sanitized_linked = []
    for summary in linked_list:
        name = summary["name"]
        payload = az_get(f"{BASE_URL}/linkedservices/{name}?api-version={API_VERSION}")
        safe_payload = sanitize(payload)
        sanitized_linked.append(safe_payload)
        properties = payload.get("properties", {})
        type_properties = properties.get("typeProperties", {})
        manifest["linkedServices"].append(
            {
                "name": name,
                "type": properties.get("type"),
                "version": type_properties.get("version"),
                "sslMode": type_properties.get("sslMode"),
                "safeTypePropertyNames": sorted(
                    key for key in type_properties if not SENSITIVE_KEY.search(key)
                ),
            }
        )
    write_json(snapshot_dir / "linked-services.sanitized.json", sanitized_linked)
    write_json(snapshot_dir / "manifest.json", manifest)

    print(json.dumps({
        "snapshotDirectory": str(snapshot_dir),
        "pipelines": [item["name"] for item in manifest["pipelines"]],
        "datasets": [item["name"] for item in manifest["datasets"]],
        "linkedServices": manifest["linkedServices"],
        "secretValuesPersisted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
