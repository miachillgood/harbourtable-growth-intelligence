from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from app.server import build_services, create_server


def _json(url: str, method: str = "GET") -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_http_api_and_static_dashboard(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_ENABLE_WRITES", raising=False)
    project_root = Path(__file__).resolve().parents[1]
    services = build_services(project_root, tmp_path / "server.db")
    try:
        server = create_server("127.0.0.1", 0, services)
    except PermissionError:
        pytest.skip("This sandbox does not permit binding a localhost test socket")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _json(f"{base}/api/health")
        assert status == 200
        assert health["data_mode"] == "synthetic"

        status, dashboard = _json(f"{base}/api/dashboard?store=ST01&channel=website")
        assert status == 200
        assert dashboard["meta"]["filters"]["store_id"] == "ST01"
        assert dashboard["overview"]["orders_30d"] > 0

        with urllib.request.urlopen(f"{base}/", timeout=10) as response:
            html = response.read().decode("utf-8")
        assert response.status == 200
        assert "HarbourTable" in html

        _, actions = _json(f"{base}/api/actions")
        first_id = actions["actions"][0]["id"]
        _, decided = _json(f"{base}/api/actions/{first_id}/approve", method="POST")
        assert decided["action"]["status"] == "approved"
        assert decided["action"]["result"]["external_write"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
