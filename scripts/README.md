# Automation scripts

Run modules from the repository root so package imports and relative paths are
consistent:

```powershell
python -m scripts.ingestion.generate_data
python -m scripts.validation.validate_generated_data
python -m scripts.deployment.build_adf_definitions
```

Folders are organized by responsibility:

- `ingestion/`: deterministic source generation and PostgreSQL load helpers.
- `transformation/`: raw-to-curated/rejected processing.
- `validation/`: reconciliation, ADLS inspection, and evidence checks.
- `deployment/`: ADF definition generation plus explicitly invoked Azure
  export, validation, deployment, run, and publication helpers.

Only `build_adf_definitions` is local-only within `deployment/`. Other scripts
in that folder can contact or mutate existing Azure resources and should be run
only with deliberate authorization. `load_to_postgres` can truncate local
tables; never point it at the completed Azure database.
