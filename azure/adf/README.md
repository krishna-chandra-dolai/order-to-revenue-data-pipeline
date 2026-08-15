# Azure Data Factory definitions

Status: generated, validated, published, and retested. The live incremental
pipeline includes the run-ID sink-path remediation discovered during the
2026-08-15 controlled test.

Run `python -m scripts.deployment.build_adf_definitions` from the repository root to
rebuild this folder from the verified PostgreSQL schemas.

## Existing live objects preserved

- `LS_AzurePostgreSQL_OrderRevenue`
- `LS_ADLS_OrderRevenue`
- `PL_Initial_Full_Load`
- `DS_PG_Customers`
- `DS_ADLS_Customers_Raw`
- `Copy_Customers_To_Raw`

The checked-in customers activity mirrors the live published activity. Linked
service bodies are deliberately not exported because they can contain encrypted
credential material.

## Deployed datasets

Full load:

- `DS_PG_Customers`, `DS_PG_Products`, `DS_PG_Orders`, `DS_PG_Payments`
- `DS_ADLS_Customers_Raw`, `DS_ADLS_Products_Raw`
- `DS_ADLS_Orders_Raw`, `DS_ADLS_Payments_Raw`

Incremental/control:

- `DS_PG_Query`
- `DS_ADLS_Incremental_Raw`
- `DS_ADLS_Watermark_Control`

## Deployed pipelines

`PL_Initial_Full_Load` keeps the customers copy, adds products/orders/payments
copies, and writes initial per-table watermarks only after all four copies
succeed.

`PL_Incremental_Load` reads the current control file, fixes one upper watermark
per table, copies `(old, new]`, checks the bounded source counts against
`rowsCopied`, writes watermark history, and only then advances the current file.
The generated sink folder includes `run_id=<pipeline().RunId>` so a no-change
run cannot overwrite the prior batch. This correction is live and verified.

## Deployment evidence

The nine new datasets were deployed before the two pipeline definitions. The
full load and zero-row incremental baseline both succeeded. Exact run IDs and
counts are recorded in `docs/results/AZURE_EXECUTION_RESULTS.md`.

The original controlled non-zero/idempotency test exposed that the deployed
watermark-only path overwrote a prior file when the upper watermark was
unchanged. The live factory was saved, exactly four approved sink expressions
were published in `PL_Incremental_Load`, and two remediation retests proved
one-row recovery plus a separate zero-row run without overwriting the first.

The manifest names the exact resource group, factory, linked services, and
deployment order. The files contain no secrets. Evidence is recorded in
`docs/evidence/incremental-sink-remediation-deployment.json` and
`docs/evidence/incremental-remediation-final-proof.json`.
