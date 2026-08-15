"""Build sanitized Azure Data Factory JSON definitions from verified schemas.

This script only writes local files under ``azure/adf``. It never authenticates
to Azure and never publishes resources.
"""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADF_ROOT = REPO_ROOT / "azure" / "adf"

TABLES = {
    "customers": [
        ("customer_id", "Int64", "bigint"),
        ("customer_name", "String", "character varying"),
        ("email", "String", "character varying"),
        ("city", "String", "character varying"),
        ("state", "String", "character varying"),
        ("created_at", "DateTime", "timestamp with time zone"),
        ("updated_at", "DateTime", "timestamp with time zone"),
    ],
    "products": [
        ("product_id", "Int64", "bigint"),
        ("product_name", "String", "character varying"),
        ("category", "String", "character varying"),
        ("brand", "String", "character varying"),
        ("unit_price", "Decimal", "numeric"),
        ("created_at", "DateTime", "timestamp with time zone"),
        ("updated_at", "DateTime", "timestamp with time zone"),
    ],
    "orders": [
        ("order_id", "Int64", "bigint"),
        ("customer_id", "Int64", "bigint"),
        ("product_id", "Int64", "bigint"),
        ("quantity", "Int32", "integer"),
        ("unit_price", "Decimal", "numeric"),
        ("order_status", "String", "character varying"),
        ("created_at", "DateTime", "timestamp with time zone"),
        ("updated_at", "DateTime", "timestamp with time zone"),
    ],
    "payments": [
        ("payment_id", "Int64", "bigint"),
        ("order_id", "Int64", "bigint"),
        ("payment_amount", "Decimal", "numeric"),
        ("payment_method", "String", "character varying"),
        ("payment_status", "String", "character varying"),
        ("payment_time", "DateTime", "timestamp with time zone"),
        ("updated_at", "DateTime", "timestamp with time zone"),
    ],
}

PRIMARY_KEYS = {
    "customers": "customer_id",
    "products": "product_id",
    "orders": "order_id",
    "payments": "payment_id",
}


def expression(value: str) -> dict[str, str]:
    return {"value": value, "type": "Expression"}


def dataset_reference(name: str, parameters: dict | None = None) -> dict:
    result = {"referenceName": name, "type": "DatasetReference"}
    if parameters is not None:
        result["parameters"] = parameters
    return result


def linked_service_reference(name: str) -> dict:
    return {"referenceName": name, "type": "LinkedServiceReference"}


def policy() -> dict:
    return {
        "timeout": "0.12:00:00",
        "retry": 0,
        "retryIntervalInSeconds": 30,
        "secureOutput": False,
        "secureInput": False,
    }


def nested_and(checks: list[str]) -> str:
    if not checks:
        raise ValueError("At least one condition is required")
    combined = checks[0]
    for check in checks[1:]:
        combined = f"and({combined}, {check})"
    return "@" + combined


def write_definition(folder: str, name: str, payload: dict) -> None:
    target_dir = ADF_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def pg_dataset(table_name: str) -> dict:
    schema = []
    for name, _, physical_type in TABLES[table_name]:
        column = {"name": name, "type": physical_type}
        if physical_type == "numeric":
            column.update(precision=12, scale=2)
        else:
            column.update(precision=0, scale=0)
        schema.append(column)
    return {
        "name": f"DS_PG_{table_name.title()}",
        "properties": {
            "linkedServiceName": linked_service_reference("LS_AzurePostgreSQL_OrderRevenue"),
            "annotations": [],
            "type": "AzurePostgreSqlTable",
            "schema": schema,
            "typeProperties": {"schema": "public", "table": table_name},
        },
    }


def raw_dataset(table_name: str) -> dict:
    return {
        "name": f"DS_ADLS_{table_name.title()}_Raw",
        "properties": {
            "linkedServiceName": linked_service_reference("LS_ADLS_OrderRevenue"),
            "annotations": [],
            "type": "DelimitedText",
            "typeProperties": {
                "location": {
                    "type": "AzureBlobFSLocation",
                    "fileName": f"{table_name}.csv",
                    "folderPath": table_name,
                    "fileSystem": "raw",
                },
                "columnDelimiter": ",",
                "escapeChar": "\\",
                "firstRowAsHeader": True,
                "quoteChar": '"',
            },
            "schema": [],
        },
    }


