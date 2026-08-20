from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DATABASE_PATH", "signalforge.db")


class Condition(BaseModel):
    field: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "in"]
    value: Any
    reason: str
    points: int = Field(ge=0, le=100)


class RuleVersionCreate(BaseModel):
    name: str
    conditions: list[Condition] = Field(min_length=1)
    review_threshold: int = Field(default=40, ge=0, le=100)
    reject_threshold: int = Field(default=70, ge=0, le=100)
    mode: Literal["candidate", "active", "archived"] = "candidate"


class DecisionRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)
    subject_id: str = Field(min_length=1, max_length=256)
    features: dict[str, Any]


class ReplayRequest(BaseModel):
    rule_version_id: str
    limit: int = Field(default=100, ge=1, le=1000)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS rules (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, conditions_json TEXT NOT NULL,
          review_threshold INTEGER NOT NULL, reject_threshold INTEGER NOT NULL,
          mode TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON rules(mode) WHERE mode='active';
        CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
          subject_hash TEXT NOT NULL, features_json TEXT NOT NULL, score INTEGER NOT NULL,
          action TEXT NOT NULL, reasons_json TEXT NOT NULL, rule_version_id TEXT NOT NULL,
          shadow_json TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(tenant_id, idempotency_key)
        );
        """)
        if not conn.execute("SELECT 1 FROM rules").fetchone():
            conditions = json.dumps(
                [
                    {
                        "field": "amount",
                        "operator": "gte",
                        "value": 1000,
                        "reason": "high_amount",
                        "points": 35,
                    },
                    {
                        "field": "velocity_1h",
                        "operator": "gte",
                        "value": 5,
                        "reason": "high_velocity",
                        "points": 45,
                    },
                    {
                        "field": "country_mismatch",
                        "operator": "eq",
                        "value": True,
                        "reason": "country_mismatch",
                        "points": 30,
                    },
                ]
            )
            conn.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?,?,?)",
                ("rules-default-v1", "default", conditions, 40, 70, "active", datetime.now(UTC).isoformat()),
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="SignalForge", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def ready() -> dict[str, str]:
    with db() as conn:
        conn.execute("SELECT 1")
    return {"status": "ready"}


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    with db() as conn:
        rows = conn.execute("SELECT action, COUNT(*) AS total FROM decisions GROUP BY action").fetchall()
    lines = [f'signalforge_decisions_total{{action="{row["action"]}"}} {row["total"]}' for row in rows]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.post("/v1/rules", status_code=201)
def create_rule(rule: RuleVersionCreate) -> dict[str, str]:
    payload = json.dumps([c.model_dump() for c in rule.conditions], sort_keys=True)
    rule_id = "rules-" + hashlib.sha256(f"{rule.name}:{payload}".encode()).hexdigest()[:12]
    with db() as conn:
        if rule.mode == "active":
            conn.execute("UPDATE rules SET mode='archived' WHERE mode='active'")
        conn.execute(
            "INSERT OR IGNORE INTO rules VALUES (?,?,?,?,?,?,?)",
            (
                rule_id,
                rule.name,
                payload,
                rule.review_threshold,
                rule.reject_threshold,
                rule.mode,
                datetime.now(UTC).isoformat(),
            ),
        )
    return {"id": rule_id, "mode": rule.mode}


@app.post("/v1/rules/{rule_id}/activate")
def activate(rule_id: str) -> dict[str, str]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM rules WHERE id=?", (rule_id,)).fetchone():
            raise HTTPException(404, "rule version not found")
        conn.execute("UPDATE rules SET mode='archived' WHERE mode='active'")
        conn.execute("UPDATE rules SET mode='active' WHERE id=?", (rule_id,))
    return {"id": rule_id, "mode": "active"}


@app.post("/v1/decisions", status_code=201)
def decide(request: DecisionRequest) -> dict[str, Any]:
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM decisions WHERE tenant_id=? AND idempotency_key=?",
            (request.tenant_id, request.idempotency_key),
        ).fetchone()
        if existing:
            return decision_dict(existing) | {"replayed": True}
        active = conn.execute("SELECT * FROM rules WHERE mode='active'").fetchone()
        if not active:
            raise HTTPException(503, "no active rule version")
        score, action, reasons = evaluate(active, request.features)
        shadows = []
        for candidate in conn.execute("SELECT * FROM rules WHERE mode='candidate'").fetchall():
            s_score, s_action, s_reasons = evaluate(candidate, request.features)
            shadows.append(
                {
                    "rule_version_id": candidate["id"],
                    "score": s_score,
                    "action": s_action,
                    "reasons": s_reasons,
                    "changed": s_action != action,
                }
            )
        decision_id = (
            "dec-"
            + hashlib.sha256(f"{request.tenant_id}:{request.idempotency_key}".encode()).hexdigest()[:16]
        )
        subject_hash = hashlib.sha256(request.subject_id.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                request.tenant_id,
                request.idempotency_key,
                subject_hash,
                json.dumps(request.features, sort_keys=True),
                score,
                action,
                json.dumps(reasons),
                active["id"],
                json.dumps(shadows),
                now,
            ),
        )
        row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return decision_dict(row) | {"replayed": False}


@app.post("/v1/replay")
def replay(request: ReplayRequest) -> dict[str, Any]:
    with db() as conn:
        rule = conn.execute("SELECT * FROM rules WHERE id=?", (request.rule_version_id,)).fetchone()
        if not rule:
            raise HTTPException(404, "rule version not found")
        decisions = conn.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (request.limit,)
        ).fetchall()
    transitions: dict[str, int] = {}
    changed = 0
    for item in decisions:
        _, new_action, _ = evaluate(rule, json.loads(item["features_json"]))
        transition = f"{item['action']}->{new_action}"
        transitions[transition] = transitions.get(transition, 0) + 1
        changed += int(item["action"] != new_action)
    return {
        "rule_version_id": rule["id"],
        "evaluated": len(decisions),
        "changed": changed,
        "transitions": transitions,
    }


@app.get("/v1/decisions")
def list_decisions(limit: int = 50) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (min(limit, 200),)
        ).fetchall()
    return [decision_dict(row) for row in rows]


def evaluate(rule: sqlite3.Row, features: dict[str, Any]) -> tuple[int, str, list[str]]:
    score, reasons = 0, []
    for raw in json.loads(rule["conditions_json"]):
        if matches(features.get(raw["field"]), raw["operator"], raw["value"]):
            score = min(100, score + raw["points"])
            reasons.append(raw["reason"])
    action = (
        "reject"
        if score >= rule["reject_threshold"]
        else "review"
        if score >= rule["review_threshold"]
        else "approve"
    )
    return score, action, reasons


def matches(actual: Any, operator: str, expected: Any) -> bool:
    if actual is None:
        return False
    operations = {
        "gt": lambda: actual > expected,
        "gte": lambda: actual >= expected,
        "lt": lambda: actual < expected,
        "lte": lambda: actual <= expected,
        "eq": lambda: actual == expected,
        "in": lambda: actual in expected,
    }
    try:
        return operations[operator]()
    except (TypeError, KeyError):
        return False


def decision_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "score": row["score"],
        "action": row["action"],
        "reason_codes": json.loads(row["reasons_json"]),
        "rule_version_id": row["rule_version_id"],
        "shadow_results": json.loads(row["shadow_json"]),
        "created_at": row["created_at"],
    }
