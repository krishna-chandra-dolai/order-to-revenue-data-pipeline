# Validation results

Validation date: 2026-08-14 (Asia/Calcutta)

## Continuation verification — 2026-08-15

A read-only continuation check confirmed that Azure authentication remains
enabled and that `rg-order-revenue-dev` still contains only the expected
StorageV2 account, PostgreSQL Flexible Server, and Data Factory. The live ADF
inventory remains two pipelines, eleven datasets, and two linked services. The
recorded full-load and zero-change incremental runs both still report
`Succeeded` through the Azure Resource Manager API.

The sanitized ADF definitions were regenerated and all seven unit/static tests
passed. A fresh deterministic local run also passed generation validation,
raw-to-curated/rejected processing, and output reconciliation with the expected
counts: 2,000 customers, 300 products, 10,000 orders, and 9,990 payments; 47
orders and 47 payments were rejected with reasons. No PostgreSQL or ADLS data
was changed during this continuation check.

## Azure inventory

- Subscription state: enabled.
- Resource group `rg-order-revenue-dev`: succeeded, Central India.
- Storage `stordrevdata26081401`: StorageV2 Standard LRS, HNS enabled, HTTPS
  required, TLS 1.2 minimum, public blob access disabled.
- PostgreSQL `pg-order-revenue-26081401`: version 17, Ready, Burstable
  `Standard_B1ms`, 32 GiB P4, HA disabled, geo-redundant backup disabled.
- Data Factory `adf-order-revenue-26081401`: succeeded, system-assigned identity.
- No ADF Git repository configuration and no triggers were found.

## Live ADF inventory after approved deployment

- Linked services: `LS_AzurePostgreSQL_OrderRevenue`, `LS_ADLS_OrderRevenue`.
- Datasets: eleven total; the two original customer datasets plus nine approved
  datasets listed in `docs/results/AZURE_EXECUTION_RESULTS.md`.
- Pipelines: `PL_Initial_Full_Load`, `PL_Incremental_Load`.
- Full-load run `4d23b5f2-84d3-4a6f-955c-8132bd887cc9`: Succeeded.
- Incremental baseline run `c1a0e615-3b53-480c-84a6-d2d2909ac6a0`: Succeeded.

## PostgreSQL evidence

Connection used `sslmode=require`; PostgreSQL reported TLS 1.3. Every inspection
transaction was read-only.

| Table | Rows | Minimum `updated_at` | Maximum `updated_at` | Null watermarks |
|---|---:|---|---|---:|
| customers | 2,000 | 2023-01-02 23:54:08+00 | 2024-03-28 04:31:20+00 | 0 |
| products | 300 | 2023-01-01 00:00:00+00 | 2023-05-29 00:00:00+00 | 0 |
| orders | 10,000 | 2024-01-01 22:59:01+00 | 2025-07-03 06:58:08+00 | 0 |
| payments | 9,990 | 2024-01-02 01:23:31+00 | 2025-07-09 12:40:20+00 | 0 |

Quality classification:

| Result | Orders |
|---|---:|
| Confirmed revenue | 8,941 |
| Failed payment | 25 |
| Invalid quantity | 12 |
| Missing customer reference | 10 |
| Missing product reference | 10 |
| No payment | 10 |
| Payment amount mismatch | 15 |
| Pending payment | 303 |
| Refunded payment | 674 |

Confirmed revenue is `307,416,040.00`; average confirmed order value is
`34,382.74`.

## ADLS evidence

An account-scoped `Storage Blob Data Reader` role was added for the signed-in
user. No storage key was retrieved.

- `raw/customers/customers.csv`: 262,365 bytes, expected header, 2,000 data rows.
- `raw/products/products.csv`: 37,208 bytes, 300 data rows.
- `raw/orders/orders.csv`: 922,769 bytes, 10,000 data rows.
- `raw/payments/payments.csv`: 955,625 bytes, 9,990 data rows.
- Four versioned incremental baseline CSVs exist with expected headers and zero
  data rows.
- Current and historical watermark files each contain one control row.
- `raw/customers` is a zero-byte marker blob.
- `curated` contains four versioned full-load CSVs with 2,000 customer, 300
  product, 9,953 order, and 9,943 payment rows.
