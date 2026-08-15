# Data workspace

`generated/` is an ignored local output directory for deterministic synthetic
CSV data. Only its placeholder is versioned; regenerate the data with:

```powershell
python -m scripts.ingestion.generate_data
python -m scripts.ingestion.add_incremental_batch
```

No production or PostgreSQL export data belongs in this repository.