def translator(table_name: str) -> dict:
    mappings = []
    for name, logical_type, physical_type in TABLES[table_name]:
        source = {"name": name, "type": logical_type, "physicalType": physical_type}
        sink = {"name": name, "type": "String", "physicalType": "String"}
        mappings.append({"source": source, "sink": sink})
    return {
        "type": "TabularTranslator",
        "mappings": mappings,
        "typeConversion": True,
        "typeConversionSettings": {
            "allowDataTruncation": True,
            "treatBooleanAsNumber": False,
        },
    }


def copy_activity(table_name: str) -> dict:
    title = table_name.title()
    return {
        "name": f"Copy_{title}_To_Raw",
        "type": "Copy",
        "dependsOn": [],
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "AzurePostgreSqlSource",
                "queryTimeout": "02:00:00",
                "partitionOption": "None",
            },
            "sink": {
                "type": "DelimitedTextSink",
                "storeSettings": {"type": "AzureBlobFSWriteSettings"},
                "formatSettings": {
                    "type": "DelimitedTextWriteSettings",
                    "quoteAllText": True,
                    "fileExtension": ".txt",
                },
            },
            "enableStaging": False,
            "translator": translator(table_name),
        },
        "inputs": [dataset_reference(f"DS_PG_{title}", {})],
        "outputs": [dataset_reference(f"DS_ADLS_{title}_Raw", {})],
    }


def pg_query_dataset() -> dict:
    return {
        "name": "DS_PG_Query",
        "properties": {
            "linkedServiceName": linked_service_reference("LS_AzurePostgreSQL_OrderRevenue"),
            "annotations": [],
            "type": "AzurePostgreSqlTable",
            "schema": [],
            "typeProperties": {},
        },
    }


def watermark_dataset() -> dict:
    return {
        "name": "DS_ADLS_Watermark_Control",
        "properties": {
            "linkedServiceName": linked_service_reference("LS_ADLS_OrderRevenue"),
            "parameters": {
                "folderPath": {"type": "String", "defaultValue": "_control"},
                "fileName": {"type": "String", "defaultValue": "watermarks.csv"},
            },
            "annotations": [],
            "type": "DelimitedText",
            "typeProperties": {
                "location": {
                    "type": "AzureBlobFSLocation",
                    "fileName": expression("@dataset().fileName"),
                    "folderPath": expression("@dataset().folderPath"),
                    "fileSystem": "raw",
                },
                "columnDelimiter": ",",
                "escapeChar": "\\",
                "firstRowAsHeader": True,
                "quoteChar": '"',
            },
            "schema": [
                {"name": f"{table_name}_watermark", "type": "String"}
                for table_name in TABLES
            ] + [
                {"name": "last_successful_run_id", "type": "String"},
                {"name": "completed_at", "type": "String"},
            ],
        },
    }


def incremental_sink_dataset() -> dict:
    return {
        "name": "DS_ADLS_Incremental_Raw",
        "properties": {
            "linkedServiceName": linked_service_reference("LS_ADLS_OrderRevenue"),
            "parameters": {
                "tableName": {"type": "String"},
                "watermarkPath": {"type": "String"},
            },
            "annotations": [],
            "type": "DelimitedText",
            "typeProperties": {
                "location": {
                    "type": "AzureBlobFSLocation",
                    "fileName": expression("@concat(dataset().tableName, '.csv')"),
                    "folderPath": expression(
                        "@concat(dataset().tableName, '/incremental/', dataset().watermarkPath)"
                    ),
                    "fileSystem": "raw",
                },
                "columnDelimiter": ",",
                "escapeChar": "\\",
                "firstRowAsHeader": True,
                "quoteChar": '"',
            },
            "schema": [],
        },
    }


