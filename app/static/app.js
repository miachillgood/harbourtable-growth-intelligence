const state = {
  dashboard: null,
  brief: null,
  actions: [],
  integrations: null,
  filters: { store: "all", channel: "all", days: 90 },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const money = new Intl.NumberFormat("en-NZ", {
  style: "currency",
  currency: "NZD",
  maximumFractionDigits: 0,
});
const integer = new Intl.NumberFormat("en-NZ", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("en-NZ", { maximumFractionDigits: 1 });

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function titleCase(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function percentage(value, digits = 1) {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}

function dateLabel(value, options = { day: "numeric", month: "short" }) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-NZ", options).format(new Date(value));
}

function deltaBadge(value, note = "vs prior period") {
  const numeric = Number(value || 0);
  const className = numeric < 0 ? "negative" : numeric === 0 ? "neutral" : "";
  const sign = numeric > 0 ? "+" : "";
  return `<em class="${className}">${sign}${percentage(numeric)} ${escapeHTML(note)}</em>`;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...options.headers },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function showToast(message, kind = "default") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("show"), 3600);
}

function setupNavigation() {
  const labels = {
    overview: "Command centre",
    customers: "Customer 360",
    campaigns: "Experiments",
    catering: "Catering pipeline",
    agent: "Action queue",
    trust: "Data trust",
  };

  function activate(target) {
    const section = document.getElementById(target) ? target : "overview";
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.target === section));
    $$(".dashboard-section").forEach((item) => item.classList.toggle("active", item.id === section));
    $("#page-title").textContent = labels[section];
    history.replaceState(null, "", `${location.pathname}${location.search}#${section}`);
    // Hash updates and late data rendering can otherwise leave a fresh tab
    // anchored part-way through the first section under the sticky header.
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  $$(".nav-item").forEach((item) => item.addEventListener("click", () => activate(item.dataset.target)));
  $(".brand").addEventListener("click", (event) => {
    event.preventDefault();
    activate("overview");
  });
  activate(location.hash.slice(1) || "overview");
}

function populateFilters(meta) {
  const storeSelect = $("#store-filter");
  const channelSelect = $("#channel-filter");
  if (storeSelect.options.length === 1) {
    storeSelect.innerHTML = meta.stores
      .map((store) => `<option value="${escapeHTML(store.id)}">${escapeHTML(store.name)}</option>`)
      .join("");
  }
  if (channelSelect.options.length === 1) {
    channelSelect.innerHTML = meta.channels
      .map((channel) => `<option value="${escapeHTML(channel)}">${channel === "all" ? "All channels" : escapeHTML(titleCase(channel))}</option>`)
      .join("");
  }
  storeSelect.value = state.filters.store;
  channelSelect.value = state.filters.channel;
}

function renderKpis(overview) {
  const kpis = [
    {
      label: "30-day revenue",
      value: money.format(overview.revenue_30d),
      detail: deltaBadge(overview.revenue_delta),
      primary: true,
    },
    {
      label: "30-day orders",
      value: integer.format(overview.orders_30d),
      detail: deltaBadge(overview.orders_delta),
    },
    {
      label: "Average order value",
      value: money.format(overview.aov_30d),
      detail: deltaBadge(overview.aov_delta),
    },
    {
      label: "90-day repeat rate",
      value: percentage(overview.repeat_rate_90d),
      detail: '<em class="neutral">customers with 2+ orders</em>',
    },
    {
      label: "Website conversion",
      value: percentage(overview.web_conversion_30d),
      detail: '<em class="neutral">session → purchase</em>',
    },
    {
      label: "Value currently at risk",
      value: money.format(overview.at_risk_value),
      detail: '<em class="negative">high value + high risk</em>',
    },
  ];
  $("#kpi-grid").innerHTML = kpis
    .map(
      (kpi) => `<article class="kpi-card ${kpi.primary ? "primary" : ""}">
        <span>${escapeHTML(kpi.label)}</span><strong>${escapeHTML(kpi.value)}</strong>${kpi.detail}
      </article>`,
    )
    .join("");
}