- `rejected` contains four matching CSVs with 0 customer, 0 product, 47 order,
  and 47 payment rows. Every non-empty rejected row has a reason.
- Every uploaded blob was read back and matched the local byte stream and
  SHA-256 digest.
- The raw inventory was unchanged by curated/rejected publication.

All source/raw and raw/curated/rejected reconciliations passed.

## Local implementation evidence

- Seven unit/static tests passed after adding the run-ID path regression check.
- All generated ADF JSON parsed successfully and resolved only known datasets
  and the two existing linked services.
- The deterministic full-data quality test produced 47 rejected orders and 47
  rejected payments; customers and products had zero rejects.
- All local per-table equations `raw = curated + rejected` passed.
- Duplicate-version test kept the latest customer version, rejected the older
  version with an explicit reason, and retained exact count reconciliation.
- `database/analytics/07_analytics_queries.sql` executed successfully against the live
  database with `default_transaction_read_only=on`; result rows were suppressed
  except for the separately recorded KPI summary.

## Controlled incremental result — 2026-08-15

The approved transaction retained one new row per table. Counts changed to
2,001 customers, 301 products, 10,001 orders, and 9,991 payments. All target IDs
and unique values were absent beforehand, the relationships passed, and every
row used `2026-08-15 03:25:37.527343+00` as its watermark timestamp. Confirmed
revenue remained `307,416,040.00`.

Run `543b70bb-1ba9-4571-9f90-ac3375e42989` succeeded with bounded counts and
`rowsCopied` equal to one for all four tables. The four ADLS files were read
back before the next run; exact keys, one-row counts, timestamps, and hashes
passed. All four watermarks advanced to the test timestamp.

Run `71f6b8db-482c-4827-ba29-377867bdecad` then succeeded with bounded counts
and `rowsCopied` equal to zero for all four tables. This proves source-window
idempotency. It also exposed a raw-retention failure: the unchanged upper
watermark produced the same sink paths, and the four header-only outputs
overwrote the prior one-row files. This is retained as the historical defect
reproduction.

## Incremental sink remediation and final proof — 2026-08-15

Rollback snapshot `azure/deployment/snapshots/pre-deployment-20260815T040252Z/` captured
the complete live ADF definition set before deployment. The deployment guard
found exactly four functional differences, all in the incremental Copy
`watermarkPath` expressions, and published only `PL_Incremental_Load`. Live
readback confirmed this path pattern for every table:

`<table>/incremental/watermark=<timestamp>/run_id=<pipeline-run-id>/<table>.csv`

The current watermark state was saved before reset. A read-only recovery-window
check proved that each table had exactly one eligible source row, it was the
approved synthetic ID, and there were no unexpected rows. The exact documented
pre-test control values were restored and hash-verified.

Retest `a9a497e5-ab71-4d74-a62a-38212063e860` succeeded with bounded counts and
`rowsCopied` equal to one for customers, products, orders, and payments. RBAC
readback verified exact IDs 2001, 301, 10001, and 9991 under that run ID.

Without changing PostgreSQL, retest `59b6f800-bba5-485a-9a1b-7ba608c4997a`
succeeded with all bounded counts and `rowsCopied` equal to zero. Its four
header-only files use the second run ID. The first run's four files were read
again and matched their original paths, row counts, primary keys, byte sizes,
and SHA-256 hashes.

Final status:

- append-only retention: PASS;
- watermark idempotency: PASS;
- source idempotency: PASS;
- final source counts: 2,001 / 301 / 10,001 / 9,991;
- confirmed orders and revenue: 8,941 and `307,416,040.00`;
- regression/static tests: 7/7 PASS;
- deterministic and actual-ADLS reconciliation: PASS;
- read-only PostgreSQL quality/reconciliation/analytics SQL: PASS;
- cross-file evidence validation: PASS;
- secret scan: PASS with zero matches.

See `docs/results/CONTROLLED_INCREMENTAL_TEST_RESULTS.md` and
`docs/evidence/incremental-remediation-final-proof.json` for the full proof.

Temporary container-scoped Blob Data Contributor assignments used for the live
curated/rejected publication and the controlled watermark reset were removed
after read-back verification. The pre-existing storage-account-scoped Blob Data
Reader assignment remains.