def initial_watermark_query(values: dict[str, str] | None = None) -> dict:
    if values is None:
        columns = [
            f"MAX(updated_at)::text AS {table_name}_watermark"
            for table_name in TABLES
        ]
        query = "SELECT " + ", ".join(
            f"(SELECT {column} FROM public.{table_name})"
            for table_name, column in zip(TABLES, columns)
        )
        # The subquery aliases need to be outside each scalar subquery.
        query = "SELECT " + ", ".join(
            f"(SELECT MAX(updated_at)::text FROM public.{table_name}) AS {table_name}_watermark"
            for table_name in TABLES
        )
        return expression(
            "@concat('"
            + query.replace("'", "''")
            + ", ''' , pipeline().RunId, '''::text AS last_successful_run_id, "
            "clock_timestamp()::text AS completed_at')"
        )

    sql_parts = [
        f"TIMESTAMPTZ ''' , {value}, '''::text AS {table_name}_watermark"
        for table_name, value in values.items()
    ]
    return expression(
        "@concat('SELECT "
        + ", ".join(sql_parts)
        + ", ''' , pipeline().RunId, '''::text AS last_successful_run_id, "
        "clock_timestamp()::text AS completed_at')"
    )


def watermark_copy_activity(
    name: str,
    query: dict,
    *,
    depends_on: list[dict],
    folder: str,
    filename: object,
) -> dict:
    return {
        "name": name,
        "type": "Copy",
        "dependsOn": depends_on,
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "AzurePostgreSqlSource",
                "query": query,
                "queryTimeout": "00:10:00",
                "partitionOption": "None",
            },
            "sink": {
                "type": "DelimitedTextSink",
                "storeSettings": {"type": "AzureBlobFSWriteSettings"},
                "formatSettings": {
                    "type": "DelimitedTextWriteSettings",
                    "quoteAllText": True,
                    "fileExtension": ".csv",
                },
            },
            "enableStaging": False,
        },
        "inputs": [dataset_reference("DS_PG_Query", {})],
        "outputs": [
            dataset_reference(
                "DS_ADLS_Watermark_Control",
                {"folderPath": folder, "fileName": filename},
            )
        ],
    }


def full_load_pipeline() -> dict:
    copies = [copy_activity(table_name) for table_name in TABLES]
    lookups = [full_count_lookup(table_name) for table_name in TABLES]
    dependencies = [
        {"activity": activity["name"], "dependencyConditions": ["Succeeded"]}
        for activity in [*copies, *lookups]
    ]
    initialize = watermark_copy_activity(
        "Write_Initial_Watermarks",
        initial_watermark_query(),
        depends_on=[],
        folder="_control",
        filename="watermarks.csv",
    )
    count_checks = [
        "equals(int(activity('Copy_"
        + table_name.title()
        + "_To_Raw').output.rowsCopied), int(activity('Lookup_"
        + table_name.title()
        + "_Full_Count').output.firstRow.source_rows))"
        for table_name in TABLES
    ]
    guard = {
        "name": "Validate_Full_Counts_Then_Initialize_Watermarks",
        "type": "IfCondition",
        "dependsOn": dependencies,
        "userProperties": [],
        "typeProperties": {
            "expression": expression(nested_and(count_checks)),
            "ifTrueActivities": [initialize],
            "ifFalseActivities": [
                {
                    "name": "Fail_Full_Load_Row_Count_Validation",
                    "type": "Fail",
                    "dependsOn": [],
                    "userProperties": [],
                    "typeProperties": {
                        "message": "One or more full-load source counts did not match Copy rowsCopied; initial watermarks were not written.",
                        "errorCode": "FULL_LOAD_ROW_COUNT_MISMATCH",
                    },
                }
            ],
        },
    }
    return {
        "name": "PL_Initial_Full_Load",
        "properties": {"activities": [*copies, *lookups, guard], "annotations": []},
    }


