"""Evidence-grounded daily brief with an optional aggregate-only LLM layer."""

from __future__ import annotations

from typing import Any

from app.analytics import AnalyticsEngine, Filters
from app.connectors import OptionalBriefProvider


class BriefService:
    def __init__(self, engine: AnalyticsEngine):
        self.engine = engine
        self.provider = OptionalBriefProvider()

    def build(self) -> dict[str, Any]:
        dashboard = self.engine.dashboard_payload(Filters())
        overview = dashboard["overview"]
        quality = dashboard["data_quality"]
        catering = dashboard["catering"]
        high_risk = dashboard["segments"]["high_risk"]
        experiments = dashboard["experiments"]
        strongest = max(experiments, key=lambda row: row["lift_b_vs_a"], default=None)

        evidence = [
            f"30-day revenue is NZ${overview['revenue_30d']:,.0f}, changing {overview['revenue_delta'] * 100:.1f}% versus the prior 30 days.",
            f"The current web session-to-purchase conversion rate is {overview['web_conversion_30d'] * 100:.1f}%.",
            f"There are {len(high_risk)} displayed high-value churn-risk customers.",
            f"Open catering pipeline value is NZ${catering['open_pipeline_value']:,.0f}, with {len(catering['stale_leads'])} displayed stale leads.",
            f"Data quality score is {quality['score']:.1f}/100 with {quality['issues']} detected row-level issues.",
        ]
        if strongest:
            evidence.append(
                f"The strongest current experiment is {strongest['campaign_name']}; B-vs-A lift is {strongest['lift_b_vs_a'] * 100:.1f}% with {strongest['confidence'] * 100:.1f}% confidence."
            )

        llm_text = self.provider.synthesize(evidence)
        if llm_text:
            narrative = llm_text
            provider = "configured LLM using aggregate evidence only"
        else:
            direction = "up" if overview["revenue_delta"] >= 0 else "down"
            narrative = (
                f"Revenue is {direction} {abs(overview['revenue_delta']) * 100:.1f}% versus the previous 30 days. "
                f"The most immediate commercial follow-up is the NZ${catering['open_pipeline_value']:,.0f} catering pipeline, "
                f"where {len(catering['stale_leads'])} high-value records shown below are idle. "
                f"Retention activity should stay review-led: the churn model ranks likely risk, while the data trust layer still reports {quality['issues']} issues."
            )
            provider = "deterministic evidence rules (LLM optional)"

        return {
            "title": "Daily growth brief",
            "generated_for": dashboard["meta"]["as_of"],
            "provider": provider,
            "narrative": narrative,
            "evidence": evidence,
            "method": "Only aggregate metrics are eligible for external LLM synthesis; customer-level rows stay local.",
        }
