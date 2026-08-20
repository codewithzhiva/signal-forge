# Architecture

FastAPI owns the synchronous decision boundary. The active rule is evaluated in-process so a queue outage cannot silently change decisions. Each request is committed with its version and evidence before returning. Candidate versions execute in shadow and are stored alongside the active result.

Redis and Kafka-compatible Redpanda are explicit integration boundaries for derived features and asynchronous outcomes. v0.1 keeps them off the correctness path; later adapters must preserve deterministic replay.
