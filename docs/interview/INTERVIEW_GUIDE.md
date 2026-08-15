# Interview guide

## 30-second explanation

I built a cost-conscious order-to-revenue pipeline on Azure. PostgreSQL is the
operational source, Azure Data Factory copies four tables into ADLS Gen2, and a
Python quality layer separates curated rows from rejected rows with explicit
reasons. Incremental loads use each table's indexed `updated_at` column and only
advance the watermark after all bounded counts match. PostgreSQL SQL then
calculates revenue using completed orders and the latest successful matching
payment.

## One-minute explanation

The source has customers, products, orders, and payments. I first inspected the
live catalog rather than assuming the schema. All tables have non-null
`updated_at` values and composite watermark indexes. The existing customers ADF
copy was preserved and used as the template for three deployed full-load copies.
For incrementals, ADF reads per-table watermarks from ADLS, captures fixed upper
bounds, copies `(old, new]`, validates source counts against `rowsCopied`, writes
history, and advances current state only after complete success. Python applies
the database constraints and the confirmed-revenue rule, retaining every bad or
superseded row with a reason. I deliberately avoided a new warehouse or Spark
cluster because the data volume does not justify the cost.

## Three-minute explanation

The Azure resource group already contained PostgreSQL Flexible Server, Data
Factory, and ADLS Gen2. Only the customers copy was live. I verified its pipeline
JSON, datasets, linked services, raw CSV header, and 2,000-row reconciliation.
Then I inspected the database through TLS. After the approved controlled test,
it contains 2,001 customers, 301 products, 10,001 orders, and 9,991 payments.

The full-load definition keeps `Copy_Customers_To_Raw` and adds products,
orders, and payments using exact source fields. The incremental design maintains
one watermark per table because their update timelines differ. A Lookup gets the
old boundary, another Lookup fixes the new maximum and bounded count, and Copy
writes a timestamp-versioned raw file. The control file is updated only after
all four counts match. The original controlled rerun proved the bounded query is
retryable and exposed the need for a run ID. The deployed run-ID path was then
proved with separate one-row and zero-row outputs and unchanged first-run
hashes.

The Python quality processor checks required fields, types, allowed values,
positive prices, timestamp order, relationships, quantities, duplicates, and
payment amount reconciliation. It writes every record to curated or rejected.
Failed or pending payments stay curated business events but do not become
revenue. Analytics choose the latest payment attempt and count revenue only when
the order is completed, references are valid, payment is successful, and the
amount matches the order.

The design is intentionally small: existing ADF, PostgreSQL, ADLS, and standard
Python. Production improvements would include CDC, private endpoints, managed
identity database authentication, orchestration for the Python transform,
monitoring, and alerting.

## Detailed end-to-end flow

1. Applications write the current state to PostgreSQL.
2. Full load copies all four tables to stable raw CSV paths.
3. Full-load success initializes the four watermarks.
4. Incremental Lookup activities read old watermarks and capture new boundaries.
5. Copy activities extract only `(old, new]` rows to versioned raw paths.
6. ADF compares each bounded source count with `rowsCopied`.
7. On complete success it writes control history and advances current state.
8. Python selects latest primary-key versions and applies quality rules.
9. Valid current rows go to curated; all other rows retain rejection reasons.
10. Reconciliation checks source/raw and raw/curated/rejected counts.
11. PostgreSQL SQL produces revenue, customer, product, location, order, and
    payment analysis.

## Why each technology

- PostgreSQL: relational constraints and efficient operational queries.
- ADF: managed orchestration and connectors already present in the project.
- ADLS Gen2: inexpensive layered object storage; a unique run-ID path is still
  required before the incremental history is append-only.
- Python standard library: transparent validation without another paid compute
  service.
- PostgreSQL analytics: sufficient for the current volume and avoids provisioning
  a warehouse.

## Failure handling

- Copy failures stop downstream dependencies.
- Count mismatch follows an explicit Fail activity.
- Watermark history is written before current state.
- Current state advances only on the true validation branch.
- Versioned raw files preserve evidence.
- Latest-key deduplication protects curated output on retries.
- Rejected rows keep the original fields and readable reasons.

## Security and cost

Credentials remain in the existing linked services or user-level pgpass file.
The repository contains no passwords, keys, or connection strings. Inspection
used TLS and Azure RBAC. High availability, geo-redundancy, premium services,
and new analytics platforms were not enabled. ADF runs and storage transactions
are still billable, so execution requires approval and is limited to deliberate
tests.

## Common questions and truthful answers

### Why use a timestamp watermark instead of CDC?

Every source table already has an indexed, non-null `updated_at`, so a watermark
is simple and appropriate for this portfolio volume. CDC is stronger for deletes
and late commits and is the preferred production improvement.

### Why one watermark per table?

Customers, products, orders, and payments have different maximum timestamps.
Independent boundaries prevent a fast-moving table from skipping another
table's updates.

### Why store control state in ADLS rather than PostgreSQL?

The live database has no control table and the working ADF PostgreSQL linked
service is version 1. Lookup and Copy are supported without changing that linked
service or writing into the operational database.

### What is idempotent here?

Failed runs retain the old watermark, incremental raw paths include the upper
watermark, and curated processing chooses the latest primary-key version. A
retry therefore does not create duplicate curated keys.

### Are failed payments rejected?

No. A failed payment can be structurally valid and useful for operations. It is
curated but excluded from confirmed revenue. Structural failures and amount
mismatches are rejected.

### What has actually run in Azure?

The four-table full load succeeded and reconciled 2,000 customers, 300 products,
10,000 orders, and 9,990 payments. A controlled test then added one retained row
per table and exposed that a watermark-only path let a zero-change run overwrite
the prior files. I added a unique run-ID folder, published only the incremental
pipeline, restored the documented lower watermarks after a recovery-window
safety check, and reran the test. The first remediation run copied one exact row
per table; the second copied zero into different paths. Hash readback proved the
first files were unchanged, so source, watermark, and append-only idempotency all
pass.

### What would you change in production?

Use CDC or a transactional snapshot, private networking, managed identity or
Key Vault-backed authentication, automated transform compute, Parquet instead of
CSV, centralized run metadata, alerts, retention policies, and CI/CD promotion
across environments.
