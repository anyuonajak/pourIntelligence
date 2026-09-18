const summaryEl = document.querySelector("#summary");
const checksBody = document.querySelector("#checks-body");
const keysBody = document.querySelector("#keys-body");
const newKeyEl = document.querySelector("#new-key");
const keyForm = document.querySelector("#key-form");
const detailEl = document.querySelector("#check-detail");
const workspaceNote = document.querySelector("#workspace-note");

const WORKSPACE = {
  concrete: "Concrete slab",
  masonry: "Masonry",
};

const NOGO_CODES = new Set([
  "EXTREME_EVAPORATION_RATE",
  "FREEZING_BEFORE_500_PSI",
  "HEAVY_RAIN_DURING_POUR",
  "MASONRY_BELOW_20F",
]);

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
let watchFilter = "watching";
let selectedId = null;

function watchState(row) {
  return row.watching ? "watching" : "closed";
}

function filteredChecks() {
  return allChecks.filter((row) => {
    if ((row.product || "concrete") !== productFilter) return false;
    if (watchFilter === "all") return true;
    return watchState(row) === watchFilter;
  });
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

function predictionFacts(row) {
  const predictions = row.predictions || {};
  if ((row.product || "concrete") === "masonry") {
    return [
      [
        "Protection period",
        predictions.protection_period_hours == null ? "—" : `${predictions.protection_period_hours} hr`,
      ],
    ];
  }
  return [
    [
      "500 psi",
      predictions.estimated_time_to_500_psi_hours == null
        ? "—"
        : `~${predictions.estimated_time_to_500_psi_hours} hr`,
    ],
    [
      "70% strength",
      predictions.estimated_days_to_70_percent_strength == null
        ? "—"
        : `~${predictions.estimated_days_to_70_percent_strength} days`,
    ],
  ];
}

function watchLine(row) {
  const events = row.watch_events || [];
  const updates = `${events.length} update${events.length === 1 ? "" : "s"}`;
  if (!row.watching) return `Watch closed · ${updates}`;
  return `Watching · last checked ${fmt(row.last_checked_at)} · ${updates}`;
}

function stripSpecCopy(text) {
  return String(text || "")
    .replace(/\b(ACI|TMS)\s*[\d.]+R?(?:\s*\/\s*(?:ACI|TMS)?\s*[\d.]+R?)*/gi, "")
    .replace(/\b(?:ACI|TMS)\b/gi, "")
    .replace(/\badvisory\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:])/g, "$1")
    .trim();
}

function eventSeverity(event) {
  const code = String(event.code || "");
  const current = String(event.current || "").toUpperCase();
  const riskCode = code.startsWith("RISK_ADDED_")
    ? code.slice("RISK_ADDED_".length)
    : code.startsWith("RISK_CLEARED_")
      ? code.slice("RISK_CLEARED_".length)
      : code;
  if (code === "STATUS") {
    if (current === "NO_GO") return "critical";
    if (current === "WARNING") return "warning";
    return "info";
  }
  if (code.startsWith("RISK_CLEARED_")) return "info";
  if (NOGO_CODES.has(riskCode) || NOGO_CODES.has(code)) return "critical";
  if (code.startsWith("RISK_ADDED_") || event.kind === "risk") return "warning";
  if (code.startsWith("CROSS_")) return "warning";
  return "info";
}

function eventFact(event) {
  const code = String(event.code || "");
  if (code === "STATUS") {
    const prev = event.previous ? String(event.previous).replace("_", "-") : "";
    const cur = event.current ? String(event.current).replace("_", "-") : "";
    if (prev && cur) return `${prev} → ${cur}`;
    if (cur) return `Stamped ${cur}`;
    return stripSpecCopy(event.detail) || "Stamp updated";
  }
  if (code.startsWith("RISK_ADDED_")) {
    return stripSpecCopy(event.detail || event.label) || "Risk triggered";
  }
  if (code.startsWith("RISK_CLEARED_")) {
    const name = stripSpecCopy(event.detail || event.label) || "Risk";
    return `${name} cleared`;
  }
  if (code === "WATCH_STARTED") return "Watch started";
  if (code === "WATCH_PAUSED") return "Watch paused";
  if (code === "WATCH_RESUMED") return "Watch resumed";
  if (code === "WATCH_CLOSED") return "Watch closed";
  const label = stripSpecCopy(event.label);
  const detail = stripSpecCopy(event.detail);
  if (code.startsWith("METRIC_") || code.startsWith("CROSS_")) {
    return [label, detail].filter(Boolean).join(" · ");
  }
  if (event.kind === "risk") return label || detail || "Risk triggered";
  return label || detail || "Update";
}

