"""List ADLS blobs and count CSV rows using Azure RBAC, never account keys."""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


ACCOUNT = "stordrevdata26081401"
CONTAINERS = ("raw", "curated", "rejected")


def shell_az(arguments: list[str]) -> str:
    command = subprocess.list2cmdline(["az", *arguments, "--only-show-errors"])
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        shell=True,
    )
    return completed.stdout


def access_token() -> str:
    token = shell_az([
        "account",
        "get-access-token",
        "--resource",
        "https://storage.azure.com/",
        "--query",
        "accessToken",
        "-o",
        "tsv",
    ]).strip()
    if not token:
        raise RuntimeError("Azure CLI did not return a storage-scoped token")
    return token


def list_blobs(container: str) -> list[dict]:
    return json.loads(shell_az([
        "storage",
        "blob",
        "list",
        "--account-name",
        ACCOUNT,
        "--container-name",
        container,
        "--auth-mode",
        "login",
        "--num-results",
        "5000",
        "-o",
        "json",
    ]))


def read_blob(token: str, container: str, name: str) -> bytes:
    quoted_name = urllib.parse.quote(name, safe="/")
    request = urllib.request.Request(
        f"https://{ACCOUNT}.blob.core.windows.net/{container}/{quoted_name}",
        headers={
            "Authorization": f"Bearer {token}",
            "x-ms-version": "2023-11-03",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    token = access_token()
    result = {"storageAccount": ACCOUNT, "authentication": "Azure RBAC", "containers": {}}
    for container in CONTAINERS:
        entries = []
        for blob in list_blobs(container):
            name = blob["name"]
            properties = blob.get("properties") or {}
            entry = {
                "name": name,
                "contentLength": properties.get("contentLength"),
                "lastModified": properties.get("lastModified"),
            }
            if name.lower().endswith(".csv"):
                content = read_blob(token, container, name)
                text = content.decode("utf-8-sig")
                reader = csv.reader(io.StringIO(text, newline=""))
                rows = list(reader)
                entry["header"] = rows[0] if rows else []
                entry["dataRows"] = max(len(rows) - 1, 0)
            entries.append(entry)
        result["containers"][container] = sorted(entries, key=lambda item: item["name"])
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
