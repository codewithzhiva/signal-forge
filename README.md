# SignalForge

An explainable, versioned real-time risk decision platform. SignalForge treats decisions as durable engineering artifacts: every outcome names its rule version, score, reason codes, and shadow results.

## Quick start

```bash
docker compose up --build
```

- API and OpenAPI: <http://localhost:8000/docs>
- Decision dashboard: <http://localhost:8081>

## What is implemented

- Deterministic rules with approve/review/reject thresholds.
- Tenant-scoped idempotency and privacy-safe subject hashing.
- Immutable decision evidence.
- Candidate shadow evaluation without production mutation.
- Historical replay and atomic rule activation/rollback.
- Redis and Redpanda are included as integration boundaries for online features and outcome events; the laptop reference path remains deterministic when they are unavailable.

## Guarantees and limitations

An idempotency key always returns its first recorded decision. Shadow versions cannot affect the active outcome. v0.1 uses SQLite WAL for the reference deployment and a deterministic rule engine; model serving and distributed adapters are extension points, not fabricated capabilities.

See [architecture](docs/architecture.md), [failure semantics](docs/failure-semantics.md), and [benchmark policy](docs/benchmarks.md).

Apache-2.0 licensed.
