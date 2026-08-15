# Deployment evidence

`snapshots/` contains sanitized, read-only captures taken before the documented
ADF changes. They are retained for rollback comparison and implementation
evidence. Linked-service secret values were not persisted.

The executable helpers live in `scripts/deployment/`. Nothing in this folder
deploys automatically.
