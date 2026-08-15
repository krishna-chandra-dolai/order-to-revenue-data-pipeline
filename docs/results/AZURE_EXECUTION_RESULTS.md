# Azure execution results

Execution window: 2026-08-14 18:48–18:51 UTC

## Deployment

Pre-deployment snapshot:
`azure/deployment/snapshots/pre-deployment-20260814T184240Z/`

The snapshot contains the live full-load pipeline, both live customer datasets,
factory metadata, hashes, and sanitized linked-service metadata. No linked
service secret value was persisted.

Published datasets:

- `DS_PG_Products`
- `DS_PG_Orders`
- `DS_PG_Payments`
- `DS_ADLS_Products_Raw`
- `DS_ADLS_Orders_Raw`
- `DS_ADLS_Payments_Raw`
- `DS_PG_Query`
- `DS_ADLS_Incremental_Raw`
- `DS_ADLS_Watermark_Control`

Published pipelines:

- updated `PL_Initial_Full_Load`
- created `PL_Incremental_Load`

`Copy_Customers_To_Raw`, `DS_PG_Customers`, and
`DS_ADLS_Customers_Raw` were exact property matches with the pre-deployment live
objects. Neither linked service was written or recreated.

## Full-load run

- Run ID: `4d23b5f2-84d3-4a6f-955c-8132bd887cc9`
- Status: Succeeded
- Duration: 47,292 ms

| Table | PostgreSQL count | ADF rows copied | Raw CSV rows | Result |
|---|---:|---:|---:|---|
| customers | 2,000 | 2,000 | 2,000 | PASS |
| products | 300 | 300 | 300 | PASS |
| orders | 10,000 | 10,000 | 10,000 | PASS |
| payments | 9,990 | 9,990 | 9,990 | PASS |

All four count Lookups, Copies, the native count guard, and initial watermark
write succeeded.

## Incremental baseline run

- Run ID: `c1a0e615-3b53-480c-84a6-d2d2909ac6a0`
- Status: Succeeded
- Duration: 81,611 ms

Every bounded source count was zero, every Copy read/copied zero rows, and four
header-only files were written to versioned watermark paths. The validation
branch, watermark-history write, and current-watermark advance all succeeded.

Watermark timestamps remained unchanged:

| Table | Current watermark |
|---|---|
| customers | `2024-03-28 04:31:20+00` |
| products | `2023-05-29 00:00:00+00` |
| orders | `2025-07-03 06:58:08+00` |
| payments | `2025-07-09 12:40:20+00` |

The current control row now records the successful incremental run ID, and a
separate history CSV retains the same successful boundary.

## Actual raw processing

The four live full-load CSVs were downloaded using Azure RBAC and processed by
the repository's Python quality layer.

| Table | Raw | Curated | Rejected | Reconciled |
|---|---:|---:|---:|---|
| customers | 2,000 | 2,000 | 0 | PASS |
| products | 300 | 300 | 0 | PASS |
| orders | 10,000 | 9,953 | 47 | PASS |
| payments | 9,990 | 9,943 | 47 | PASS |

All curated primary keys are unique and every rejected row has a reason.

## Curated/rejected publication

The validated outputs were uploaded with Microsoft Entra authentication to
non-destructive, run-specific paths:

`<table>/full/run_id=4d23b5f2-84d3-4a6f-955c-8132bd887cc9/<table>.csv`

| Table | Curated rows | Rejected rows | Remote content verified | Reasons complete |
|---|---:|---:|---|---|
| customers | 2,000 | 0 | PASS | N/A |
| products | 300 | 0 | PASS | N/A |
| orders | 9,953 | 47 | PASS | PASS |
| payments | 9,943 | 47 | PASS | PASS |

All eight uploaded files were read back from ADLS and matched their local bytes
and SHA-256 hashes. Uploads used `If-None-Match: *`, so an existing blob could
not be overwritten. A comparison of the raw inventory before and after the
publication found no change in blob names, byte sizes, modification times, or
row counts.

Two temporary `Storage Blob Data Contributor` assignments were used, one at
the `curated` container ARM scope and one at the `rejected` container ARM scope.
Both exact assignments were removed after verification. The signed-in user now
retains only the previously approved `Storage Blob Data Reader` data-plane role
at storage-account scope; no Blob Data Contributor or Blob Data Owner assignment
remains on either target container. No storage key was read or used.

## Issue found and fixed

ADF serialized PostgreSQL `timestamptz` values as timezone-naive strings such as
`2023-06-24 21:56:01.0000000`. The first local real-raw pass therefore rejected
all rows for missing offsets. Because the verified source values and extraction
session are UTC, the parser now restores UTC for this exact ADF representation,
normalizes curated timestamps to offset-bearing ISO format, and retains original
raw values in rejects. Unit tests and real-raw reconciliation passed after the
fix. No incorrect files were uploaded to Azure.

## Controlled incremental execution — 2026-08-15

The approved source transaction inserted and retained exactly one customer,
product, order, and payment. First run
`543b70bb-1ba9-4571-9f90-ac3375e42989` succeeded with a bounded source count and
`rowsCopied` of one for every table. Key-level ADLS readback and hashes passed,
and all four watermarks advanced to `2026-08-15 03:25:37.527343+00`.

Second run `71f6b8db-482c-4827-ba29-377867bdecad` succeeded without another
source change. Every bounded count and `rowsCopied` value was zero and the
watermarks remained unchanged. However, the sink path is based only on the
upper watermark, so the second run targeted the same four filenames and
overwrote the verified one-row files with header-only files. Source-window
idempotency passed; append-only raw retention failed. No Azure resource,
permission, linked service, dataset, pipeline definition, or SKU was changed
during this test.

## Incremental sink-path remediation — 2026-08-15

The live factory was captured in
`azure/deployment/snapshots/pre-deployment-20260815T040252Z/`. Pre-deployment comparison
proved that the only functional differences were the four incremental Copy
sink expressions. Seven regression/static tests and the scoped ADF definition
validator passed. Only `PL_Incremental_Load` was published, and live ARM
readback confirmed this pattern for all four outputs:

`<table>/incremental/watermark=<timestamp>/run_id=<pipeline-run-id>/<table>.csv`

After the saved control state and recovery-window safety check passed, the
watermarks were restored to their exact documented pre-test values. Retest
`a9a497e5-ab71-4d74-a62a-38212063e860` copied one row for each table and exact
ID readback passed. Zero-change retest
`59b6f800-bba5-485a-9a1b-7ba608c4997a` copied zero for each table into separate
run-ID paths. The first retest files remained byte- and hash-identical after
the second run.

Append-only retention, watermark idempotency, and source idempotency all
passed. Final PostgreSQL counts are 2,001 customers, 301 products, 10,001
orders, and 9,991 payments; confirmed revenue remains `307,416,040.00`.
