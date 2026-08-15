# Safe live demo guide

Do not improvise credential screens. Keep this order so the demo remains
evidence-based and safe.

## Before the interview

- Close terminals containing environment variables or command history with
  credentials.
- Confirm the active Azure subscription and resource group without opening
  access keys.
- Prepare read-only SQL in a query window; never paste a password on screen.
- Use a user-level pgpass file and `sslmode=require`.
- Keep the two successful ADF run IDs and the versioned ADLS path available in
  the evidence documents; do not expose linked-service secure fields.

## Demo sequence

1. Open the repository README and state the verified/current boundary.
2. Show the Mermaid architecture and explain the three lake layers.
3. Show `database/ddl/01_create_tables.sql` and the four actual table shapes.
4. Run read-only row-count and KPI queries.
5. Open ADF Author and show `PL_Initial_Full_Load`.
6. Open `Copy_Customers_To_Raw`, source/sink dataset names, and mappings.
7. Show the live products/orders/payments activities and successful full-load
   run `4d23b5f2-84d3-4a6f-955c-8132bd887cc9`.
8. Show the incremental Lookup → bounded Copy → count guard → watermark flow.
9. Open ADLS `raw/customers/customers.csv` properties and show size/path; do not
   display customer rows unnecessarily.
10. Show curated/rejected processing code and a summary JSON, not personal row
    contents.
11. Run `python -m unittest discover -s tests -v`.
12. Show the rejected-file schema and aggregate reason counts; avoid displaying
    customer data or unnecessary full rows.
13. Run the executive KPI query and one product or payment-status query.
14. Finish with limitations and production improvements.

## Controlled remediation evidence

1. Confirm no trigger is active.
2. Revalidate the already deployed pipeline in ADF Studio.
3. Do not insert another test batch. Open the two recorded controlled run IDs in
   Monitor and show activity names, status, duration, rows read, and rows copied.
4. Show that the first run copied one row per table and the second copied zero.
5. Use remediation runs `a9a497e5-ab71-4d74-a62a-38212063e860` and
   `59b6f800-bba5-485a-9a1b-7ba608c4997a` to show the separate `run_id` paths.
6. Show that the first run's one-row files kept their exact IDs, sizes, and
   hashes after the second run wrote separate header-only files.
7. Do not insert another test batch or trigger another run without approval.

## Never open or click during the demo

- Storage account **Access keys** or **Shared access signature**.
- Linked-service connection-string edit fields or password fields.
- PostgreSQL reset-password controls.
- Browser developer tools showing authorization headers.
- `pgpass.conf`, `.env`, Azure CLI token output, or shell environment dumps.
- ADF **Publish all** or **Trigger now** unless that action is approved.
- Delete, purge, truncate, firewall broadening, SKU, HA, backup redundancy, or
  networking change controls.

## Honest closing statement

State that bounded extraction, count reconciliation, watermarks,
source-window idempotency, and append-only raw retention all passed after the
run-ID remediation. Keep the original failed runs as the precise defect trail,
then show the two successful remediation retests.
