# Controlled non-zero incremental test proposal

Status: **approved and executed on 2026-08-15**.

The bounded extraction and watermark checks passed, but the original second
no-change run exposed a same-watermark raw-file overwrite defect. That defect
has now been remediated and both controlled retests pass. See
[`CONTROLLED_INCREMENTAL_TEST_RESULTS.md`](../results/CONTROLLED_INCREMENTAL_TEST_RESULTS.md)
for the original failure, deployment, and final proof. The four approved rows
were retained.

The zero-change ADF baseline is verified. Proving a non-zero bounded load now
requires PostgreSQL source mutation, so this proposal is deliberately separated
from the completed deployment.

## Read-only preconditions verified

At the end of the approved stage, the live maximum keys were:

| Table | Maximum key | Proposed test key |
|---|---:|---:|
| customers | 2,000 | 2,001 |
| products | 300 | 301 |
| orders | 10,000 | 10,001 |
| payments | 9,990 | 9,991 |

The execution must recheck that all four proposed keys are unused and abort the
transaction if any conflict exists.

## Exact proposed rows and logic

Use one PostgreSQL transaction. `transaction_timestamp()` supplies one identical
UTC-aware timestamp, called `test_ts` below, for every created/updated field.
The transaction returns and records that exact value before ADF is run.

| Table | Proposed values |
|---|---|
| customers | `customer_id=2001`, name `Incremental Test Customer`, email `incremental.test.2001@example.invalid`, city `Test City`, state `Test State`, `created_at=test_ts`, `updated_at=test_ts` |
| products | `product_id=301`, name `Incremental Test Product`, category `Test Category`, brand `Test Brand`, `unit_price=1.00`, `created_at=test_ts`, `updated_at=test_ts` |
| orders | `order_id=10001`, `customer_id=2001`, `product_id=301`, `quantity=1`, `unit_price=1.00`, `order_status=pending`, `created_at=test_ts`, `updated_at=test_ts` |
| payments | `payment_id=9991`, `order_id=10001`, `payment_amount=1.00`, `payment_method=UPI`, `payment_status=pending`, `payment_time=test_ts`, `updated_at=test_ts` |

The foreign-key insertion order is customers, products, orders, payments. The
pending statuses keep the test order out of confirmed-revenue analytics.

## Expected proof

- bounded source count and ADF `rowsCopied` are exactly one for every table;
- all four rows land in one new, versioned incremental raw path per table;
- all four rows pass structural quality checks and enter curated;
- no new rejects are expected;
- each watermark advances from its current value to `test_ts` only after all
  four reconciliations pass; and
- confirmed revenue remains unchanged.

## Cleanup and risk

The safest choice is to retain these four clearly named synthetic rows. Removing
them would require deleting payment, order, product, then customer. Timestamp
watermark loading does not capture deletes, so that cleanup would leave the lake
and source inconsistent unless delete capture or a separately approved rebaseline
were also performed. A database rollback cannot be used after ADF observes the
rows because the transaction must already be committed.

Risks are limited but real: four source rows persist, table counts increase by
one each, and all four watermarks advance to `test_ts`. Key conflicts, partial
insertion, and foreign-key failures are controlled by the single transaction and
precondition checks. This test required separate approval before any SQL was run.
