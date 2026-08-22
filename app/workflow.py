"""Persistent human-approval workflow and audit log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.connectors import ConnectorError, HubSpotConnector


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionStore:
    def __init__(self, database_path: Path, connector: HubSpotConnector):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self.connector = connector
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_key TEXT NOT NULL UNIQUE,
                    action_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    target_count INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES actions(id)
                );
                """
            )

    def seed(self, suggestions: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            for item in suggestions:
                connection.execute(
                    """
                    INSERT INTO actions (
                        action_key, action_type, title, summary, evidence_json,
                        recommended_action, risk, target_count, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_key) DO UPDATE SET
                        action_type = excluded.action_type,
                        title = excluded.title,
                        summary = excluded.summary,
                        evidence_json = excluded.evidence_json,
                        recommended_action = excluded.recommended_action,
                        risk = excluded.risk,
                        target_count = excluded.target_count,
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at
                    WHERE actions.status = 'pending'
                    """,
                    (
                        item["action_key"],
                        item["type"],
                        item["title"],
                        item["summary"],
                        json.dumps(item["evidence"]),
                        item["recommended_action"],
                        item["risk"],
                        item["target_count"],
                        json.dumps(item["payload"]),
                        _now(),
                    ),
                )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["type"] = item.pop("action_type")
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        result = item.pop("result_json")
        item["result"] = json.loads(result) if result else None
        return item

    def list_actions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM actions ORDER BY id").fetchall()
        return [self._decode(row) for row in rows]

    def decide(self, action_id: int, decision: str) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        with self._connect() as connection:
            # Serialize the read-decision-write sequence so two concurrent UI
            # requests cannot invoke the connector for the same action twice.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown action {action_id}")
            action = self._decode(row)
            if action["status"] != "pending":
                raise ValueError("Only pending actions can be decided")
            if decision == "reject":
                result = {"external_write": False, "message": "Action rejected; no connector was invoked."}
                status = "rejected"
            else:
                try:
                    result = self.connector.execute(action)
                    status = "approved"
                except ConnectorError as exc:
                    result = {"external_write": False, "message": str(exc), "connector_error": True}
                    status = "failed"
            decided_at = _now()
            connection.execute(
                "UPDATE actions SET status = ?, result_json = ?, decided_at = ? WHERE id = ?",
                (status, json.dumps(result), decided_at, action_id),
            )
            connection.execute(
                "INSERT INTO audit_log(action_id, event, details_json, created_at) VALUES (?, ?, ?, ?)",
                (action_id, decision, json.dumps(result), decided_at),
            )
            updated = connection.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        return self._decode(updated)

    def audit_log(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, action_id, event, details_json, created_at FROM audit_log ORDER BY id DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "event": row["event"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
