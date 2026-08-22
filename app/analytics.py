"""Source-backed restaurant growth metrics and model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import erf, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.data_generator import ANCHOR_DATE, generate_dataset


FUNNEL_ORDER = ["session_start", "menu_view", "offer_click", "begin_checkout", "purchase"]
VALID_LEAD_STAGES = ["new", "qualified", "quoted", "won", "lost"]


def _python(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _python(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_python(item) for item in value]
    if isinstance(value, tuple):
        return [_python(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return round(float(value), 6)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, date)):
        return pd.Timestamp(value).isoformat()
    if pd.isna(value):
        return None
    return value


def _safe_delta(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous


def _normal_cdf(value: float) -> float:
    return (1.0 + erf(value / sqrt(2.0))) / 2.0


@dataclass(frozen=True)
class Filters:
    store_id: str = "all"
    channel: str = "all"
    window_days: int = 90

    @classmethod
    def from_query(cls, query: dict[str, list[str]]) -> "Filters":
        store_id = query.get("store", ["all"])[0]
        channel = query.get("channel", ["all"])[0]
        try:
            window_days = int(query.get("days", ["90"])[0])
        except ValueError:
            window_days = 90
        return cls(store_id=store_id, channel=channel, window_days=max(30, min(window_days, 365)))


class AnalyticsEngine:
    """Loads durable CSV sources and exposes reconciled dashboard metrics."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        if not (data_dir / "orders.csv").exists():
            generate_dataset(data_dir)
        self._load()
        self._build_customer_intelligence()

    def _load(self) -> None:
        self.customers = pd.read_csv(self.data_dir / "customers.csv", keep_default_na=False)
        self.orders_raw = pd.read_csv(self.data_dir / "orders.csv")
        self.order_items = pd.read_csv(self.data_dir / "order_items.csv")
        self.web_events = pd.read_csv(self.data_dir / "web_events.csv")
        self.campaigns = pd.read_csv(self.data_dir / "campaign_responses.csv")
        self.catering = pd.read_csv(self.data_dir / "catering_leads.csv")
        self.stores = pd.read_csv(self.data_dir / "stores.csv")
        self.menu = pd.read_csv(self.data_dir / "menu.csv")

        self.customers["joined_at"] = pd.to_datetime(self.customers["joined_at"])
        self.orders_raw["ordered_at"] = pd.to_datetime(self.orders_raw["ordered_at"])
        self.web_events["event_at"] = pd.to_datetime(self.web_events["event_at"])
        self.campaigns["sent_at"] = pd.to_datetime(self.campaigns["sent_at"])
        self.catering["created_at"] = pd.to_datetime(self.catering["created_at"])
        self.catering["event_date"] = pd.to_datetime(self.catering["event_date"])
        self.catering["last_contacted_at"] = pd.to_datetime(self.catering["last_contacted_at"])

        customer_ids = set(self.customers["customer_id"])
        self.orders = self.orders_raw[
            self.orders_raw["customer_id"].isin(customer_ids)
            & self.orders_raw["status"].eq("completed")
            & self.orders_raw["total"].gt(0)
        ].copy()
        self.orders["order_date"] = self.orders["ordered_at"].dt.normalize()

    def _features_at(self, cutoff: pd.Timestamp, window_days: int = 240) -> pd.DataFrame:
        start = cutoff - pd.Timedelta(days=window_days)
        history = self.orders[(self.orders["ordered_at"] < cutoff) & (self.orders["ordered_at"] >= start)].copy()
        if history.empty:
            return pd.DataFrame()
        aggregate = history.groupby("customer_id").agg(
            last_order=("ordered_at", "max"),
            frequency=("order_id", "nunique"),
            monetary=("total", "sum"),
            avg_order_value=("total", "mean"),
            average_discount=("discount", "mean"),
            channel_count=("channel", "nunique"),
        )
        aggregate["recency"] = (cutoff - aggregate["last_order"]).dt.days.clip(lower=0)
        aggregate["discount_ratio"] = aggregate["average_discount"] / aggregate["avg_order_value"].clip(lower=1)
        joined = self.customers.set_index("customer_id")["joined_at"]
        aggregate["tenure_days"] = (cutoff - joined.reindex(aggregate.index)).dt.days.clip(lower=1)
        return aggregate.drop(columns=["last_order"]).fillna(0)

    def _build_customer_intelligence(self) -> None:
        target_cutoff = ANCHOR_DATE - pd.Timedelta(days=60)
        historical = self._features_at(target_cutoff)
        future_customers = set(
            self.orders[
                (self.orders["ordered_at"] >= target_cutoff) & (self.orders["ordered_at"] <= ANCHOR_DATE)
            ]["customer_id"]
        )
        historical["churned"] = (~historical.index.isin(future_customers)).astype(int)
        features = [
            "recency", "frequency", "monetary", "avg_order_value", "discount_ratio", "channel_count", "tenure_days"
        ]
        X = historical[features]
        y = historical["churned"]
        model = Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=500, class_weight="balanced", random_state=23)),
            ]
        )
        if y.nunique() >= 2 and len(historical) >= 40:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.30, random_state=23, stratify=y
            )
            model.fit(X_train, y_train)
            probabilities = model.predict_proba(X_test)[:, 1]
            auc = float(roc_auc_score(y_test, probabilities))
            evaluation_rows = len(X_test)
        else:
            model.fit(X, y)
            auc = 0.5
            evaluation_rows = len(X)

        current = self._features_at(ANCHOR_DATE)
        current["churn_probability"] = model.predict_proba(current[features])[:, 1]
        current["risk_tier"] = pd.cut(
            current["churn_probability"],
            bins=[-0.01, 0.35, 0.65, 1.01],
            labels=["Low", "Medium", "High"],
        ).astype(str)
        self.customer_scores = current
        self.model_summary = {
            "name": "Logistic regression churn propensity",
            "holdout_auc": round(auc, 3),
            "evaluation_rows": evaluation_rows,
            "feature_cutoff": target_cutoff.date().isoformat(),
            "label_window_days": 60,
            "features": features,
            "guardrail": "Temporal features stop before the 60-day outcome window.",
        }

    def _filtered_orders(self, filters: Filters) -> pd.DataFrame:
        start = ANCHOR_DATE - pd.Timedelta(days=filters.window_days - 1)
        result = self.orders[(self.orders["ordered_at"] >= start) & (self.orders["ordered_at"] <= ANCHOR_DATE + pd.Timedelta(days=1))]
        if filters.store_id != "all":
            result = result[result["store_id"] == filters.store_id]
        if filters.channel != "all":
            result = result[result["channel"] == filters.channel]
        return result.copy()

    def _overview(self, filters: Filters) -> dict[str, Any]:
        current_end = ANCHOR_DATE + pd.Timedelta(days=1)
        current_start = ANCHOR_DATE - pd.Timedelta(days=29)
        previous_start = current_start - pd.Timedelta(days=30)
        base = self.orders.copy()
        if filters.store_id != "all":
            base = base[base["store_id"] == filters.store_id]
        if filters.channel != "all":
            base = base[base["channel"] == filters.channel]
        current = base[(base["ordered_at"] >= current_start) & (base["ordered_at"] < current_end)]
        previous = base[(base["ordered_at"] >= previous_start) & (base["ordered_at"] < current_start)]
        current_revenue = float(current["total"].sum())
        previous_revenue = float(previous["total"].sum())
        current_orders = int(current["order_id"].nunique())
        previous_orders = int(previous["order_id"].nunique())
        current_aov = current_revenue / current_orders if current_orders else 0
        previous_aov = previous_revenue / previous_orders if previous_orders else 0

        active_90 = base[base["ordered_at"] >= ANCHOR_DATE - pd.Timedelta(days=89)]
        customer_order_counts = active_90.groupby("customer_id")["order_id"].nunique()
        repeat_rate = float((customer_order_counts >= 2).mean()) if len(customer_order_counts) else 0.0

        sessions = self.web_events[self.web_events["event_at"] >= ANCHOR_DATE - pd.Timedelta(days=29)]
        if filters.store_id != "all":
            sessions = sessions[sessions["store_id"] == filters.store_id]
        session_total = sessions["session_id"].nunique()
        purchase_sessions = sessions[sessions["event_name"] == "purchase"]["session_id"].nunique()
        web_conversion = purchase_sessions / session_total if session_total else 0

        score_pool = self.customer_scores
        if filters.store_id != "all" or filters.channel != "all":
            score_pool = score_pool.loc[score_pool.index.intersection(base["customer_id"].unique())]
        high_risk_ids = score_pool[
            (score_pool["risk_tier"] == "High")
            & (score_pool["monetary"] >= score_pool["monetary"].quantile(0.65))
        ].index
        at_risk_value = float(score_pool.loc[high_risk_ids, "monetary"].sum())
        return {
            "revenue_30d": current_revenue,
            "revenue_delta": _safe_delta(current_revenue, previous_revenue),
            "orders_30d": current_orders,
            "orders_delta": _safe_delta(current_orders, previous_orders),
            "aov_30d": current_aov,
            "aov_delta": _safe_delta(current_aov, previous_aov),
            "repeat_rate_90d": repeat_rate,
            "web_conversion_30d": web_conversion,
            "at_risk_value": at_risk_value,
        }

    def _revenue_trend(self, filters: Filters) -> list[dict[str, Any]]:
        orders = self._filtered_orders(filters)
        if orders.empty:
            return []
        weekly = (
            orders.set_index("ordered_at")
            .resample("W-MON", label="left", closed="left")
            .agg(revenue=("total", "sum"), orders=("order_id", "nunique"), margin=("gross_margin", "sum"))
            .reset_index()
        )
        weekly["aov"] = weekly["revenue"] / weekly["orders"].replace(0, np.nan)
        return _python(weekly.fillna(0).to_dict(orient="records"))

    def _funnel(self, filters: Filters) -> list[dict[str, Any]]:
        start = ANCHOR_DATE - pd.Timedelta(days=filters.window_days - 1)
        events = self.web_events[self.web_events["event_at"] >= start]
        if filters.store_id != "all":
            events = events[events["store_id"] == filters.store_id]
        if filters.channel not in {"all", "website"}:
            return [{"stage": stage, "sessions": 0, "conversion_from_start": 0} for stage in FUNNEL_ORDER]
        start_count = events[events["event_name"] == "session_start"]["session_id"].nunique()
        result = []
        for stage in FUNNEL_ORDER:
            count = int(events[events["event_name"] == stage]["session_id"].nunique())
            result.append(
                {
                    "stage": stage,
                    "sessions": count,
                    "conversion_from_start": count / start_count if start_count else 0,
                }
            )
        return result

    def _segments(self, filters: Filters | None = None) -> dict[str, Any]:
        scored = self.customer_scores.copy()
        if filters and (filters.store_id != "all" or filters.channel != "all"):
            active_ids = self._filtered_orders(filters)["customer_id"].unique()
            scored = scored.loc[scored.index.intersection(active_ids)]
        try:
            scored["r_score"] = pd.qcut(scored["recency"].rank(method="first"), 5, labels=[5, 4, 3, 2, 1]).astype(int)
            scored["f_score"] = pd.qcut(scored["frequency"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
            scored["m_score"] = pd.qcut(scored["monetary"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
        except ValueError:
            scored[["r_score", "f_score", "m_score"]] = 3

        conditions = [
            (scored["r_score"] >= 4) & (scored["f_score"] >= 4),
            (scored["r_score"] >= 3) & (scored["f_score"] >= 3),
            (scored["r_score"] >= 4) & (scored["f_score"] <= 2),
            (scored["r_score"] <= 2) & (scored["f_score"] >= 3),
        ]
        scored["segment"] = np.select(
            conditions, ["Champions", "Loyal", "New & Promising", "At Risk"], default="Hibernating"
        )
        summary = (
            scored.groupby("segment", observed=True)
            .agg(customers=("frequency", "size"), revenue=("monetary", "sum"), avg_churn_risk=("churn_probability", "mean"))
            .reset_index()
            .sort_values("revenue", ascending=False)
        )
        total_revenue = summary["revenue"].sum()
        summary["revenue_share"] = summary["revenue"] / total_revenue if total_revenue else 0

        enriched = scored.join(
            self.customers.set_index("customer_id")[
                ["first_name", "last_name", "email", "home_store_id", "marketing_consent"]
            ],
            how="left",
        )
        high_risk = enriched[
            (enriched["risk_tier"] == "High") & (enriched["monetary"] >= enriched["monetary"].quantile(0.55))
        ].sort_values(["churn_probability", "monetary"], ascending=False)
        high_risk_rows = []
        for customer_id, row in high_risk.head(12).iterrows():
            high_risk_rows.append(
                {
                    "customer_id": customer_id,
                    "customer": f"{row['first_name']} {row['last_name']}",
                    "email": row["email"],
                    "marketing_consent": row["marketing_consent"],
                    "store_id": row["home_store_id"],
                    "segment": row["segment"],
                    "recency_days": row["recency"],
                    "historical_value": row["monetary"],
                    "churn_probability": row["churn_probability"],
                    "recommended_action": "Create win-back task and review offer eligibility",
                }
            )
        return {"summary": _python(summary.to_dict(orient="records")), "high_risk": _python(high_risk_rows)}

    def _channel_performance(self, filters: Filters) -> list[dict[str, Any]]:
        customers = self.customers[["customer_id", "acquisition_source"]].copy()
        orders = self._filtered_orders(filters).merge(customers, on="customer_id", how="left")
        if orders.empty:
            return []
        per_customer = orders.groupby(["acquisition_source", "customer_id"]).agg(
            orders=("order_id", "nunique"), revenue=("total", "sum")
        ).reset_index()
        summary = per_customer.groupby("acquisition_source").agg(
            customers=("customer_id", "nunique"),
            orders=("orders", "sum"),
            revenue=("revenue", "sum"),
            repeat_customers=("orders", lambda values: int((values >= 2).sum())),
        ).reset_index()
        summary["aov"] = summary["revenue"] / summary["orders"].replace(0, np.nan)
        summary["repeat_rate"] = summary["repeat_customers"] / summary["customers"].replace(0, np.nan)
        return _python(summary.fillna(0).sort_values("revenue", ascending=False).to_dict(orient="records"))

    def _campaign_experiments(self) -> list[dict[str, Any]]:
        grouped = self.campaigns.groupby(["campaign_id", "campaign_name", "variant"]).agg(
            sent=("customer_id", "size"),
            opened=("opened", "sum"),
            clicked=("clicked", "sum"),
            converted=("converted", "sum"),
            revenue=("revenue", "sum"),
            cost=("contact_cost", "sum"),
        ).reset_index()
        grouped["open_rate"] = grouped["opened"] / grouped["sent"]
        grouped["click_rate"] = grouped["clicked"] / grouped["sent"]
        grouped["conversion_rate"] = grouped["converted"] / grouped["sent"]
        grouped["profit"] = grouped["revenue"] - grouped["cost"]
        result: list[dict[str, Any]] = []
        for campaign_id, experiment in grouped.groupby("campaign_id"):
            variants = {row["variant"]: row for _, row in experiment.iterrows()}
            a = variants.get("A")
            b = variants.get("B")
            lift = 0.0
            confidence = 0.0
            if a is not None and b is not None and a["conversion_rate"]:
                lift = (b["conversion_rate"] - a["conversion_rate"]) / a["conversion_rate"]
                pooled = (a["converted"] + b["converted"]) / (a["sent"] + b["sent"])
                standard_error = sqrt(max(1e-12, pooled * (1 - pooled) * (1 / a["sent"] + 1 / b["sent"])))
                z_score = abs(b["conversion_rate"] - a["conversion_rate"]) / standard_error
                confidence = 2 * _normal_cdf(z_score) - 1
            result.append(
                {
                    "campaign_id": campaign_id,
                    "campaign_name": experiment.iloc[0]["campaign_name"],
                    "variants": _python(experiment.to_dict(orient="records")),
                    "lift_b_vs_a": lift,
                    "confidence": confidence,
                    "decision": "Prefer B" if lift > 0 and confidence >= 0.80 else "Keep testing",
                }
            )
        return _python(result)

    def _catering_funnel(self, filters: Filters | None = None) -> dict[str, Any]:
        valid = self.catering[self.catering["stage"].isin(VALID_LEAD_STAGES)].copy()
        if filters and filters.store_id != "all":
            valid = valid[valid["owner_store_id"] == filters.store_id]
        stage_counts = valid["stage"].value_counts().to_dict()
        cumulative = {
            "new": len(valid),
            "qualified": int(valid["stage"].isin(["qualified", "quoted", "won"]).sum()),
            "quoted": int(valid["stage"].isin(["quoted", "won"]).sum()),
            "won": int(valid["stage"].eq("won").sum()),
        }
        stale = valid[
            valid["stage"].isin(["new", "qualified", "quoted"])
            & ((ANCHOR_DATE - valid["last_contacted_at"]).dt.days >= 7)
        ].sort_values("expected_value", ascending=False)
        stale_rows = stale.head(10)[
            ["lead_id", "company", "source", "stage", "headcount", "expected_value", "owner_store_id", "last_contacted_at"]
        ].copy()
        stale_rows["days_idle"] = (ANCHOR_DATE - stale_rows["last_contacted_at"]).dt.days
        return {
            "funnel": [{"stage": stage, "leads": cumulative[stage]} for stage in ["new", "qualified", "quoted", "won"]],
            "raw_stage_counts": stage_counts,
            "open_pipeline_value": float(valid[valid["stage"].isin(["new", "qualified", "quoted"])]["expected_value"].sum()),
            "stale_leads": _python(stale_rows.to_dict(orient="records")),
        }

    def _data_quality(self) -> dict[str, Any]:
        customer_ids = set(self.customers["customer_id"])
        checks = [
            {
                "check": "Duplicate customer emails",
                "severity": "high",
                "count": int(self.customers["email"].duplicated(keep=False).sum()),
                "source": "customers.csv",
                "rule": "email must identify one customer record",
            },
            {
                "check": "Missing acquisition source",
                "severity": "medium",
                "count": int(self.customers["acquisition_source"].eq("").sum()),
                "source": "customers.csv",
                "rule": "acquisition_source must be populated",
            },
            {
                "check": "Orders with unknown customer",
                "severity": "critical",
                "count": int((~self.orders_raw["customer_id"].isin(customer_ids)).sum()),
                "source": "orders.csv",
                "rule": "order.customer_id must resolve to customers.customer_id",
            },
            {
                "check": "Website events with unknown customer",
                "severity": "high",
                "count": int((~self.web_events["customer_id"].isin(customer_ids)).sum()),
                "source": "web_events.csv",
                "rule": "known event customer or explicit anonymous identity required",
            },
            {
                "check": "Invalid catering lifecycle stage",
                "severity": "high",
                "count": int((~self.catering["stage"].isin(VALID_LEAD_STAGES)).sum()),
                "source": "catering_leads.csv",
                "rule": f"stage in {VALID_LEAD_STAGES}",
            },
        ]
        issue_count = sum(item["count"] for item in checks)
        passing = sum(1 for item in checks if item["count"] == 0)
        score = max(0, 100 - issue_count * 1.8)
        return {
            "score": round(score, 1),
            "checks_passing": passing,
            "checks_total": len(checks),
            "issues": issue_count,
            "checks": checks,
            "metric_policy": "Invalid financial records are excluded from revenue and order KPIs, then reported here.",
        }

    def suggested_actions(self) -> list[dict[str, Any]]:
        segments = self._segments()
        high_risk = segments["high_risk"]
        catering = self._catering_funnel()
        experiments = self._campaign_experiments()
        best_experiment = max(experiments, key=lambda row: row["lift_b_vs_a"], default=None)
        actions: list[dict[str, Any]] = []
        if high_risk:
            eligible_rows = [row for row in high_risk if row["marketing_consent"]]
            contacts = [
                {
                    "email": row["email"],
                    "first_name": row["customer"].split(" ", 1)[0],
                    "last_name": row["customer"].split(" ", 1)[1] if " " in row["customer"] else "",
                    "lifecyclestage": "customer",
                }
                for row in eligible_rows[:8]
            ]
            total_value = sum(row["historical_value"] for row in eligible_rows[:8])
            actions.append(
                {
                    "action_key": "retention-high-value",
                    "type": "hubspot_contact_upsert",
                    "title": "Create a high-value win-back cohort",
                    "summary": f"{len(contacts)} consented customer profiles combine high churn propensity with meaningful historical value.",
                    "evidence": [
                        f"Selected historical value: NZ${total_value:,.0f}",
                        f"Model holdout ROC-AUC: {self.model_summary['holdout_auc']:.3f}",
                        "Eligibility is based on aggregate behavior; send remains outside this action.",
                    ],
                    "recommended_action": "Upsert eligible contacts to a HubSpot test account for human segmentation review.",
                    "risk": "mutating",
                    "target_count": len(contacts),
                    "payload": {"contacts": contacts, "segment": "RGI_HIGH_VALUE_AT_RISK"},
                }
            )
        stale = catering["stale_leads"]
        if stale:
            total_pipeline = sum(row["expected_value"] for row in stale[:6])
            actions.append(
                {
                    "action_key": "catering-stale-leads",
                    "type": "hubspot_task_draft",
                    "title": "Review stale catering opportunities",
                    "summary": f"{len(stale[:6])} open catering leads have been idle for at least seven days.",
                    "evidence": [
                        f"Pipeline represented: NZ${total_pipeline:,.0f}",
                        f"Oldest selected lead: {max(row['days_idle'] for row in stale[:6])} days idle",
                        "No email will be sent automatically.",
                    ],
                    "recommended_action": "Create follow-up task drafts for the store owners.",
                    "risk": "mutating",
                    "target_count": len(stale[:6]),
                    "payload": {"lead_ids": [row["lead_id"] for row in stale[:6]], "priority": "HIGH"},
                }
            )
        if best_experiment:
            actions.append(
                {
                    "action_key": "experiment-decision",
                    "type": "experiment_note",
                    "title": f"{best_experiment['campaign_name']}: review variant B",
                    "summary": f"Variant B lift is {best_experiment['lift_b_vs_a'] * 100:.1f}% versus A.",
                    "evidence": [
                        f"Statistical confidence: {best_experiment['confidence'] * 100:.1f}%",
                        f"Current decision rule: {best_experiment['decision']}",
                        "Results are from fictional historical campaign outcomes.",
                    ],
                    "recommended_action": "Save an experiment decision note; do not roll out unless confidence meets the operating threshold.",
                    "risk": "safe",
                    "target_count": 1,
                    "payload": {"campaign_id": best_experiment["campaign_id"], "decision": best_experiment["decision"]},
                }
            )
        return actions

    def source_inventory(self) -> list[dict[str, Any]]:
        sources = [
            ("customers.csv", len(self.customers), "customer profile", self.customers["joined_at"].max()),
            ("orders.csv", len(self.orders_raw), "order", self.orders_raw["ordered_at"].max()),
            ("web_events.csv", len(self.web_events), "GA4-style event", self.web_events["event_at"].max()),
            ("campaign_responses.csv", len(self.campaigns), "customer-campaign", self.campaigns["sent_at"].max()),
            ("catering_leads.csv", len(self.catering), "catering lead", self.catering["created_at"].max()),
        ]
        return [
            {"source": name, "rows": rows, "grain": grain, "freshness": pd.Timestamp(freshness).date().isoformat(), "mode": "synthetic"}
            for name, rows, grain, freshness in sources
        ]

    def dashboard_payload(self, filters: Filters) -> dict[str, Any]:
        stores = [{"id": "all", "name": "All stores"}] + [
            {"id": row["store_id"], "name": row["store_name"]} for _, row in self.stores.iterrows()
        ]
        return _python(
            {
                "meta": {
                    "title": "HarbourTable",
                    "subtitle": "Customer, campaign and catering signals for a fictional Auckland hospitality group",
                    "as_of": ANCHOR_DATE.date().isoformat(),
                    "data_mode": "100% synthetic data",
                    "filters": filters.__dict__,
                    "stores": stores,
                    "channels": ["all", "in_store", "website", "mobile_app", "uber_eats"],
                },
                "overview": self._overview(filters),
                "revenue_trend": self._revenue_trend(filters),
                "web_funnel": self._funnel(filters),
                "segments": self._segments(filters),
                "channels": self._channel_performance(filters),
                "experiments": self._campaign_experiments(),
                "catering": self._catering_funnel(filters),
                "data_quality": self._data_quality(),
                "model": self.model_summary,
                "sources": self.source_inventory(),
            }
        )