def full_count_lookup(table_name: str) -> dict:
    title = table_name.title()
    return {
        "name": f"Lookup_{title}_Full_Count",
        "type": "Lookup",
        "dependsOn": [],
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "AzurePostgreSqlSource",
                "query": f"SELECT COUNT(*)::bigint AS source_rows FROM public.{table_name}",
                "queryTimeout": "00:10:00",
                "partitionOption": "None",
            },
            "dataset": dataset_reference("DS_PG_Query", {}),
            "firstRowOnly": True,
        },
    }


def lookup_current_watermarks() -> dict:
    return {
        "name": "Lookup_Current_Watermarks",
        "type": "Lookup",
        "dependsOn": [],
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "DelimitedTextSource",
                "storeSettings": {
                    "type": "AzureBlobFSReadSettings",
                    "recursive": False,
                    "enablePartitionDiscovery": False,
                },
                "formatSettings": {"type": "DelimitedTextReadSettings"},
            },
            "dataset": dataset_reference(
                "DS_ADLS_Watermark_Control",
                {"folderPath": "_control", "fileName": "watermarks.csv"},
            ),
            "firstRowOnly": True,
        },
    }


def lookup_window(table_name: str) -> dict:
    title = table_name.title()
    old = f"activity('Lookup_Current_Watermarks').output.firstRow.{table_name}_watermark"
    primary_key = PRIMARY_KEYS[table_name]
    query = (
        "@concat('WITH boundary AS (SELECT COALESCE(MAX(updated_at), TIMESTAMPTZ ''' , "
        f"{old}, "
        f"''' ) AS new_watermark FROM public.{table_name}) "
        "SELECT boundary.new_watermark::text AS new_watermark, COUNT(t."
        f"{primary_key})::bigint AS expected_rows FROM boundary "
        f"LEFT JOIN public.{table_name} t ON t.updated_at > TIMESTAMPTZ ''' , {old}, "
        "''' AND t.updated_at <= boundary.new_watermark GROUP BY boundary.new_watermark')"
    )
    return {
        "name": f"Lookup_{title}_Window",
        "type": "Lookup",
        "dependsOn": [
            {"activity": "Lookup_Current_Watermarks", "dependencyConditions": ["Succeeded"]}
        ],
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "AzurePostgreSqlSource",
                "query": expression(query),
                "queryTimeout": "00:10:00",
                "partitionOption": "None",
            },
            "dataset": dataset_reference("DS_PG_Query", {}),
            "firstRowOnly": True,
        },
    }


def incremental_copy(table_name: str) -> dict:
    title = table_name.title()
    old = f"activity('Lookup_Current_Watermarks').output.firstRow.{table_name}_watermark"
    new = f"activity('Lookup_{title}_Window').output.firstRow.new_watermark"
    columns = ", ".join(column[0] for column in TABLES[table_name])
    query = (
        f"@concat('SELECT {columns} FROM public.{table_name} WHERE updated_at > TIMESTAMPTZ ''' , "
        f"{old}, "
        "''' AND updated_at <= TIMESTAMPTZ ''' , "
        f"{new}, "
        f"''' ORDER BY updated_at, {PRIMARY_KEYS[table_name]}')"
    )
    return {
        "name": f"Copy_{title}_Incremental_To_Raw",
        "type": "Copy",
        "dependsOn": [
            {"activity": f"Lookup_{title}_Window", "dependencyConditions": ["Succeeded"]}
        ],
        "policy": policy(),
        "userProperties": [],
        "typeProperties": {
            "source": {
                "type": "AzurePostgreSqlSource",
                "query": expression(query),
                "queryTimeout": "02:00:00",
                "partitionOption": "None",
            },
            "sink": {
                "type": "DelimitedTextSink",
                "storeSettings": {"type": "AzureBlobFSWriteSettings"},
                "formatSettings": {
                    "type": "DelimitedTextWriteSettings",
                    "quoteAllText": True,
                    "fileExtension": ".csv",
                },
            },
            "enableStaging": False,
            "translator": translator(table_name),
        },
        "inputs": [dataset_reference("DS_PG_Query", {})],
        "outputs": [
            dataset_reference(
                "DS_ADLS_Incremental_Raw",
                {
                    "tableName": table_name,
                    "watermarkPath": expression(
                        f"@concat('watermark=', formatDateTime({new}, "
                        "'yyyyMMddTHHmmssfffffffZ'), '/run_id=', pipeline().RunId)"
                    ),
                },
            )
        ],
    }


