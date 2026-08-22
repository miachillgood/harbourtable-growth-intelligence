"""Approval-gated external connector adapters.

The default connector is deliberately non-mutating. A real HubSpot contact upsert
is possible only when a token is present and the separate enable flag is true.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


class ConnectorError(RuntimeError):
    pass


class HubSpotConnector:
    API_URL = "https://api.hubapi.com/crm/v3/objects/contacts/batch/upsert"

    def __init__(self) -> None:
        self.token = os.getenv("HUBSPOT_ACCESS_TOKEN", "").strip()
        self.writes_enabled = os.getenv("HUBSPOT_ENABLE_WRITES", "false").lower() == "true"

    @property
    def mode(self) -> str:
        return "live-approval-gated" if self.token and self.writes_enabled else "mock"

    def status(self) -> dict[str, Any]:
        return {
            "name": "HubSpot CRM",
            "mode": self.mode,
            "configured": bool(self.token),
            "writes_enabled": bool(self.token and self.writes_enabled),
            "guardrail": "An action must be approved in the UI before this connector is invoked.",
            "supported_live_action": "contact batch upsert by email",
        }

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "mock":
            return {
                "mode": "mock",
                "external_write": False,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "message": "Approval recorded. HubSpot mutation simulated because live writes are disabled.",
                "preview": action.get("payload", {}),
            }
        if action.get("type") != "hubspot_contact_upsert":
            return {
                "mode": "local-only",
                "external_write": False,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "message": "This action type remains a reviewed local draft; only contact upsert is enabled for live mode.",
                "preview": action.get("payload", {}),
            }

        contacts = action.get("payload", {}).get("contacts", [])
        if not contacts:
            raise ConnectorError("No contacts were supplied for HubSpot upsert")
        inputs = []
        for contact in contacts:
            properties = {
                "email": contact["email"],
                "firstname": contact.get("first_name", ""),
                "lastname": contact.get("last_name", ""),
                "lifecyclestage": contact.get("lifecyclestage", "customer"),
            }
            inputs.append(
                {
                    "id": contact["email"],
                    "idProperty": "email",
                    "properties": properties,
                }
            )
        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps({"inputs": inputs}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError(f"HubSpot returned HTTP {exc.code}: {details[:500]}") from exc
        except urllib.error.URLError as exc:
            raise ConnectorError(f"HubSpot request failed: {exc.reason}") from exc
        return {
            "mode": "live-approval-gated",
            "external_write": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "message": f"Submitted {len(inputs)} contact upserts to HubSpot.",
            "hubspot_status": payload.get("status", "COMPLETE"),
            "result_count": len(payload.get("results", [])),
        }


class OptionalBriefProvider:
    """Optional OpenAI-compatible provider that only receives aggregate evidence."""

    def __init__(self) -> None:
        self.url = os.getenv("LLM_API_URL", "").strip()
        self.key = os.getenv("LLM_API_KEY", "").strip()
        self.model = os.getenv("LLM_MODEL", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key and self.model)

    def synthesize(self, evidence: list[str]) -> str | None:
        if not self.configured:
            return None
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Write a concise restaurant growth brief using only supplied aggregate evidence. Do not invent numbers.",
                },
                {"role": "user", "content": "\n".join(evidence)},
            ],
            "temperature": 0.1,
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None
