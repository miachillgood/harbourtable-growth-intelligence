from __future__ import annotations

import math

import pandas as pd

from app.analytics import AnalyticsEngine, Filters
from app.data_generator import ANCHOR_DATE


def test_source_counts_and_deliberate_quality_failures(engine: AnalyticsEngine) -> None:
    counts = {item["source"]: item["rows"] for item in engine.source_inventory()}
    assert counts == {
        "customers.csv": 720,
        "orders.csv": 5781,
        "web_events.csv": 6495,
        "campaign_responses.csv": 1080,
        "catering_leads.csv": 90,
    }

    quality = engine.dashboard_payload(Filters())["data_quality"]
    detected = {item["check"]: item["count"] for item in quality["checks"]}
    assert detected == {
        "Duplicate customer emails": 8,
        "Missing acquisition source": 9,
        "Orders with unknown customer": 1,
        "Website events with unknown customer": 3,
        "Invalid catering lifecycle stage": 1,
    }
    assert quality["issues"] == sum(detected.values()) == 22


def test_revenue_kpi_reconciles_to_clean_orders(engine: AnalyticsEngine) -> None:
    payload = engine.dashboard_payload(Filters())
    start = ANCHOR_DATE - pd.Timedelta(days=29)
    end = ANCHOR_DATE + pd.Timedelta(days=1)
    expected = engine.orders[
        (engine.orders["ordered_at"] >= start) & (engine.orders["ordered_at"] < end)
    ]["total"].sum()
    assert payload["overview"]["revenue_30d"] == expected
    assert "O999999" not in set(engine.orders["order_id"])


def test_filters_apply_to_commercial_views(engine: AnalyticsEngine) -> None:
    all_store = engine.dashboard_payload(Filters())
    filtered = engine.dashboard_payload(Filters(store_id="ST02", channel="website"))
    non_web = engine.dashboard_payload(Filters(channel="in_store"))

    assert 0 < filtered["overview"]["revenue_30d"] < all_store["overview"]["revenue_30d"]
    assert filtered["meta"]["filters"] == {"store_id": "ST02", "channel": "website", "window_days": 90}
    assert all(stage["sessions"] == 0 for stage in non_web["web_funnel"])
    filtered_segment_customers = sum(row["customers"] for row in filtered["segments"]["summary"])
    all_segment_customers = sum(row["customers"] for row in all_store["segments"]["summary"])
    assert 0 < filtered_segment_customers < all_segment_customers
    assert filtered["catering"]["open_pipeline_value"] < all_store["catering"]["open_pipeline_value"]


def test_funnel_and_experiment_outputs_are_coherent(engine: AnalyticsEngine) -> None:
    payload = engine.dashboard_payload(Filters())
    funnel = payload["web_funnel"]
    sessions = {row["stage"]: row["sessions"] for row in funnel}
    assert sessions["session_start"] >= sessions["menu_view"]
    assert sessions["begin_checkout"] >= sessions["purchase"]
    assert all(0 <= row["conversion_from_start"] <= 1 for row in funnel)

    assert len(payload["experiments"]) == 3
    for experiment in payload["experiments"]:
        assert {row["variant"] for row in experiment["variants"]} == {"A", "B"}
        assert math.isfinite(experiment["lift_b_vs_a"])
        assert 0 <= experiment["confidence"] <= 1


def test_churn_model_uses_temporal_holdout_and_has_signal(engine: AnalyticsEngine) -> None:
    model = engine.model_summary
    assert model["feature_cutoff"] == (ANCHOR_DATE - pd.Timedelta(days=60)).date().isoformat()
    assert model["label_window_days"] == 60
    assert model["evaluation_rows"] >= 150
    assert 0.75 <= model["holdout_auc"] <= 1
    assert "future" not in model["features"]
    assert "Temporal" in model["guardrail"]


def test_suggested_actions_have_evidence_and_do_not_send(engine: AnalyticsEngine) -> None:
    actions = engine.suggested_actions()
    assert {action["type"] for action in actions} == {
        "hubspot_contact_upsert",
        "hubspot_task_draft",
        "experiment_note",
    }
    for action in actions:
        assert action["evidence"]
        assert action["target_count"] > 0
        assert action["risk"] in {"safe", "mutating"}

    retention = next(action for action in actions if action["type"] == "hubspot_contact_upsert")
    consent_by_email = engine.customers.set_index("email")["marketing_consent"].to_dict()
    assert all(consent_by_email[contact["email"]] for contact in retention["payload"]["contacts"])
