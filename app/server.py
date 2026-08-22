"""Dependency-light local HTTP server for the dashboard and approval API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.analytics import AnalyticsEngine, Filters
from app.brief import BriefService
from app.connectors import HubSpotConnector
from app.workflow import ActionStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Services:
    engine: AnalyticsEngine
    brief: BriefService
    actions: ActionStore
    connector: HubSpotConnector
    static_dir: Path


def build_services(project_root: Path = PROJECT_ROOT, database_path: Path | None = None) -> Services:
    engine = AnalyticsEngine(project_root / "data" / "generated")
    connector = HubSpotConnector()
    actions = ActionStore(database_path or project_root / "data" / "app.db", connector)
    actions.seed(engine.suggested_actions())
    return Services(
        engine=engine,
        brief=BriefService(engine),
        actions=actions,
        connector=connector,
        static_dir=project_root / "app" / "static",
    )


class DashboardHandler(BaseHTTPRequestHandler):
    services: Services
    server_version = "RestaurantGrowth/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: HTTPStatus) -> None:
        self._json({"error": message, "status": int(status)}, status)

    def _serve_static(self, relative_path: str) -> None:
        candidate = (self.services.static_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.services.static_dir.resolve())
        except ValueError:
            self._error("Invalid path", HTTPStatus.BAD_REQUEST)
            return
        if not candidate.is_file():
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok", "data_mode": "synthetic", "as_of": "2026-08-17"})
            return
        if parsed.path == "/api/dashboard":
            filters = Filters.from_query(parse_qs(parsed.query))
            self._json(self.services.engine.dashboard_payload(filters))
            return
        if parsed.path == "/api/brief":
            self._json(self.services.brief.build())
            return
        if parsed.path == "/api/actions":
            self._json({"actions": self.services.actions.list_actions()})
            return
        if parsed.path == "/api/audit":
            self._json({"events": self.services.actions.audit_log()})
            return
        if parsed.path == "/api/integrations":
            self._json(
                {
                    "hubspot": self.services.connector.status(),
                    "ga4": {
                        "mode": "CSV adapter",
                        "configured": True,
                        "source": "web_events.csv",
                        "guardrail": "HarbourTable uses GA4-style synthetic events; no Google account is accessed.",
                    },
                    "llm": {
                        "mode": "configured" if self.services.brief.provider.configured else "deterministic fallback",
                        "configured": self.services.brief.provider.configured,
                        "guardrail": "Only aggregate evidence can leave the local process.",
                    },
                }
            )
            return
        if parsed.path in {"/", "/index.html"}:
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        self._error("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        match = re.fullmatch(r"/api/actions/(\d+)/(approve|reject)", parsed.path)
        if not match:
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        action_id = int(match.group(1))
        decision = match.group(2)
        try:
            action = self.services.actions.decide(action_id, decision)
        except KeyError as exc:
            self._error(str(exc), HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            self._error(str(exc), HTTPStatus.CONFLICT)
            return
        status = HTTPStatus.OK if action["status"] != "failed" else HTTPStatus.BAD_GATEWAY
        self._json({"action": action}, status)


def create_server(host: str, port: int, services: Services | None = None) -> ThreadingHTTPServer:
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {"services": services or build_services()})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HarbourTable")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("APP_PORT", "8787")))
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"HarbourTable running at http://{args.host}:{args.port}")
    print("Synthetic data mode. HubSpot writes are approval-gated and disabled by default.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