function renderRevenueChart(rows) {
  const container = $("#revenue-chart");
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">No completed orders match these filters.</div>';
    return;
  }
  const width = 900;
  const height = 260;
  const padding = { top: 18, right: 24, bottom: 35, left: 62 };
  const values = rows.map((row) => Number(row.revenue || 0));
  const max = Math.max(...values, 1) * 1.12;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (index) => padding.left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
  const y = (value) => padding.top + plotHeight - (value / max) * plotHeight;
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const area = `${padding.left},${padding.top + plotHeight} ${points} ${x(rows.length - 1)},${padding.top + plotHeight}`;
  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map((ratio) => {
      const yPos = padding.top + plotHeight - ratio * plotHeight;
      return `<line class="grid-line" x1="${padding.left}" y1="${yPos}" x2="${width - padding.right}" y2="${yPos}" />
        <text class="axis-label" x="${padding.left - 9}" y="${yPos + 4}" text-anchor="end">${escapeHTML(money.format(max * ratio))}</text>`;
    })
    .join("");
  const labelEvery = Math.max(1, Math.ceil(rows.length / 7));
  const labels = rows
    .map((row, index) => {
      if (index % labelEvery !== 0 && index !== rows.length - 1) return "";
      return `<text class="axis-label" x="${x(index)}" y="${height - 8}" text-anchor="middle">${escapeHTML(dateLabel(row.ordered_at))}</text>`;
    })
    .join("");
  const dots = values
    .map(
      (value, index) => `<circle class="trend-dot" cx="${x(index)}" cy="${y(value)}" r="4">
        <title>${escapeHTML(dateLabel(rows[index].ordered_at, { day: "numeric", month: "short", year: "numeric" }))}: ${escapeHTML(money.format(value))}</title>
      </circle>`,
    )
    .join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f05a3f" stop-opacity=".24"/><stop offset="100%" stop-color="#f05a3f" stop-opacity="0"/></linearGradient></defs>
    ${grid}<polygon class="trend-area" points="${area}"/><polyline class="trend-line" points="${points}"/>${dots}${labels}
  </svg>`;
}

function renderChannels(rows) {
  const max = Math.max(...rows.map((row) => Number(row.revenue || 0)), 1);
  $("#channel-table").innerHTML = rows.length
    ? rows
        .map(
          (row) => `<tr><td>${escapeHTML(titleCase(row.acquisition_source || "Unknown"))}</td>
            <td><div class="metric-bar"><strong>${escapeHTML(money.format(row.revenue))}</strong><i style="--value:${Math.max(3, (row.revenue / max) * 100)}%"></i></div></td>
            <td>${escapeHTML(percentage(row.repeat_rate))}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="3" class="empty-cell">No channel records match these filters.</td></tr>';
}

function renderWebFunnel(rows) {
  $("#web-funnel").innerHTML = rows
    .map(
      (row) => `<div class="funnel-row"><span>${escapeHTML(titleCase(row.stage))}</span>
        <div class="funnel-bar"><i style="width:${Math.max(0, Number(row.conversion_from_start) * 100)}%"></i></div>
        <strong>${escapeHTML(integer.format(row.sessions))}</strong></div>`,
    )
    .join("");
}

function renderSegments(segments) {
  const palette = ["#f05a3f", "#203746", "#d7963b", "#7399a8", "#c8c3ba", "#8f6d9d"];
  const rows = segments.summary || [];
  const customerTotal = rows.reduce((sum, row) => sum + Number(row.customers || 0), 0);
  $("#segment-total").textContent = integer.format(customerTotal);
  let cursor = 0;
  const stops = rows.map((row, index) => {
    const share = customerTotal ? (Number(row.customers) / customerTotal) * 100 : 0;
    const stop = `${palette[index % palette.length]} ${cursor}% ${cursor + share}%`;
    cursor += share;
    return stop;
  });
  $("#segment-donut").style.background = `conic-gradient(${stops.join(",") || "#ece8e2 0 100%"})`;
  $("#segment-legend").innerHTML = rows
    .map(
      (row, index) => `<div class="legend-row"><i style="--color:${palette[index % palette.length]}"></i>
        <span>${escapeHTML(row.segment)}</span><strong>${escapeHTML(integer.format(row.customers))} · ${escapeHTML(percentage(row.revenue_share, 0))}</strong></div>`,
    )
    .join("");
}

function renderModel(model) {
  $("#model-card").innerHTML = `<div class="model-score"><strong>${escapeHTML(Number(model.holdout_auc).toFixed(3))}</strong><span>holdout ROC-AUC</span></div>
    <div class="model-facts">
      <div class="model-fact"><span>Evaluation rows</span><strong>${escapeHTML(integer.format(model.evaluation_rows))}</strong></div>
      <div class="model-fact"><span>Feature cutoff</span><strong>${escapeHTML(model.feature_cutoff)}</strong></div>
      <div class="model-fact"><span>Outcome window</span><strong>${escapeHTML(model.label_window_days)} days</strong></div>
      <div class="model-fact"><span>Signals</span><strong>${escapeHTML(model.features.length)} behavioural</strong></div>
    </div><p class="model-note">${escapeHTML(model.guardrail)}</p>`;
}

