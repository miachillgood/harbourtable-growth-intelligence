# HarbourTable

Restaurant growth intelligence for a fictional four-location Auckland hospitality group. HarbourTable turns customer, order, web, campaign, and corporate-catering data into decisions a small growth team can review and act on. Its operating model is reusable across restaurants, cafés, quick-service concepts, and catering operations rather than tied to one cuisine.

The product brings commercial analytics, customer intelligence, experiment measurement, B2B pipeline management, and approval-first CRM actions into one traceable workflow.

## Business scenario

A multi-location hospitality operator has data scattered across its point-of-sale exports, website events, campaign results, and catering enquiries. The growth team needs to answer six practical questions:

1. Are revenue and orders improving, and which acquisition channels bring repeat customers?
2. Which valuable customers are becoming inactive?
3. Did campaign variant B really outperform A, or is the sample still inconclusive?
4. Which corporate catering leads are stuck and need follow-up?
5. What should the team do today, based on traceable evidence?
6. Can a recommendation be prepared for CRM without silently messaging or changing customer data?

The application answers those questions in one local dashboard and keeps every mutating recommendation behind a human approval step.

## What is implemented

- Deterministic synthetic data generator with 720 customers, roughly 5,700 orders, GA4-style web events, three A/B campaigns, and 90 B2B catering leads.
- Reconciled commercial KPIs: 30-day revenue, orders, AOV, 90-day repeat rate, web conversion, weekly trend, and acquisition-channel quality.
- Customer 360 with RFM segments and a logistic-regression churn propensity model.
- Honest model evaluation: features stop at a historical cutoff and ROC-AUC is calculated on a stratified holdout set from the following 60-day outcome window.
- Campaign experiment cards with conversion lift, approximate two-proportion confidence, and an explicit `Prefer B` / `Keep testing` rule.
- Corporate-catering lifecycle funnel, open pipeline value, and seven-day stale-lead detection.
- Evidence-grounded daily brief. It works without an LLM; an optional OpenAI-compatible endpoint receives aggregate facts only.
- Approval queue backed by SQLite. Approve/reject decisions and connector results are persisted in an audit log.
- HubSpot contact-upsert adapter with two independent controls: a token must exist and `HUBSPOT_ENABLE_WRITES=true`. Mock mode is the default and never makes an external write.
- Visible data-quality layer that catches duplicate emails, missing acquisition sources, orphan records, and invalid lifecycle stages. Invalid financial records are excluded from KPIs rather than hidden.
- Responsive, dependency-free frontend with filters and custom SVG/CSS charts; no CDN or real customer data required.
- GitHub Actions workflow for the Python test suite and browser JavaScript syntax check.

## Architecture

```text
Synthetic source generator
  ├─ customers / orders / order items
  ├─ GA4-style web events
  ├─ campaign outcomes
  └─ catering leads
           │
           ▼
Pandas metric + feature layer ──► scikit-learn temporal churn model
           │
           ├─► dashboard JSON API ──► responsive web interface
           ├─► evidence-only daily brief
           └─► suggested actions ──► SQLite approval + audit log
                                         │
                                         └─► HubSpot adapter (mock by default)
```

## Run locally

Python 3.11 or newer is recommended.

```bash
git clone https://github.com/miachillgood/harbourtable-growth-intelligence.git
cd harbourtable-growth-intelligence
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m app.server
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). The server generates the fictional CSV files automatically on first run.

To regenerate the same dataset deliberately:

```bash
python3 scripts/seed_data.py
```

To run the verification suite:

```bash
pytest -q
```

## Optional integrations

Copy `.env.example` values into your own shell or secret manager. The application does not load or commit a `.env` file automatically.

### HubSpot

The live path implements HubSpot's contact batch-upsert-by-email endpoint. It requires a private app token with contact write permission and both variables below:

```bash
export HUBSPOT_ACCESS_TOKEN="your-private-app-token"
export HUBSPOT_ENABLE_WRITES="true"
```

An approved action is still required in the interface. Only contact upsert is supported in live mode; catering tasks and experiment notes remain local drafts. See the [official HubSpot contact API guide](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/contacts/guide).

### Aggregate-only LLM brief

```bash
export LLM_API_URL="https://your-provider.example/v1/chat/completions"
export LLM_API_KEY="your-key"
export LLM_MODEL="your-model"
```

Only the six aggregate evidence sentences displayed in the UI are eligible to leave the local process. Customer names, emails, orders, and lead rows are not included in the prompt. If the provider is absent or fails, deterministic evidence rules produce the brief.

## Metric and modelling notes

- **Revenue:** sum of positive, completed orders with a customer ID that resolves to the customer table.
- **Repeat rate:** share of customers with at least two completed orders in the latest 90 days.
- **Web conversion:** distinct purchase sessions divided by distinct sessions in the latest 30 days.
- **At-risk value:** historical value for customers in the high model-risk tier and above the 65th monetary percentile.
- **Campaign lift:** `(B conversion rate - A conversion rate) / A conversion rate`.
- **Churn label:** no completed order in the 60 days after the feature cutoff.
- **Guardrail:** records inside that 60-day label window never enter model features.

The synthetic generator deliberately gives engagement behaviour a noisy relationship with retention, so the model has signal without becoming perfectly separable. Its holdout score is evidence about this fictional dataset only; it is not a claim of production performance.

## Data ethics and validation boundary

- Every email uses the reserved `.test` domain.
- No restaurant, customer, Google, or HubSpot account is accessed by default.
- Approval in mock mode records a realistic workflow but explicitly reports `external_write: false`.
- Marketing consent is represented in the source data, but a real deployment would still need suppression lists, legal review, identity resolution, role-based access, retention policy, and experiment governance.
- The catering pipeline is designed for pilot validation with a restaurant owner or manager before any claim of real commercial impact.

## Repository map

```text
app/
  analytics.py       KPI, funnel, RFM, experiments, quality and model outputs
  brief.py           evidence-grounded daily brief
  connectors.py      mock-first HubSpot and optional LLM adapters
  data_generator.py  deterministic fictional source system
  server.py          local HTTP and JSON API
  workflow.py        SQLite approvals and audit log
  static/             responsive dashboard UI
data/
  README.md           source definitions and intentional defects
scripts/
  seed_data.py        deterministic data generation entry point
tests/                analytics, workflow and HTTP integration checks
```

## Project claim boundary

Safe claim after independently running the test suite:

> Built HarbourTable, a restaurant GTM intelligence product that unifies transactional, behavioural, campaign, and B2B pipeline signals; temporally validates churn propensity; quantifies experiments; and routes evidence-backed CRM recommendations through a persistent human-approval workflow.

Do not claim that the product has increased revenue, reduced churn, or been deployed by a real restaurant until an actual pilot supplies that evidence.

## Licence

MIT. See `LICENSE`.
