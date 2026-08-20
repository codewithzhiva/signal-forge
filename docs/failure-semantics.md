# Failure semantics

- Duplicate tenant/idempotency pairs return the original record.
- With no active rule version, the service fails closed with `503`.
- Candidate failure never changes the active decision.
- Activation archives the previous active version in the same transaction.
- Subject identifiers are stored only as SHA-256 fingerprints in the reference implementation.
