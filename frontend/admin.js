const summaryEl = document.querySelector("#summary");
const checksBody = document.querySelector("#checks-body");
const keysBody = document.querySelector("#keys-body");
const newKeyEl = document.querySelector("#new-key");
const keyForm = document.querySelector("#key-form");
const detailEl = document.querySelector("#check-detail");
const workspaceNote = document.querySelector("#workspace-note");

const WORKSPACE = {
  concrete: "Concrete slab · ACI 305R / 306R",
  masonry: "Masonry · TMS 602 / ACI 530.1",
};

function metric(label, value) {
  const wrap = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = value;
  wrap.append(dt, dd);
  return wrap;
}

function fmt(stamp) {
  if (!stamp) return "—";
  return String(stamp).replace("T", " ").slice(0, 16);
}

function escapeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.href = "/admin/login";
    throw new Error("Not signed in.");
  }
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || response.statusText);
  }
  return payload;
}

let allChecks = [];
let productFilter = "concrete";
let selectedId = null;

function filteredChecks() {
  return allChecks.filter((row) => (row.product || "concrete") === productFilter);
}

function mixLine(row) {
  const mix = row.mix_design || {};
  if ((row.product || "concrete") === "masonry") {
    const unit = mix.unit_type || "CMU";
    const mortar = String(mix.mortar_type || "Type_N").replaceAll("_", " ");
    return `${unit} · ${mortar}`;
  }
  const cement = String(mix.cement_type || "Type_I").replaceAll("_", " ");
  const parts = [cement];
  if (mix.target_psi) parts.push(`${mix.target_psi} psi`);
  if (mix.thickness_inches) parts.push(`${mix.thickness_inches} in`);
  return parts.join(" · ");
}

function predictionLine(row) {
  const predictions = row.predictions || {};
  if ((row.product || "concrete") === "masonry") {
    if (predictions.protection_period_hours == null) return "Protection window: see TMS 602";
    return `Protect wall ~${predictions.protection_period_hours} hr after laying`;
  }
  const to500 =
    predictions.estimated_time_to_500_psi_hours == null
      ? "500 psi: not in forecast"
      : `500 psi: ~${predictions.estimated_time_to_500_psi_hours} hr`;
  const to70 =
    predictions.estimated_days_to_70_percent_strength == null
      ? "70% strength: not in forecast"
      : `70% strength: ~${predictions.estimated_days_to_70_percent_strength} days`;
  return `${to500} · ${to70}`;
}

function watchLine(row) {
  const events = row.watch_events || [];
  const updates = `${events.length} update${events.length === 1 ? "" : "s"}`;
  if (!row.watching) return `Watch closed · ${updates}`;
  return `Watching · last checked ${fmt(row.last_checked_at)} · ${updates}`;
}

function watchEvents(row) {
  const events = (row.watch_events || []).slice(-5).reverse();
  if (!events.length) return "";
  return events
    .map(
      (event) =>
        `<li><strong>${escapeText(event.label || event.code)}</strong>${escapeText(
          event.detail || ""
        )}<span class="watch-time">${escapeText(fmt(event.at))}</span></li>`
    )
    .join("");
}

function summarize(rows) {
  const withOutcome = rows.filter((row) => row.outcome);
  const byStatus = {};
  let goSuccess = 0;
  let goBad = 0;
  rows.forEach((row) => {
    const status = row.go_no_go_status || "UNKNOWN";
    byStatus[status] = (byStatus[status] || 0) + 1;
    if (status === "GO" && row.outcome === "success") goSuccess += 1;
    if (status === "GO" && (row.outcome === "cracked" || row.outcome === "delayed")) goBad += 1;
  });
  return {
    total: rows.length,
    watching: rows.filter((row) => row.watching).length,
    withOutcome: withOutcome.length,
    pending: rows.length - withOutcome.length,
    go: byStatus.GO || 0,
    warning: byStatus.WARNING || 0,
    nogo: byStatus.NO_GO || 0,
    goSuccess,
    goBad,
  };
}

function renderSummary(rows) {
  const summary = summarize(rows);
  summaryEl.innerHTML = "";
  summaryEl.append(
    metric("Checks", summary.total),
    metric("GO", summary.go),
    metric("Warning", summary.warning),
    metric("No-go", summary.nogo),
    metric("Watching", summary.watching),
    metric("With outcome", summary.withOutcome),
    metric("Pending", summary.pending),
    metric("GO + success", summary.goSuccess),
    metric("GO + cracked/delayed", summary.goBad)
  );
}