def incremental_watermark_query() -> dict:
    values = {
        table_name: f"activity('Lookup_{table_name.title()}_Window').output.firstRow.new_watermark"
        for table_name in TABLES
    }
    return initial_watermark_query(values)


def incremental_pipeline() -> dict:
    current = lookup_current_watermarks()
    lookups = [lookup_window(table_name) for table_name in TABLES]
    copies = [incremental_copy(table_name) for table_name in TABLES]
    count_checks = [
        "equals(int(activity('Copy_"
        + table_name.title()
        + "_Incremental_To_Raw').output.rowsCopied), "
        + "int(activity('Lookup_"
        + table_name.title()
        + "_Window').output.firstRow.expected_rows))"
        for table_name in TABLES
    ]
    condition = nested_and(count_checks)
    query = incremental_watermark_query()
    history = watermark_copy_activity(
        "Write_Watermark_History",
        query,
        depends_on=[],
        folder="_control/history",
        filename=expression("@concat(pipeline().RunId, '.csv')"),
    )
    current_write = watermark_copy_activity(
        "Advance_Current_Watermarks",
        query,
        depends_on=[
            {"activity": "Write_Watermark_History", "dependencyConditions": ["Succeeded"]}
        ],
        folder="_control",
        filename="watermarks.csv",
    )
    validate = {
        "name": "Validate_Counts_Then_Advance_Watermarks",
        "type": "IfCondition",
        "dependsOn": [
            {"activity": activity["name"], "dependencyConditions": ["Succeeded"]}
            for activity in copies
        ],
        "userProperties": [],
        "typeProperties": {
            "expression": expression(condition),
            "ifTrueActivities": [history, current_write],
            "ifFalseActivities": [
                {
                    "name": "Fail_Row_Count_Validation",
                    "type": "Fail",
                    "dependsOn": [],
                    "userProperties": [],
                    "typeProperties": {
                        "message": "One or more incremental source counts did not match Copy rowsCopied; watermarks were not advanced.",
                        "errorCode": "INCREMENTAL_ROW_COUNT_MISMATCH",
                    },
                }
            ],
        },
    }
    return {
        "name": "PL_Incremental_Load",
        "properties": {
            "activities": [current, *lookups, *copies, validate],
            "annotations": [],
        },
    }


def main() -> None:
    for table_name in TABLES:
        write_definition("datasets", f"DS_PG_{table_name.title()}", pg_dataset(table_name))
        write_definition("datasets", f"DS_ADLS_{table_name.title()}_Raw", raw_dataset(table_name))
    write_definition("datasets", "DS_PG_Query", pg_query_dataset())
    write_definition("datasets", "DS_ADLS_Watermark_Control", watermark_dataset())
    write_definition("datasets", "DS_ADLS_Incremental_Raw", incremental_sink_dataset())
    write_definition("pipelines", "PL_Initial_Full_Load", full_load_pipeline())
    write_definition("pipelines", "PL_Incremental_Load", incremental_pipeline())
    manifest = {
        "factory": "adf-order-revenue-26081401",
        "resourceGroup": "rg-order-revenue-dev",
        "reuseLinkedServices": [
            "LS_AzurePostgreSQL_OrderRevenue",
            "LS_ADLS_OrderRevenue",
        ],
        "deploymentOrder": ["datasets", "pipelines"],
        "generatedBy": "scripts/deployment/build_adf_definitions.py",
        "containsSecrets": False,
    }
    (ADF_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote sanitized ADF definitions under {ADF_ROOT}")


if __name__ == "__main__":
    main()
