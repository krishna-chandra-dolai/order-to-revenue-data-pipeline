# Linked services

The pipelines and datasets reuse the existing Azure Data Factory linked
services `LS_AzurePostgreSQL_OrderRevenue` and `LS_ADLS_OrderRevenue`.
Definitions are intentionally not exported here because live linked-service
payloads may contain credential material. Dataset and pipeline references are
validated by the test suite.
