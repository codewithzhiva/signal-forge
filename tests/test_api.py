import os
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(__file__).parent / "test.db")

from fastapi.testclient import TestClient

from app.main import app


def request(key: str = "payment-0001") -> dict:
    return {
        "tenant_id": "merchant-a",
        "idempotency_key": key,
        "subject_id": "customer-42",
        "features": {"amount": 1500, "velocity_1h": 7, "country_mismatch": False},
    }


def test_decision_is_explainable_and_idempotent():
    with TestClient(app) as client:
        first = client.post("/v1/decisions", json=request()).json()
        second = client.post("/v1/decisions", json=request()).json()
        assert first["id"] == second["id"]
        assert first["action"] == "reject"
        assert set(first["reason_codes"]) == {"high_amount", "high_velocity"}
        assert second["replayed"] is True


def test_shadow_and_replay_do_not_mutate_production():
    with TestClient(app) as client:
        candidate = client.post(
            "/v1/rules",
            json={
                "name": "strict",
                "conditions": [
                    {"field": "amount", "operator": "gte", "value": 100, "reason": "amount", "points": 80}
                ],
                "review_threshold": 30,
                "reject_threshold": 70,
                "mode": "candidate",
            },
        ).json()["id"]
        result = client.post("/v1/decisions", json=request("payment-0002")).json()
        assert result["rule_version_id"] == "rules-default-v1"
        assert any(s["rule_version_id"] == candidate for s in result["shadow_results"])
        replay = client.post("/v1/replay", json={"rule_version_id": candidate}).json()
        assert replay["evaluated"] >= 1


def test_unknown_activation_is_404():
    with TestClient(app) as client:
        assert client.post("/v1/rules/missing/activate").status_code == 404
