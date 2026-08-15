# Controlled non-zero incremental test results

Execution date: 2026-08-15 (Asia/Calcutta)

Overall result: **PASS — the sink-path defect was remediated, deployed, and
proved with non-zero and zero-change retests under separate run-ID paths.**

## PostgreSQL mutation

The approved one-time transaction in
`database/queries/08_controlled_incremental_test.sql` first asserted the exact baseline
counts, unused IDs, unused unique values, and safe foreign-key insertion order.
It then inserted and retained only the four approved rows.

| Table | Count before | Count after | Retained test key |
|---|---:|---:|---:|
| customers | 2,000 | 2,001 | 2,001 |
| products | 300 | 301 | 301 |
| orders | 10,000 | 10,001 | 10,001 |
| payments | 9,990 | 9,991 | 9,991 |

All four rows use `2026-08-15 03:25:37.527343+00` for `updated_at`. The order
references customer 2,001 and product 301, and payment 9,991 references order
10,001. Confirmed orders remained 8,941 and confirmed revenue remained
`307,416,040.00` before and after insertion and across all four ADF runs.

## Original first incremental run

- Run ID: `543b70bb-1ba9-4571-9f90-ac3375e42989`
- Status: `Succeeded`
- Window: 2026-08-15 03:26:01–03:27:36 UTC

| Table | Bounded source count | ADF rowsCopied | ADLS row verified |
|---|---:|---:|---:|
| customers | 1 | 1 | customer 2,001 |
| products | 1 | 1 | product 301 |
| orders | 1 | 1 | order 10,001 |
| payments | 1 | 1 | payment 9,991 |

The four raw files were read through Azure RBAC before the second run. Each had
one data row, the exact expected primary key, the test timestamp, and a recorded
SHA-256 digest. All four current watermarks advanced from their independent
baseline values to `2026-08-15 03:25:37.527343+00` only after count validation.

## Original second incremental run

- Run ID: `71f6b8db-482c-4827-ba29-377867bdecad`
- Status: `Succeeded`
- Window: 2026-08-15 03:37:37–03:38:59 UTC

Every bounded source count and `rowsCopied` value was zero. PostgreSQL counts,
test rows, confirmed orders, confirmed revenue, and all four watermark
timestamps remained unchanged. This proves source-window idempotency.

## Defect found

The deployed sink path is
`<table>/incremental/watermark=<upper-bound>/<table>.csv`. With no source
changes, the second run has the same upper bound and therefore targets the same
file. ADF wrote one header-only file per table and overwrote all four one-row
files from the first run. At that point the raw layer was one row behind
PostgreSQL for each table, although the pre-overwrite hashes and row evidence
were retained under `docs/evidence/`.

Extraction idempotency: **PASS**. Append-only raw retention: **FAIL**.

The deployed path needed a unique run component, specifically
`watermark=<upper-bound>/run_id=<pipeline().RunId>/<table>.csv`, before the raw
layer could truthfully be described as immutable or safely rerunnable. This
historical failure is retained as the defect reproduction evidence.

## Remediation deployment

Before deployment, the live factory was saved under
`azure/deployment/snapshots/pre-deployment-20260815T040252Z/`. A complete live-versus-local
comparison found exactly four functional differences: the `watermarkPath`
expression on the customers, products, orders, and payments incremental Copy
outputs. Each difference added only `/run_id=<pipeline().RunId>`.
Service-managed `lastPublishTime` values were ignored as non-functional
metadata.

Seven regression/static tests and deterministic validation passed before
deployment. The scoped deployment guard then published only
`PL_Incremental_Load`; immediate ARM readback matched the approved definition.
No dataset, linked service, other pipeline, PostgreSQL schema, Azure resource
configuration, or SKU was changed.

## Controlled recovery

The current watermark row was saved before reset. The pre-test values were
verified from the original non-zero-run Lookup evidence:

| Table | Restored lower watermark |
|---|---|
| customers | `2024-03-28 04:31:20+00` |
| products | `2023-05-29 00:00:00+00` |
| orders | `2025-07-03 06:58:08+00` |
| payments | `2025-07-09 12:40:20+00` |

A read-only PostgreSQL safety query found exactly one row in each recovery
window, each was the approved synthetic ID, and every unexpected-ID list was
empty. The control-file reset was read back with matching SHA-256 digest. A
temporary raw-container-only Blob Data Contributor assignment was required for
that write and was removed immediately after verification; the original
storage-account Reader assignment remains.

## Remediation retest 1 — non-zero

- Run ID: `a9a497e5-ab71-4d74-a62a-38212063e860`
- Status: `Succeeded`
- Window: 2026-08-15 04:10:29–04:11:56 UTC

| Table | Bounded source count | ADF `rowsCopied` | Exact key |
|---|---:|---:|---:|
| customers | 1 | 1 | 2001 |
| products | 1 | 1 | 301 |
| orders | 1 | 1 | 10001 |
| payments | 1 | 1 | 9991 |

All four files were read through Azure RBAC from
`<table>/incremental/watermark=20260815T0325375273430Z/run_id=a9a497e5-ab71-4d74-a62a-38212063e860/<table>.csv`.
Their row counts, exact keys, byte sizes, and SHA-256 digests were recorded.

## Remediation retest 2 — zero change

- Run ID: `59b6f800-bba5-485a-9a1b-7ba608c4997a`
- Status: `Succeeded`
- Window: 2026-08-15 04:12:30–04:13:51 UTC

Every bounded source count and `rowsCopied` value was zero. The four header-only
outputs were written under this second run ID, not the first run's paths. A
post-run readback proved that every first-run file still had the same path,
one-row count, exact primary key, byte size, and SHA-256 digest.

Final watermarks remained `2026-08-15 03:25:37.527343+00` for all four tables,
and the control row recorded the second run ID. Final PostgreSQL counts remained
2,001 customers, 301 products, 10,001 orders, and 9,991 payments. Confirmed
orders remained 8,941 and confirmed revenue remained `307,416,040.00`.

Append-only retention: **PASS**. Watermark idempotency: **PASS**. Source
idempotency: **PASS**.

## Final validation

- Seven of seven unit/static tests passed, including the run-ID path regression
  check, both before deployment and in the final pass.
- Deterministic full-data reconciliation passed.
- Actual ADLS full-load raw-to-curated/rejected reconciliation passed.
- Read-only PostgreSQL quality, reconciliation, and analytics SQL passed.
- Cross-file remediation evidence validation passed with no failures.
- The final secret scan found zero value-pattern matches and zero
  credential-file candidates.
- The resource group still contains only the original storage account,
  PostgreSQL Flexible Server, and Data Factory.
- No Azure resource was created or deleted, and no linked service, PostgreSQL
  row/schema, or service tier was changed. The temporary reset role was removed.

Primary evidence:

- `docs/evidence/incremental-nonzero-run.json`
- `docs/evidence/adls-nonzero-batch.json`
- `docs/evidence/incremental-idempotency-run.json`
- `docs/evidence/adls-after-idempotency-run.json`
- `docs/evidence/controlled-test-quality-summary.json`
- `docs/evidence/incremental-sink-remediation-deployment.json`
- `docs/evidence/incremental-remediation-first-run.json`
- `docs/evidence/incremental-remediation-first-batch.json`
- `docs/evidence/incremental-remediation-second-run.json`
- `docs/evidence/incremental-remediation-second-batch.json`
- `docs/evidence/incremental-remediation-first-batch-after-second.json`
- `docs/evidence/incremental-remediation-final-proof.json`
