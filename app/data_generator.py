"""Deterministic fictional data for a multi-location hospitality group.

The generator intentionally creates a handful of data-quality failures. They are
small enough not to dominate the demo, but concrete enough to validate that the
trust layer catches duplicates, missing sources, orphan records, and malformed
lifecycle values.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ANCHOR_DATE = pd.Timestamp("2026-08-17")
RANDOM_SEED = 20260817

STORES = [
    ("ST01", "Britomart", "Auckland CBD"),
    ("ST02", "Newmarket", "Auckland Central"),
    ("ST03", "Takapuna", "North Shore"),
    ("ST04", "Sylvia Park", "Mount Wellington"),
]

MENU = [
    ("M01", "Salmon Poke Bowl", "Bowls", 18.90, 6.40),
    ("M02", "Teriyaki Chicken Bowl", "Bowls", 17.50, 6.20),
    ("M03", "Roasted Vegetable Wrap", "Vegetarian", 13.90, 4.10),
    ("M04", "Market Fish Rice Box", "Rice Boxes", 19.90, 7.30),
    ("M05", "Chicken Katsu Bento", "Bento", 18.90, 6.80),
    ("M06", "Crispy Chicken Bao", "Small Plates", 11.50, 3.90),
    ("M07", "Miso Soup", "Sides", 4.50, 0.95),
    ("M08", "Halloumi Garden Salad", "Salads", 15.90, 4.85),
    ("M09", "Crispy Chicken Wrap", "Wraps", 14.90, 4.60),
    ("M10", "Prawn Dumplings", "Small Plates", 13.90, 4.90),
    ("M11", "Corporate Lunch Platter", "Catering", 89.00, 34.00),
    ("M12", "Vegetarian Event Platter", "Catering", 74.00, 24.50),
]


def _choice(rng: np.random.Generator, values: list, probabilities: list | None = None):
    return rng.choice(values, p=probabilities)


def _write(frame: pd.DataFrame, output_dir: Path, name: str) -> int:
    frame.to_csv(output_dir / f"{name}.csv", index=False)
    return len(frame)


def _generate_customers(rng: np.random.Generator, count: int = 720) -> pd.DataFrame:
    first_names = [
        "Aroha", "Mia", "Leo", "Anika", "Noah", "Sophie", "Liam", "Isla",
        "Ethan", "Mei", "Harper", "Theo", "Zoe", "Arjun", "Grace", "Finn",
    ]
    last_names = [
        "Chen", "Patel", "Williams", "Kim", "Singh", "Brown", "Wilson",
        "Taylor", "Li", "Thompson", "Martin", "Nguyen", "Walker", "Kaur",
    ]
    sources = ["organic_search", "paid_social", "referral", "email", "walk_in", "corporate_event"]
    source_probs = [0.22, 0.18, 0.17, 0.12, 0.25, 0.06]
    rows: list[dict] = []
    for idx in range(1, count + 1):
        first = str(_choice(rng, first_names))
        last = str(_choice(rng, last_names))
        joined_days_ago = int(rng.integers(25, 700))
        source = str(_choice(rng, sources, source_probs))
        rows.append(
            {
                "customer_id": f"C{idx:04d}",
                "first_name": first,
                "last_name": last,
                "email": f"{first.lower()}.{last.lower()}.{idx}@example.test",
                "joined_at": (ANCHOR_DATE - pd.Timedelta(days=joined_days_ago)).date().isoformat(),
                "acquisition_source": source,
                "home_store_id": str(_choice(rng, [item[0] for item in STORES])),
                "marketing_consent": bool(rng.random() < 0.83),
                "customer_type": "corporate_contact" if source == "corporate_event" else "individual",
            }
        )

    customers = pd.DataFrame(rows)
    customers.loc[customers.index[-9:], "acquisition_source"] = ""
    for offset in range(1, 5):
        customers.loc[customers.index[-offset], "email"] = customers.loc[offset - 1, "email"]
    return customers


def _generate_orders_and_items(
    rng: np.random.Generator, customers: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    orders: list[dict] = []
    order_items: list[dict] = []
    order_number = 1
    channels = ["in_store", "website", "mobile_app", "uber_eats"]
    channel_probs = [0.39, 0.23, 0.27, 0.11]
    menu_ids = [item[0] for item in MENU]
    prices = {item[0]: item[3] for item in MENU}
    costs = {item[0]: item[4] for item in MENU}

    for _, customer in customers.iterrows():
        joined = pd.Timestamp(customer["joined_at"])
        tenure_days = max(30, int((ANCHOR_DATE - joined).days))
        value_tier = float(rng.beta(2.2, 3.5))
        expected_orders = 1.2 + value_tier * 17
        order_count = max(1, int(rng.poisson(expected_orders)))

        # The fictional population has a learnable but noisy retention pattern:
        # historically higher-value customers are less likely to become dormant.
        # This keeps the churn demo realistic without making the target trivial.
        lifecycle_draw = rng.random()
        dormant_probability = 0.58 - (0.50 * value_tier)
        cooling_probability = 0.25 - (0.08 * value_tier)
        if tenure_days >= 61 and lifecycle_draw < dormant_probability:
            last_days_ago = int(rng.integers(61, min(180, tenure_days) + 1))
        elif tenure_days >= 22 and lifecycle_draw < dormant_probability + cooling_probability:
            last_days_ago = int(rng.integers(22, min(60, tenure_days) + 1))
        else:
            last_days_ago = int(rng.integers(0, min(21, tenure_days) + 1))
        last_order_date = ANCHOR_DATE - pd.Timedelta(days=last_days_ago)
        earliest = max(joined, ANCHOR_DATE - pd.Timedelta(days=365))
        span = max(1, int((last_order_date - earliest).days))
        sampled_offsets = sorted(rng.integers(0, span + 1, size=order_count).tolist())
        sampled_dates = [earliest + pd.Timedelta(days=int(offset)) for offset in sampled_offsets]
        sampled_dates[-1] = last_order_date
        # Returning customers usually leave a recent pre-outcome signal as well
        # as an order in the next 60 days. The classifier must still discover
        # that signal from history; future rows never enter its feature window.
        model_cutoff = ANCHOR_DATE - pd.Timedelta(days=60)
        if order_count >= 2 and last_order_date >= model_cutoff:
            bridge_date = model_cutoff - pd.Timedelta(days=int(rng.integers(2, 24)))
            if bridge_date >= earliest:
                sampled_dates[-2] = bridge_date
                sampled_dates = sorted(sampled_dates)

        for order_date in sampled_dates:
            order_id = f"O{order_number:06d}"
            order_number += 1
            channel = str(_choice(rng, channels, channel_probs))
            store_id = customer["home_store_id"] if rng.random() < 0.78 else str(_choice(rng, [s[0] for s in STORES]))
            item_count = int(rng.integers(1, 5))
            allowed = menu_ids if customer["customer_type"] == "corporate_contact" else menu_ids[:-2]
            selected = rng.choice(allowed, size=item_count, replace=True)
            subtotal = 0.0
            total_cost = 0.0
            for menu_id in selected:
                quantity = 2 if rng.random() < 0.12 else 1
                subtotal += prices[str(menu_id)] * quantity
                total_cost += costs[str(menu_id)] * quantity
                order_items.append(
                    {
                        "order_id": order_id,
                        "menu_item_id": str(menu_id),
                        "quantity": quantity,
                        "unit_price": prices[str(menu_id)],
                        "unit_cost": costs[str(menu_id)],
                    }
                )
            discount = round(subtotal * (0.15 if rng.random() < 0.18 else 0), 2)
            delivery_fee = 4.5 if channel in {"website", "mobile_app", "uber_eats"} and rng.random() < 0.55 else 0
            total = round(subtotal - discount + delivery_fee, 2)
            orders.append(
                {
                    "order_id": order_id,
                    "customer_id": customer["customer_id"],
                    "store_id": store_id,
                    "ordered_at": (order_date + pd.Timedelta(hours=int(rng.integers(10, 21)))).isoformat(),
                    "channel": channel,
                    "subtotal": round(subtotal, 2),
                    "discount": discount,
                    "delivery_fee": delivery_fee,
                    "total": total,
                    "gross_margin": round(total - total_cost, 2),
                    "status": "completed" if rng.random() > 0.035 else "cancelled",
                }
            )

    orders.append(
        {
            "order_id": "O999999",
            "customer_id": "C9999",
            "store_id": "ST01",
            "ordered_at": (ANCHOR_DATE - pd.Timedelta(days=3)).isoformat(),
            "channel": "website",
            "subtotal": 49.90,
            "discount": 0,
            "delivery_fee": 0,
            "total": 49.90,
            "gross_margin": 31.20,
            "status": "completed",
        }
    )
    return pd.DataFrame(orders), pd.DataFrame(order_items)


def _generate_web_events(rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    event_number = 1
    paths = [
        (["session_start", "menu_view"], 0.31),
        (["session_start", "menu_view", "offer_click"], 0.19),
        (["session_start", "menu_view", "begin_checkout"], 0.18),
        (["session_start", "menu_view", "offer_click", "begin_checkout", "purchase"], 0.32),
    ]
    for _, customer in customers.sample(n=560, random_state=RANDOM_SEED).iterrows():
        session_count = int(rng.integers(1, 7))
        for session_idx in range(session_count):
            base_time = ANCHOR_DATE - pd.Timedelta(days=int(rng.integers(0, 91))) + pd.Timedelta(
                hours=int(rng.integers(8, 22))
            )
            draw = rng.random()
            cumulative = 0.0
            selected_path = paths[0][0]
            for path, probability in paths:
                cumulative += probability
                if draw <= cumulative:
                    selected_path = path
                    break
            session_id = f"S{customer['customer_id'][1:]}-{session_idx + 1:02d}"
            for step, event_name in enumerate(selected_path):
                rows.append(
                    {
                        "event_id": f"E{event_number:07d}",
                        "session_id": session_id,
                        "customer_id": customer["customer_id"],
                        "event_name": event_name,
                        "event_at": (base_time + pd.Timedelta(minutes=step * 2)).isoformat(),
                        "source": customer["acquisition_source"] or "unknown",
                        "store_id": customer["home_store_id"],
                    }
                )
                event_number += 1
    for idx in range(3):
        rows.append(
            {
                "event_id": f"E{event_number + idx:07d}",
                "session_id": f"ORPHAN-{idx + 1}",
                "customer_id": "C9999",
                "event_name": "offer_click",
                "event_at": (ANCHOR_DATE - pd.Timedelta(days=idx + 1)).isoformat(),
                "source": "paid_social",
                "store_id": "ST02",
            }
        )
    return pd.DataFrame(rows)


def _generate_campaigns(rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    campaigns = [
        ("LUNCH_WINBACK", "Lunch win-back", 0.16, 0.22, 1.10),
        ("SALMON_LOYALTY", "Loyalty thank-you", 0.21, 0.25, 0.85),
        ("CATERING_AWARENESS", "Corporate catering", 0.09, 0.15, 1.60),
    ]
    rows: list[dict] = []
    eligible = customers[customers["marketing_consent"]].copy()
    for campaign_id, campaign_name, conv_a, conv_b, contact_cost in campaigns:
        recipients = eligible.sample(n=min(360, len(eligible)), random_state=RANDOM_SEED + len(rows))
        for position, (_, customer) in enumerate(recipients.iterrows()):
            variant = "A" if position % 2 == 0 else "B"
            base_rate = conv_a if variant == "A" else conv_b
            source_bonus = 0.035 if customer["acquisition_source"] in {"email", "referral"} else 0
            converted = bool(rng.random() < min(0.8, base_rate + source_bonus))
            opened = bool(converted or rng.random() < 0.61)
            clicked = bool(converted or (opened and rng.random() < 0.34))
            revenue = round(float(rng.uniform(18, 92)), 2) if converted else 0.0
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "variant": variant,
                    "customer_id": customer["customer_id"],
                    "sent_at": (ANCHOR_DATE - pd.Timedelta(days=int(rng.integers(6, 75)))).date().isoformat(),
                    "opened": opened,
                    "clicked": clicked,
                    "converted": converted,
                    "revenue": revenue,
                    "contact_cost": contact_cost,
                }
            )
    return pd.DataFrame(rows)


def _generate_catering_leads(rng: np.random.Generator) -> pd.DataFrame:
    sources = ["website", "referral", "university_event", "linkedin", "phone"]
    stages = ["new", "qualified", "quoted", "won", "lost"]
    stage_probs = [0.18, 0.21, 0.23, 0.25, 0.13]
    rows: list[dict] = []
    for idx in range(1, 91):
        created_days_ago = int(rng.integers(2, 130))
        created = ANCHOR_DATE - pd.Timedelta(days=created_days_ago)
        stage = str(_choice(rng, stages, stage_probs))
        headcount = int(rng.integers(20, 220))
        expected_value = round(headcount * float(rng.uniform(13.5, 24.0)), 2)
        last_contacted = created + pd.Timedelta(days=int(rng.integers(0, max(1, created_days_ago))))
        rows.append(
            {
                "lead_id": f"L{idx:04d}",
                "company": f"Auckland Organisation {idx:02d}",
                "contact_email": f"events{idx:02d}@example.test",
                "source": str(_choice(rng, sources)),
                "stage": stage,
                "created_at": created.date().isoformat(),
                "event_date": (ANCHOR_DATE + pd.Timedelta(days=int(rng.integers(5, 120)))).date().isoformat(),
                "headcount": headcount,
                "expected_value": expected_value,
                "owner_store_id": str(_choice(rng, [s[0] for s in STORES])),
                "last_contacted_at": last_contacted.date().isoformat(),
                "dietary_requirements": str(_choice(rng, ["none", "vegetarian", "gluten-aware", "mixed"])),
            }
        )
    rows[-1]["stage"] = "quote_pending-ish"
    return pd.DataFrame(rows)


def generate_dataset(output_dir: Path) -> dict[str, int]:
    """Generate every CSV and return row counts by source."""

    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(RANDOM_SEED)
    customers = _generate_customers(rng)
    orders, order_items = _generate_orders_and_items(rng, customers)
    web_events = _generate_web_events(rng, customers)
    campaigns = _generate_campaigns(rng, customers)
    catering = _generate_catering_leads(rng)
    stores = pd.DataFrame(STORES, columns=["store_id", "store_name", "area"])
    menu = pd.DataFrame(MENU, columns=["menu_item_id", "name", "category", "price", "unit_cost"])

    frames = {
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
        "web_events": web_events,
        "campaign_responses": campaigns,
        "catering_leads": catering,
        "stores": stores,
        "menu": menu,
    }
    return {name: _write(frame, output_dir, name) for name, frame in frames.items()}
