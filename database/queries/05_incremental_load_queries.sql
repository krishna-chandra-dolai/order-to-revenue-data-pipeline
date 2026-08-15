-- Legacy local Phase 2 simulation only. This file is not the proposed Azure
-- control implementation and has not been applied to the live database. The
-- proposed ADF design stores independent table watermarks in ADLS _control.
--
-- A single control row stores the upper boundary of the last successful run.
-- The watermark advances in the same transaction as successful validation, so
-- a failed run can safely retry the same (old_watermark, new_watermark] window.
CREATE TABLE IF NOT EXISTS pipeline_watermark (
    pipeline_name VARCHAR(80) PRIMARY KEY,
    last_successful_watermark TIMESTAMPTZ NOT NULL,
    last_run_completed_at TIMESTAMPTZ NULL,
    last_order_rows INTEGER NOT NULL DEFAULT 0,
    last_payment_rows INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT pipeline_watermark_nonnegative_counts CHECK (
        last_order_rows >= 0 AND last_payment_rows >= 0
    )
);

COMMENT ON TABLE pipeline_watermark IS
    'Current high watermark for each incremental pipeline; not an operational source table.';

-- The runner supplies these variables after it fixes the extraction boundary.
-- Both source queries use the same half-open interval: (old, new].
\if :{?old_watermark}
    \if :{?new_watermark}
        DROP TABLE IF EXISTS incremental_orders;
        CREATE TEMP TABLE incremental_orders AS
        SELECT *
        FROM orders
        WHERE updated_at > :'old_watermark'::timestamptz
          AND updated_at <= :'new_watermark'::timestamptz;

        DROP TABLE IF EXISTS incremental_payments;
        CREATE TEMP TABLE incremental_payments AS
        SELECT *
        FROM payments
        WHERE updated_at > :'old_watermark'::timestamptz
          AND updated_at <= :'new_watermark'::timestamptz;
    \endif
\endif