function renderDetail(row) {
  if (!row) {
    const label = productFilter === "masonry" ? "masonry lay-up" : "concrete slab";
    detailEl.innerHTML = `<p class="empty-kicker">No ${escapeText(label)} checks yet</p>
      <p>Run a ${escapeText(label)} ticket on the demo. This panel will show mix, risks, metrics, and predictions.</p>`;
    return;
  }

  const metrics = row.metrics || {};
  const product = row.product || "concrete";
  const materialLabel = product === "masonry" ? "Mortar temp" : "Concrete temp";
  const kicker = product === "masonry" ? "Lay advisory" : "Pour advisory";
  const details = row.risk_factor_details || [];
  const risks =
    details.length === 0
      ? `<li><strong>No risk factors triggered</strong>Forecast sat inside normal placement ranges.</li>`
      : details
          .map(
            (factor) =>
              `<li><strong>${escapeText(factor.label)}</strong>${escapeText(factor.detail)}</li>`
          )
          .join("");

  const fmtMetric = (value, suffix) => (value == null || value === "" ? "—" : `${value}${suffix || ""}`);

  detailEl.innerHTML = `
    <div class="result-top">
      <div class="stamp" data-status="${escapeText(row.go_no_go_status)}">
        <span class="stamp-kicker">${kicker}</span>
            <span class="stamp-status">${escapeText(String(row.go_no_go_status || "").replace("_", "-"))}</span>
      </div>
      <div class="result-meta">
        <h2>${escapeText(row.location_name || row.zip_code || "Unknown site")}</h2>
        <p>${escapeText(fmt(row.pour_date))} · ${escapeText(mixLine(row))}</p>
      </div>
    </div>
    <ul class="risk-list">${risks}</ul>
    <dl class="metrics"></dl>
    <div class="predictions"><span>${escapeText(predictionLine(row))}</span></div>
    <blockquote class="mitigation">${escapeText(row.recommended_mitigation || "No mitigation stored.")}</blockquote>
    <p class="disclaimer">Outcome: ${escapeText(row.outcome || "pending")} · Source: ${escapeText(
      row.source || "—"
    )} · ${escapeText(watchLine(row))}</p>
    <ol class="watch-log">${watchEvents(row)}</ol>
  `;
  const metricsEl = detailEl.querySelector("dl.metrics");
  metricsEl.append(
    metric("Air temp", fmtMetric(metrics.ambient_temp_f, " °F")),
    metric(materialLabel, fmtMetric(metrics.concrete_temp_f ?? row.concrete_temp_f, " °F")),
    metric("RH", fmtMetric(metrics.relative_humidity_pct, "%")),
    metric("Wind", fmtMetric(metrics.wind_speed_mph, " mph")),
    metric("Evaporation", fmtMetric(metrics.calculated_evaporation_rate_lbs_sqft_hr, " lb/ft²/hr")),
    metric("Rain", fmtMetric(metrics.precipitation_in, " in")),
    metric("Min 24h", fmtMetric(metrics.min_temp_next_24h_f, " °F")),
    metric("Min 48h", fmtMetric(metrics.min_temp_next_48h_f, " °F"))
  );
}

function renderCheckRows() {
  const rows = filteredChecks();
  workspaceNote.textContent = WORKSPACE[productFilter];
  renderSummary(rows);
  checksBody.innerHTML = "";
  if (!rows.length) {
    selectedId = null;
    checksBody.innerHTML = '<tr><td colspan="5">No checks for this product yet.</td></tr>';
    renderDetail(null);
    return;
  }
  if (!rows.some((row) => row.id === selectedId)) {
    selectedId = rows[0].id;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const outcome = row.outcome || "pending";
    tr.dataset.id = row.id;
    if (row.id === selectedId) tr.classList.add("selected");
    tr.innerHTML = `<td>${escapeText(fmt(row.created_at))}</td>
      <td>${escapeText(row.location_name || row.zip_code || "—")}</td>
      <td><span class="pill" data-status="${escapeText(row.go_no_go_status)}">${escapeText(
      row.go_no_go_status
    )}</span></td>
      <td><span class="pill" data-outcome="${escapeText(outcome)}">${escapeText(outcome)}</span></td>
      <td>${escapeText(row.source || "—")}</td>`;
    checksBody.append(tr);
  });
  renderDetail(rows.find((row) => row.id === selectedId) || rows[0]);
}

async function loadChecks() {
  const payload = await api("/admin/api/checks");
  allChecks = payload.checks;
  renderCheckRows();
}

async function loadKeys() {
  const payload = await api("/admin/api/keys");
  keysBody.innerHTML = "";
  if (!payload.keys.length) {
    keysBody.innerHTML = '<tr><td colspan="5">No keys yet. Mint one to act as a vendor.</td></tr>';
    return;
  }
  payload.keys.forEach((row) => {
    const tr = document.createElement("tr");
    const status = row.active ? "active" : "revoked";
    const revoke = row.active ? `<button type="button" data-revoke="${row.id}">Revoke</button>` : "";
    tr.innerHTML = `<td>${escapeText(row.name)}</td>
      <td><code>${escapeText(row.key_prefix)}…</code></td>
      <td>${escapeText(row.rate_limit_per_hour)}/hr</td>
      <td>${status}</td>
      <td>${revoke}</td>`;
    keysBody.append(tr);
  });
}

document.querySelectorAll(".admin-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".admin-tabs button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector("#tab-checks").hidden = button.dataset.tab !== "checks";
    document.querySelector("#tab-keys").hidden = button.dataset.tab !== "keys";
  });
});

document.querySelector("#logout-btn").addEventListener("click", async () => {
  await api("/admin/api/logout", { method: "POST" });
  window.location.href = "/admin/login";
});

keysBody.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-revoke]");
  if (!button) return;
  if (!window.confirm("Revoke this key? Existing vendor calls will start failing.")) return;
  await api(`/admin/api/keys/${button.dataset.revoke}/revoke`, { method: "POST" });
  await loadKeys();
});

keyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = await api("/admin/api/keys", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#key-name").value,
      rate_limit_per_hour: Number(document.querySelector("#key-limit").value),
    }),
  });
  newKeyEl.hidden = false;
  newKeyEl.innerHTML = `<strong>Copy now:</strong> <code>${payload.key}</code><br>${payload.warning}`;
  document.querySelector("#key-name").value = "";
  await loadKeys();
});

checksBody.addEventListener("click", (event) => {
  const tr = event.target.closest("tr[data-id]");
  if (!tr) return;
  selectedId = tr.dataset.id;
  renderCheckRows();
});

document.querySelectorAll("#product-filter button").forEach((button) => {
  button.addEventListener("click", () => {
    productFilter = button.dataset.filter;
    selectedId = null;
    document.querySelectorAll("#product-filter button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderCheckRows();
  });
});

loadChecks().catch((error) => {
  checksBody.innerHTML = `<tr><td colspan="5">${escapeText(error.message)}</td></tr>`;
});
loadKeys().catch(() => {});
