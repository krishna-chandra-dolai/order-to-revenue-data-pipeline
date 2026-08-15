# Database assets

SQL is grouped by intent:

- `ddl/` contains the schema and constraints used for local reproduction.
- `queries/` contains read-only checks, watermark examples, and the explicitly
  controlled incremental test SQL.
- `analytics/` contains the read-only order-to-revenue analysis queries.

Review a script before running it. In particular,
`queries/08_controlled_incremental_test.sql` inserts four retained test rows and
is preserved as historical test evidence; it is not a general setup script.
The Python PostgreSQL loader also performs a deliberate local-development
truncate and reload and must not be pointed at the completed Azure database.
