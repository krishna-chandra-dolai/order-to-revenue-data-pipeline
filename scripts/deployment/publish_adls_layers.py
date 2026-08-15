"""Temporarily grant narrow blob writes, publish verified layers, and revoke them."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transformation.process_raw_to_curated import TABLE_FIELDS
from scripts.validation.inspect_adls_counts import ACCOUNT, access_token, read_blob


SUBSCRIPTION_ID = "699ac1b5-f0fd-40eb-b865-b4b22eaf3dec"
RESOURCE_GROUP = "rg-order-revenue-dev"
ROLE = "Storage Blob Data Contributor"
FULL_LOAD_RUN_ID = "4d23b5f2-84d3-4a6f-955c-8132bd887cc9"
ACCOUNT_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.Storage/storageAccounts/{ACCOUNT}"
)
CONTAINER_SCOPES = {
    container: f"{ACCOUNT_SCOPE}/blobServices/default/containers/{container}"
    for container in ("curated", "rejected")
}
WRITE_DATA_ROLES = {"Storage Blob Data Contributor", "Storage Blob Data Owner"}


def run_az(arguments: list[str]) -> object:
    command = subprocess.list2cmdline(["az", *arguments, "--only-show-errors"])
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        shell=True,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def current_principal_id() -> str:
    result = run_az(["ad", "signed-in-user", "show", "-o", "json"])
    principal_id = result.get("id") if isinstance(result, dict) else None
    if not principal_id:
        raise RuntimeError("Could not resolve the signed-in Azure user")
    return principal_id


def role_assignments(principal_id: str, scope: str, *, inherited: bool) -> list[dict]:
    args = [
        "role",
        "assignment",
        "list",
        "--assignee-object-id",
        principal_id,
        "--scope",
        scope,
        "-o",
        "json",
    ]
    if inherited:
        args.append("--include-inherited")
    result = run_az(args)
    return result if isinstance(result, list) else []


def verify_container_resource(scope: str) -> None:
    url = f"https://management.azure.com{scope}?api-version=2023-05-01"
    payload = run_az(["rest", "--method", "get", "--url", url, "-o", "json"])
    if not isinstance(payload, dict) or payload.get("id", "").lower() != scope.lower():
        raise RuntimeError(f"Container resource scope could not be verified: {scope}")


def create_role(principal_id: str, scope: str) -> dict:
    existing = role_assignments(principal_id, scope, inherited=False)
    if any(item.get("roleDefinitionName") == ROLE for item in existing):
        raise RuntimeError(f"A pre-existing {ROLE} assignment already exists at {scope}; refusing to own/remove it")
    result = run_az([
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        principal_id,
        "--assignee-principal-type",
        "User",
        "--role",
        ROLE,
        "--scope",
        scope,
        "-o",
        "json",
    ])
    assignment_id = result.get("id") if isinstance(result, dict) else None
    if not assignment_id:
        raise RuntimeError(f"Role assignment creation did not return an ID for {scope}")
    return {"id": assignment_id, "scope": scope, "role": ROLE}


def blob_url(container: str, name: str) -> str:
    return (
        f"https://{ACCOUNT}.blob.core.windows.net/{container}/"
        f"{urllib.parse.quote(name, safe='/=')}"
    )


def blob_exists(token: str, container: str, name: str) -> bool:
    request = urllib.request.Request(
        blob_url(container, name),
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "x-ms-version": "2023-11-03"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def put_blob(token: str, container: str, name: str, content: bytes) -> None:
    request = urllib.request.Request(
        blob_url(container, name),
        method="PUT",
        data=content,
        headers={
            "Authorization": f"Bearer {token}",
            "x-ms-version": "2023-11-03",
            "x-ms-blob-type": "BlockBlob",
            "Content-Type": "text/csv; charset=utf-8",
            "If-None-Match": "*",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status not in {201, 202}:
            raise RuntimeError(f"Unexpected upload status {response.status} for {container}/{name}")


def upload_with_propagation(container: str, name: str, content: bytes) -> None:
    deadline = time.monotonic() + 240
    while True:
        token = access_token()
        try:
            if blob_exists(token, container, name):
                raise FileExistsError(f"Refusing to overwrite existing blob {container}/{name}")
            put_blob(token, container, name, content)
            return
        except urllib.error.HTTPError as error:
            if error.code == 403 and time.monotonic() < deadline:
                time.sleep(10)
                continue
            raise


def verify_uploaded(container: str, name: str, expected: bytes) -> dict:
    actual = read_blob(access_token(), container, name)
    if actual != expected:
        raise AssertionError(f"Uploaded content differs for {container}/{name}")
    rows = list(csv.DictReader(io.StringIO(actual.decode("utf-8-sig"), newline="")))
    missing_reasons = 0
    if container == "rejected":
        missing_reasons = sum(not row.get("rejection_reason", "").strip() for row in rows)
        if missing_reasons:
            raise AssertionError(f"{container}/{name} has {missing_reasons} rows without rejection reasons")
    return {
        "container": container,
        "name": name,
        "bytes": len(actual),
        "sha256": hashlib.sha256(actual).hexdigest(),
        "dataRows": len(rows),
        "rejectedRowsWithoutReason": missing_reasons,
    }


def remove_assignment(assignment_id: str) -> None:
    run_az(["role", "assignment", "delete", "--ids", assignment_id])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--approve-temporary-roles", action="store_true")
    args = parser.parse_args()
    if not args.approve_temporary_roles:
        raise RuntimeError("Temporary role creation/removal requires --approve-temporary-roles")

    work_dir = args.work_dir.resolve()
    principal_id = current_principal_id()
    for scope in CONTAINER_SCOPES.values():
        verify_container_resource(scope)

    inherited_before = role_assignments(principal_id, ACCOUNT_SCOPE, inherited=True)
    preexisting_write_roles = [
        {"role": item.get("roleDefinitionName"), "scope": item.get("scope")}
        for item in inherited_before
        if item.get("roleDefinitionName") in WRITE_DATA_ROLES
    ]
    if preexisting_write_roles:
        raise RuntimeError(
            f"A pre-existing inherited blob data write role exists; refusing temporary-role workflow: {preexisting_write_roles}"
        )

    assignments: list[dict] = []
    uploaded: list[dict] = []
    cleanup_errors: list[str] = []
    try:
        for container, scope in CONTAINER_SCOPES.items():
            assignment = create_role(principal_id, scope)
            assignment["container"] = container
            assignments.append(assignment)

        for table_name in TABLE_FIELDS:
            for container in ("curated", "rejected"):
                source = work_dir / container / f"{table_name}.csv"
                if not source.is_file():
                    raise FileNotFoundError(source)
                name = (
                    f"{table_name}/full/run_id={FULL_LOAD_RUN_ID}/"
                    f"{table_name}.csv"
                )
                content = source.read_bytes()
                upload_with_propagation(container, name, content)
                uploaded.append(verify_uploaded(container, name, content))
    finally:
        for assignment in reversed(assignments):
            try:
                remove_assignment(assignment["id"])
            except Exception as error:  # cleanup evidence must survive and be reported
                cleanup_errors.append(f"{assignment['scope']}: {error}")

    if cleanup_errors:
        raise RuntimeError(f"Temporary role cleanup failed: {cleanup_errors}")

    # Confirm both exact assignments are gone and no inherited blob data writer remains.
    deadline = time.monotonic() + 180
    final_assignments: list[dict] = []
    while True:
        final_assignments = role_assignments(principal_id, ACCOUNT_SCOPE, inherited=True)
        remaining_ids = {item.get("id") for item in final_assignments}
        if all(assignment["id"] not in remaining_ids for assignment in assignments):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Temporary role assignments still appear after removal")
        time.sleep(10)

    final_relevant_roles = [
        {"role": item.get("roleDefinitionName"), "scope": item.get("scope")}
        for item in final_assignments
        if item.get("roleDefinitionName", "").startswith("Storage Blob Data")
    ]
    if any(item["role"] in WRITE_DATA_ROLES for item in final_relevant_roles):
        raise RuntimeError(f"Blob write role remains after cleanup: {final_relevant_roles}")

    evidence = {
        "status": "PASS",
        "temporaryAssignments": [
            {"role": item["role"], "scope": item["scope"], "container": item["container"]}
            for item in assignments
        ],
        "uploaded": uploaded,
        "temporaryAssignmentsRemoved": True,
        "finalRelevantRoles": final_relevant_roles,
        "rawPermissionsChanged": False,
        "storageKeysUsed": False,
    }
    args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_file.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
