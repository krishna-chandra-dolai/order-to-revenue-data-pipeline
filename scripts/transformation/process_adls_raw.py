"""Download the four full raw CSVs with Azure RBAC and process them locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transformation.process_raw_to_curated import TABLE_FIELDS, process
from scripts.validation.inspect_adls_counts import access_token, read_blob
from scripts.validation.validate_lake_outputs import validate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    raw_dir = work_dir / "raw"
    curated_dir = work_dir / "curated"
    rejected_dir = work_dir / "rejected"
    raw_dir.mkdir(parents=True, exist_ok=True)

    token = access_token()
    downloads = {}
    for table_name in TABLE_FIELDS:
        target = raw_dir / f"{table_name}.csv"
        if target.exists() and not args.overwrite:
            raise FileExistsError(f"Refusing to overwrite {target}")
        content = read_blob(token, "raw", f"{table_name}/{table_name}.csv")
        target.write_bytes(content)
        downloads[table_name] = {"bytes": len(content), "source": f"raw/{table_name}/{table_name}.csv"}

    processing = process(
        raw_dir,
        curated_dir,
        rejected_dir,
        overwrite=args.overwrite,
    )
    verification = validate(raw_dir, curated_dir, rejected_dir)
    evidence = {
        "status": "PASS" if processing["status"] == verification["status"] == "PASS" else "FAIL",
        "source": "actual ADLS full-load raw CSVs",
        "downloads": downloads,
        "processing": processing,
        "verification": verification,
        "localWorkDirectory": str(work_dir),
    }
    args.summary_file.parent.mkdir(parents=True, exist_ok=True)
    args.summary_file.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    if evidence["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