function renderRiskTable(rows) {
  $("#risk-table").innerHTML = rows.length
    ? rows
        .map(
          (row) => `<tr><td><strong>${escapeHTML(row.customer)}</strong><br><small>${escapeHTML(row.customer_id)}</small></td>
            <td>${escapeHTML(row.store_id)}</td><td>${escapeHTML(row.segment)}</td><td>${escapeHTML(integer.format(row.recency_days))} days</td>
            <td>${escapeHTML(money.format(row.historical_value))}</td><td><span class="risk-meter">${escapeHTML(percentage(row.churn_probability, 0))}</span></td>
            <td>${escapeHTML(row.recommended_action)}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="7" class="empty-cell">No high-value, high-risk customers are currently displayed.</td></tr>';
}

function renderExperiments(experiments) {
  $("#experiment-grid").innerHTML = experiments
    .map((experiment) => {
      const maxRate = Math.max(...experiment.variants.map((variant) => Number(variant.conversion_rate)), 0.01);
      const variants = experiment.variants
        .map(
          (variant) => `<div class="variant-row"><strong>${escapeHTML(variant.variant)}</strong>
            <div class="variant-track"><i style="width:${(variant.conversion_rate / maxRate) * 100}%"></i></div>
            <span>${escapeHTML(percentage(variant.conversion_rate))} · ${escapeHTML(integer.format(variant.sent))} sent</span></div>`,
        )
        .join("");
      const confidenceClass = experiment.confidence >= 0.8 ? "healthy" : "warning";
      return `<article class="experiment-card"><p class="panel-kicker">${escapeHTML(experiment.campaign_id)}</p><h3>${escapeHTML(experiment.campaign_name)}</h3>
        ${variants}<div class="experiment-result"><div><span>B vs A lift</span><strong>${escapeHTML(percentage(experiment.lift_b_vs_a))}</strong></div>
        <span class="status-pill ${confidenceClass}">${escapeHTML(percentage(experiment.confidence, 0))} confidence · ${escapeHTML(experiment.decision)}</span></div></article>`;
    })
    .join("");
}

function renderCatering(catering) {
  const max = Math.max(...catering.funnel.map((row) => Number(row.leads || 0)), 1);
  $("#catering-funnel").innerHTML = catering.funnel
    .map(
      (row) => `<div class="catering-stage"><span>${escapeHTML(titleCase(row.stage))}</span>
        <i style="--ratio:${Math.max(0.04, row.leads / max)}"></i><strong>${escapeHTML(integer.format(row.leads))}</strong></div>`,
    )
    .join("");
  $("#pipeline-value").textContent = money.format(catering.open_pipeline_value);
  $("#catering-table").innerHTML = catering.stale_leads.length
    ? catering.stale_leads
        .map(
          (lead) => `<tr><td><strong>${escapeHTML(lead.company)}</strong><br><small>${escapeHTML(lead.source)}</small></td>
            <td>${escapeHTML(titleCase(lead.stage))}</td><td>${escapeHTML(integer.format(lead.headcount))}</td>
            <td>${escapeHTML(money.format(lead.expected_value))}</td><td>${escapeHTML(integer.format(lead.days_idle))} days</td><td>${escapeHTML(lead.owner_store_id)}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="6" class="empty-cell">No opportunities are currently stale.</td></tr>';
}

function renderBrief(brief) {
  $("#brief-provider").textContent = `${brief.provider} · all-store scope`;
  $("#brief-narrative").textContent = brief.narrative;
  $("#brief-evidence").innerHTML = brief.evidence.map((item) => `<span>${escapeHTML(item)}</span>`).join("");
}

function renderActions(actions) {
  $("#action-grid").innerHTML = actions
    .map((action) => {
      const pending = action.status === "pending";
      const result = action.result?.message ? `<div class="action-result">${escapeHTML(action.result.message)}</div>` : "";
      return `<article class="action-card ${pending ? "" : "done"}">
        <div class="action-top"><span class="status-pill ${action.risk === "safe" ? "healthy" : "warning"}">${escapeHTML(action.risk)}</span>
          <span class="status-pill neutral">${escapeHTML(action.status)}</span></div>
        <h3>${escapeHTML(action.title)}</h3><p>${escapeHTML(action.summary)}</p>
        <ul>${action.evidence.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        <div class="action-target"><span>Targets</span><strong>${escapeHTML(integer.format(action.target_count))}</strong></div>
        ${result}
        <div class="action-buttons">
          <button class="btn" data-action-id="${action.id}" data-decision="reject" ${pending ? "" : "disabled"}>Reject</button>
          <button class="btn primary" data-action-id="${action.id}" data-decision="approve" ${pending ? "" : "disabled"}>${action.risk === "safe" ? "Approve note" : "Approve simulation"}</button>
        </div>
      </article>`;
    })
    .join("");
}

function renderQuality(quality) {
  const severityColor = { critical: "#b93632", high: "#e0693f", medium: "#c38a2e", low: "#178461" };
  $("#trust-score").textContent = decimal.format(quality.score);
  $("#trust-policy").textContent = quality.metric_policy;
  $("#quality-checks").innerHTML = quality.checks
    .map(
      (check) => `<div class="quality-row"><i style="--color:${severityColor[check.severity] || "#69727d"}"></i>
        <strong>${escapeHTML(check.check)}</strong><span>${escapeHTML(check.rule)} · ${escapeHTML(check.source)}</span>
        <strong>${escapeHTML(integer.format(check.count))} rows</strong></div>`,
    )
    .join("");
}

function renderIntegrations(integrations) {
  const rows = Object.values(integrations);
  $("#integration-list").innerHTML = rows
    .map((integration) => {
      const name = integration.name || (integration.source ? "GA4-style events" : "Brief generator");
      const ready = Boolean(integration.configured);
      return `<div class="integration"><div class="integration-head"><span>${escapeHTML(name)}</span>
        <span class="status-pill ${ready ? "healthy" : "neutral"}">${escapeHTML(integration.mode)}</span></div>
        <p>${escapeHTML(integration.guardrail)}</p></div>`;
    })
    .join("");
}

function renderSources(rows) {
  $("#source-table").innerHTML = rows
    .map(
      (source) => `<tr><td><strong>${escapeHTML(source.source)}</strong></td><td>${escapeHTML(source.grain)}</td>
        <td>${escapeHTML(integer.format(source.rows))}</td><td>${escapeHTML(source.freshness)}</td><td>${escapeHTML(source.mode)}</td></tr>`,
    )
    .join("");
}

function renderDashboard(payload) {
  state.dashboard = payload;
  populateFilters(payload.meta);
  $("#as-of").textContent = dateLabel(payload.meta.as_of, { day: "numeric", month: "short", year: "numeric" });
  renderKpis(payload.overview);
  renderRevenueChart(payload.revenue_trend);
  renderChannels(payload.channels);
  renderWebFunnel(payload.web_funnel);
  renderSegments(payload.segments);
  renderModel(payload.model);
  renderRiskTable(payload.segments.high_risk);
  renderExperiments(payload.experiments);
  renderCatering(payload.catering);
  renderQuality(payload.data_quality);
  renderSources(payload.sources);
}

async function loadDashboard() {
  const params = new URLSearchParams({
    store: state.filters.store,
    channel: state.filters.channel,
    days: String(state.filters.days),
  });
  renderDashboard(await fetchJSON(`/api/dashboard?${params}`));
}

async function decideAction(actionId, decision, button) {
  button.disabled = true;
  const verb = decision === "approve" ? "Approving" : "Rejecting";
  button.textContent = `${verb}…`;
  try {
    const payload = await fetchJSON(`/api/actions/${actionId}/${decision}`, { method: "POST" });
    const index = state.actions.findIndex((item) => item.id === payload.action.id);
    if (index >= 0) state.actions[index] = payload.action;
    renderActions(state.actions);
    showToast(payload.action.result?.message || `Action ${decision}d.`, "success");
  } catch (error) {
    button.disabled = false;
    button.textContent = decision === "approve" ? "Approve simulation" : "Reject";
    showToast(error.message, "error");
  }
}

async function initialise() {
  setupNavigation();
  try {
    const [dashboard, brief, actionPayload, integrations] = await Promise.all([
      fetchJSON("/api/dashboard?store=all&channel=all&days=90"),
      fetchJSON("/api/brief"),
      fetchJSON("/api/actions"),
      fetchJSON("/api/integrations"),
    ]);
    renderDashboard(dashboard);
    state.brief = brief;
    state.actions = actionPayload.actions;
    state.integrations = integrations;
    renderBrief(brief);
    renderActions(state.actions);
    renderIntegrations(integrations);
    $("#loading").classList.add("hidden");
  } catch (error) {
    $("#loading").innerHTML = `<strong>Dashboard could not load.</strong> ${escapeHTML(error.message)}`;
    showToast(error.message, "error");
  }
}

$("#store-filter").addEventListener("change", async (event) => {
  state.filters.store = event.target.value;
  try {
    await loadDashboard();
    showToast("Store filter applied.");
  } catch (error) {
    showToast(error.message, "error");
  }
});

$("#channel-filter").addEventListener("change", async (event) => {
  state.filters.channel = event.target.value;
  try {
    await loadDashboard();
    showToast("Channel filter applied.");
  } catch (error) {
    showToast(error.message, "error");
  }
});

$("#action-grid").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action-id]");
  if (!button || button.disabled) return;
  decideAction(Number(button.dataset.actionId), button.dataset.decision, button);
});

initialise();