function watchEventRows(row) {
  const events = (row.watch_events || []).slice().reverse();
  const location = row.location_name || row.zip_code || "";
  const productLabel = (row.product || "concrete") === "masonry" ? "Masonry" : "Concrete";
  const secondary = [location, productLabel].filter(Boolean).join(" · ");
  if (!events.length) {
    return `<li class="event-empty">No events yet.</li>`;
  }
  return events
    .map((event) => {
      const severity = eventSeverity(event);
      const sevLabel = severity === "critical" ? "Critical" : severity === "warning" ? "Warning" : "Info";
      return `<li data-severity="${severity}">
        <span class="event-sev">${sevLabel}</span>
        <span class="event-time">${escapeText(fmt(event.at))}</span>
        <span class="event-body">
          <span class="event-fact">${escapeText(eventFact(event))}</span>
          <span class="event-sub">${escapeText(secondary)}</span>
        </span>
      </li>`;
    })
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
    metric("Closed", summary.total - summary.watching),
    metric("With outcome", summary.withOutcome),
    metric("Pending", summary.pending),
    metric("GO + success", summary.goSuccess),
    metric("GO + cracked/delayed", summary.goBad)
  );
}

function renderDetail(row) {
  if (!row) {
    const label = productFilter === "masonry" ? "masonry lay-up" : "concrete slab";
    detailEl.innerHTML = `<p class="empty-kicker">No ${escapeText(label)} checks yet</p>`;
    return;
  }

  const metrics = row.metrics || {};
  const product = row.product || "concrete";
  const materialLabel = product === "masonry" ? "Mortar temp" : "Concrete temp";
  const kicker = product === "masonry" ? "Masonry ticket" : "Concrete slab ticket";
  const fmtMetric = (value, suffix) => (value == null || value === "" ? "—" : `${value}${suffix || ""}`);

  detailEl.innerHTML = `
    <div class="result-head">
      <div class="result-meta">
        <p class="stamp-kicker">${kicker}</p>
        <h2>${escapeText(row.location_name || row.zip_code || "Unknown site")}</h2>
        <p>${escapeText(fmt(row.pour_date))} · ${escapeText(mixLine(row))}</p>
      </div>
      <div class="stamp" data-status="${escapeText(row.go_no_go_status)}">
        <span class="stamp-status">${escapeText(String(row.go_no_go_status || "").replace("_", "-"))}</span>
      </div>
    </div>
    <p class="watch-status">${escapeText(watchLine(row))}</p>
    ${
      row.subscriber_email
        ? `<p class="watch-status">Email ${escapeText(row.subscriber_email)}</p>`
        : ""
    }
    <dl class="metrics"></dl>
    <section class="event-log-panel">
      <div class="event-log-head"><h3>Event log</h3></div>
      <ol class="event-log">${watchEventRows(row)}</ol>
    </section>
    <p class="ticket-foot">Outcome: ${escapeText(row.outcome || "pending")} · Source: ${escapeText(
      row.source || "—"
    )}</p>
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
    metric("Min 48h", fmtMetric(metrics.min_temp_next_48h_f, " °F")),
    ...predictionFacts(row).map(([label, value]) => metric(label, value))
  );
}

function renderCheckRows() {
  const rows = filteredChecks();
  workspaceNote.textContent = WORKSPACE[productFilter];
  renderSummary(rows);
  checksBody.innerHTML = "";
  if (!rows.length) {
    selectedId = null;
    const empty =
      watchFilter === "all"
        ? "No checks for this product yet."
        : `No ${watchFilter} watches for this product.`;
    checksBody.innerHTML = `<tr><td colspan="6">${empty}</td></tr>`;
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
    const watch = watchState(row);
    tr.innerHTML = `<td class="mono">${escapeText(fmt(row.created_at))}</td>
      <td>${escapeText(row.location_name || row.zip_code || "—")}</td>
      <td>${watch}</td>
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

document.querySelectorAll("#watch-filter button").forEach((button) => {
  button.addEventListener("click", () => {
    watchFilter = button.dataset.watch;
    selectedId = null;
    document.querySelectorAll("#watch-filter button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderCheckRows();
  });
});

loadChecks().catch((error) => {
  checksBody.innerHTML = `<tr><td colspan="6">${escapeText(error.message)}</td></tr>`;
});
loadKeys().catch(() => {});
