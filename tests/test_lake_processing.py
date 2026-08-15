from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path


from scripts.ingestion.generate_data import generate
from scripts.transformation.process_raw_to_curated import process
from scripts.validation.validate_lake_outputs import validate


class LakeProcessingTests(unittest.TestCase):
    def test_generated_full_load_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw"
            curated = root / "curated"
            rejected = root / "rejected"
            generate(raw)

            summary = process(raw, curated, rejected)
            verification = validate(raw, curated, rejected)

            self.assertEqual("PASS", summary["status"])
            self.assertEqual("PASS", verification["status"])
            self.assertEqual(2_000, summary["tables"]["customers"]["curated_rows"])
            self.assertEqual(300, summary["tables"]["products"]["curated_rows"])
            self.assertEqual(47, summary["tables"]["orders"]["rejected_rows"])
            self.assertEqual(47, summary["tables"]["payments"]["rejected_rows"])

    def test_duplicate_latest_version_wins_without_dropping_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw"
            curated = root / "curated"
            rejected = root / "rejected"
            generate(raw)

            customers_path = raw / "customers.csv"
            with customers_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
                fields = list(rows[0])
            duplicate = dict(rows[0])
            duplicate["customer_name"] = "Corrected Name"
            duplicate["updated_at"] = "2026-08-14T00:00:00+00:00"
            with customers_path.open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=fields).writerow(duplicate)

            summary = process(raw, curated, rejected)
            verification = validate(raw, curated, rejected)

            self.assertEqual("PASS", verification["status"])
            self.assertEqual(2_001, summary["tables"]["customers"]["raw_rows"])
            self.assertEqual(2_000, summary["tables"]["customers"]["curated_rows"])
            self.assertEqual(1, summary["tables"]["customers"]["rejected_rows"])
            self.assertEqual(
                1,
                summary["tables"]["customers"]["rejection_reason_counts"][
                    "SUPERSEDED_BY_LATER_VERSION"
                ],
            )


if __name__ == "__main__":
    unittest.main()
