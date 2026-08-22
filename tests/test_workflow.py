from __future__ import annotations

from app.analytics import AnalyticsEngine
from app.connectors import HubSpotConnector
from app.workflow import ActionStore


def test_default_connector_is_a_non_mutating_mock(monkeypatch) -> None:
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_ENABLE_WRITES", raising=False)
    connector = HubSpotConnector()

    result = connector.execute({"type": "hubspot_contact_upsert", "payload": {"contacts": [{"email": "demo@example.test"}]}})

    assert connector.mode == "mock"
    assert result["external_write"] is False
    assert result["mode"] == "mock"


def test_actions_require_one_human_decision_and_write_an_audit_log(
    engine: AnalyticsEngine, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_ENABLE_WRITES", raising=False)
    store = ActionStore(tmp_path / "actions.db", HubSpotConnector())
    store.seed(engine.suggested_actions())
    actions = store.list_actions()

    approved = store.decide(actions[0]["id"], "approve")
    rejected = store.decide(actions[1]["id"], "reject")

    assert approved["status"] == "approved"
    assert approved["result"]["external_write"] is False
    assert rejected["status"] == "rejected"
    assert rejected["result"]["external_write"] is False
    assert [event["event"] for event in store.audit_log()] == ["reject", "approve"]


def test_a_decided_action_cannot_be_replayed(engine: AnalyticsEngine, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_ENABLE_WRITES", raising=False)
    store = ActionStore(tmp_path / "actions.db", HubSpotConnector())
    store.seed(engine.suggested_actions())
    action_id = store.list_actions()[0]["id"]
    store.decide(action_id, "approve")

    try:
        store.decide(action_id, "approve")
    except ValueError as error:
        assert "pending" in str(error)
    else:
        raise AssertionError("A decided action was unexpectedly replayed")
