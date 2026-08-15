from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADF_ROOT = REPO_ROOT / "azure" / "adf"


class AdfDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "deployment" / "build_adf_definitions.py"),
            ],
            check=True,
        )

    def test_every_definition_is_valid_json_and_contains_no_secret_material(self) -> None:
        files = sorted(ADF_ROOT.rglob("*.json"))
        self.assertGreaterEqual(len(files), 14)
        forbidden_terms = [
            "account" + "key",
            "default" + "endpointsprotocol",
            "pass" + "word",
            "client" + "_secret",
            "private" + " key",
        ]
        forbidden = re.compile(
            r"(?i)(" + "|".join(re.escape(term) for term in forbidden_terms) + r")"
        )
        for path in files:
            content = path.read_text(encoding="utf-8")
            json.loads(content)
            self.assertIsNone(forbidden.search(content), path)

    def test_full_load_preserves_customers_and_adds_three_tables(self) -> None:
        pipeline = json.loads(
            (ADF_ROOT / "pipelines" / "PL_Initial_Full_Load.json").read_text()
        )
        activities = {item["name"]: item for item in pipeline["properties"]["activities"]}
        for table_name in ("Customers", "Products", "Orders", "Payments"):
            self.assertIn(f"Copy_{table_name}_To_Raw", activities)
        customers = activities["Copy_Customers_To_Raw"]
        self.assertEqual("DS_PG_Customers", customers["inputs"][0]["referenceName"])
        self.assertEqual("DS_ADLS_Customers_Raw", customers["outputs"][0]["referenceName"])
        self.assertEqual("None", customers["typeProperties"]["source"]["partitionOption"])
        guard = activities["Validate_Full_Counts_Then_Initialize_Watermarks"]
        true_names = {
            item["name"] for item in guard["typeProperties"]["ifTrueActivities"]
        }
        false_names = {
            item["name"] for item in guard["typeProperties"]["ifFalseActivities"]
        }
        self.assertEqual({"Write_Initial_Watermarks"}, true_names)
        self.assertEqual({"Fail_Full_Load_Row_Count_Validation"}, false_names)

    def test_dataset_references_resolve_or_are_existing_linked_services(self) -> None:
        dataset_names = {
            json.loads(path.read_text())["name"]
            for path in (ADF_ROOT / "datasets").glob("*.json")
        }
        linked_services = {
            "LS_AzurePostgreSQL_OrderRevenue",
            "LS_ADLS_OrderRevenue",
        }
        for path in ADF_ROOT.rglob("*.json"):
            payload = json.loads(path.read_text())
            stack = [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if item.get("type") == "DatasetReference":
                        self.assertIn(item["referenceName"], dataset_names)
                    if item.get("type") == "LinkedServiceReference":
                        self.assertIn(item["referenceName"], linked_services)
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)

    def test_watermarks_advance_only_inside_successful_count_branch(self) -> None:
        pipeline = json.loads(
            (ADF_ROOT / "pipelines" / "PL_Incremental_Load.json").read_text()
        )
        top_level = {item["name"]: item for item in pipeline["properties"]["activities"]}
        self.assertNotIn("Advance_Current_Watermarks", top_level)
        guard = top_level["Validate_Counts_Then_Advance_Watermarks"]
        true_names = {
            item["name"] for item in guard["typeProperties"]["ifTrueActivities"]
        }
        false_names = {
            item["name"] for item in guard["typeProperties"]["ifFalseActivities"]
        }
        self.assertEqual(
            {"Write_Watermark_History", "Advance_Current_Watermarks"}, true_names
        )
        self.assertEqual({"Fail_Row_Count_Validation"}, false_names)

    def test_incremental_raw_paths_are_unique_per_pipeline_run(self) -> None:
        pipeline = json.loads(
            (ADF_ROOT / "pipelines" / "PL_Incremental_Load.json").read_text()
        )
        copies = [
            item for item in pipeline["properties"]["activities"]
            if item["name"].startswith("Copy_")
            and item["name"].endswith("_Incremental_To_Raw")
        ]
        self.assertEqual(4, len(copies))
        for activity in copies:
            value = activity["outputs"][0]["parameters"]["watermarkPath"]["value"]
            self.assertIn("/run_id=", value)
            self.assertIn("pipeline().RunId", value)


if __name__ == "__main__":
    unittest.main()
