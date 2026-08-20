"""Local HTTP latency benchmark; starts no services and makes no claims by itself."""

import json
import statistics
import time
import urllib.request

latencies = []
for i in range(200):
    body = json.dumps(
        {
            "tenant_id": "benchmark",
            "idempotency_key": f"benchmark-{i:08d}",
            "subject_id": f"s-{i}",
            "features": {"amount": i * 10, "velocity_1h": i % 8},
        }
    ).encode()
    request = urllib.request.Request(
        "http://localhost:8000/v1/decisions", data=body, headers={"Content-Type": "application/json"}
    )
    start = time.perf_counter()
    urllib.request.urlopen(request).read()
    latencies.append((time.perf_counter() - start) * 1000)
latencies.sort()
print(
    {
        "requests": len(latencies),
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[int(len(latencies) * 0.95) - 1],
        "p99_ms": latencies[int(len(latencies) * 0.99) - 1],
    }
)
