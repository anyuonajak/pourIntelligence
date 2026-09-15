const form = document.querySelector("#pour-form");
const zipInput = document.querySelector("#zip");
const pourDateInput = document.querySelector("#pour-date");
const submitBtn = document.querySelector("#submit-btn");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const resultBody = document.querySelector("#result-body");
const stamp = document.querySelector("#stamp");
const stampStatus = document.querySelector("#stamp-status");
const resultLocation = document.querySelector("#result-location");
const resultWhen = document.querySelector("#result-when");
const riskList = document.querySelector("#risk-list");
const metricsEl = document.querySelector("#metrics");
const predictionsEl = document.querySelector("#predictions");
const mitigationEl = document.querySelector("#mitigation");
const disclaimerEl = document.querySelector("#disclaimer");
const outcomeEl = document.querySelector("#outcome");
const outcomeStatus = document.querySelector("#outcome-status");
const presetButtons = document.querySelectorAll("#site-presets button");
const watchEl = document.querySelector("#watch");
const watchTitle = document.querySelector("#watch-title");
const watchStatusEl = document.querySelector("#watch-status");
const watchLog = document.querySelector("#watch-log");
const watchToggle = document.querySelector("#watch-toggle");
const watchClose = document.querySelector("#watch-close");
const watchListBody = document.querySelector("#watch-list-body");

const NOGO_CODES = new Set([
  "EXTREME_EVAPORATION_RATE",
  "FREEZING_BEFORE_500_PSI",
  "HEAVY_RAIN_DURING_POUR",
  "MASONRY_BELOW_20F",
]);

const COPY = {
  concrete: {
    heading: "Concrete slab ticket",
    sub: "Jobsite local time. Defaults are a typical Type I slab.",
    workspace: "Concrete slab · ACI 305R / 306R",
    submit: "Check this pour",
    emptyKicker: "Waiting on a concrete ticket",
    emptyBody:
      "Fill the concrete slab ticket and run a check. We stamp it, then keep watching the forecast and restamp if weather moves enough to change the call.",
    dateLabel: "Pour date & time",
    outcomeKicker: "How did this pour go?",
    chartTitle: "Pour window · 48 hours",
    checking: "Checking forecast…",
    error: "Could not check this pour.",
  },
  masonry: {
    heading: "Masonry ticket",
    sub: "Jobsite local time. TMS 602 / ACI 530.1 hot- and cold-weather masonry.",
    workspace: "Masonry · TMS 602 / ACI 530.1",
    submit: "Check this lay-up",
    emptyKicker: "Waiting on a masonry ticket",
    emptyBody:
      "Fill the masonry ticket and run a check. We stamp it, then keep watching the forecast and restamp if weather moves enough to change the call.",
    dateLabel: "Lay-up date & time",
    outcomeKicker: "How did this lay-up go?",
    chartTitle: "Lay-up window · 48 hours",
    checking: "Checking forecast…",
    error: "Could not check this lay-up.",
  },
};

let chart;
let currentCheckId = null;
let currentProduct = "concrete";
let inFlightProduct = null;
const lastByProduct = { concrete: null, masonry: null };

function newWatch() {
  return {
    open: false,
    paused: false,
    polling: false,
    events: [],
    lastCheckedAt: null,
    nextCheckSeconds: 180,
    watchUntil: null,
    request: null,
    closedByUser: false,
  };
}

const watchByProduct = { concrete: newWatch(), masonry: newWatch() };
const WATCH_STORE_KEY = "pi_watches_v1";
const WATCH_STORE_MAX = 20;
let watchRoster = [];
let selectedWatchId = null;
let watchListFilter = "watching";
let watchTimer = null;

function watchEntryState(entry) {
  if (!entry?.watch?.open) return "closed";
  if (entry.watch.paused) return "paused";
  return "watching";
}

function loadWatchRoster() {
  try {
    const raw = localStorage.getItem(WATCH_STORE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    watchRoster = Array.isArray(parsed) ? parsed : [];
  } catch {
    watchRoster = [];
  }
}

function isPersistedCheckId(value) {
  const text = String(value || "").trim();
  if (!text || text.startsWith("local-")) return false;
  return (
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text) ||
    /^[0-9a-f]{32}$/i.test(text)
  );
}

function persistWatchRoster() {
  try {
    const slim = watchRoster.slice(0, WATCH_STORE_MAX).map((entry) => ({
      ...entry,
      payload: {
        ...entry.payload,
        hourly: entry.id === selectedWatchId ? entry.payload.hourly || [] : [],
      },
    }));
    localStorage.setItem(WATCH_STORE_KEY, JSON.stringify(slim));
  } catch {
    /* quota or private mode */
  }
}

function upsertWatchEntry({ id, product, location, pourDateValue, request, payload, watch, unread = false }) {
  const next = { id, product, location, pourDateValue, request, payload, watch, unread };
  watchRoster = [next, ...watchRoster.filter((entry) => entry.id !== id)].slice(0, WATCH_STORE_MAX);
  persistWatchRoster();
  renderWatchList();
}

function renderWatchList() {
  const rows = watchRoster.filter((entry) => watchListFilter === "all" || watchEntryState(entry) === watchListFilter);
  const counts = {
    watching: watchRoster.filter((entry) => watchEntryState(entry) === "watching").length,
    paused: watchRoster.filter((entry) => watchEntryState(entry) === "paused").length,
    closed: watchRoster.filter((entry) => watchEntryState(entry) === "closed").length,
  };
  document.querySelectorAll("#watch-list-filter button").forEach((button) => {
    const key = button.dataset.watchFilter;
    if (key === "watching") button.textContent = `Watching · ${counts.watching}`;
    else if (key === "paused") button.textContent = `Paused · ${counts.paused}`;
    else if (key === "closed") button.textContent = `Closed · ${counts.closed}`;
    else button.textContent = `All · ${watchRoster.length}`;
    button.classList.toggle("active", key === watchListFilter);
  });
  if (!rows.length) {
    const empty =
      watchRoster.length === 0
        ? "No watches yet. Submit a ticket to start one."
        : `No ${watchListFilter} watches.`;
    watchListBody.innerHTML = `<tr><td colspan="5" class="watch-list-empty">${empty}</td></tr>`;
    return;
  }
  watchListBody.innerHTML = "";
  rows.forEach((entry) => {
    const item = document.createElement("tr");
    const state = watchEntryState(entry);
    const productLabel = entry.product === "masonry" ? "Masonry" : "Concrete";
    const stamp = entry.payload?.go_no_go_status || "—";
    item.dataset.watchId = entry.id;
    if (entry.id === selectedWatchId) item.classList.add("selected");
    if (entry.unread && entry.id !== selectedWatchId) item.classList.add("unread");
    const closeControl =
      state === "closed"
        ? ""
        : `<button type="button" class="text-btn" data-close-id="${entry.id}">Close</button>`;
    item.innerHTML = `<td class="mono">${String(entry.pourDateValue || "—").replace("T", " ")}</td>
      <td>
        <span class="site-cell">
          <span class="site-name">${entry.location || "Unknown site"}</span>
          <span class="site-meta">${productLabel}</span>
        </span>
      </td>
      <td><span class="pill" data-status="${stamp}">${stamp.replace("_", "-")}</span></td>
      <td>${state}</td>
      <td>${closeControl}</td>`;
    watchListBody.append(item);
  });
}

function openWatch(id) {
  const entry = watchRoster.find((item) => item.id === id);
  if (!entry) return;
  stopWatchTimer();
  selectedWatchId = id;
  entry.unread = false;
  persistWatchRoster();
  currentProduct = entry.product;
  applyProductChrome(entry.product);
  showError("");
  lastByProduct[entry.product] = { payload: entry.payload, pourDateValue: entry.pourDateValue };
  watchByProduct[entry.product] = entry.watch;
  paintResult(entry.payload, entry.pourDateValue);
  renderWatch(entry.product);
  renderWatchList();
  scheduleWatch();
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function defaultPourTime() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(8, 0, 0, 0);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

function showError(message) {
  formError.hidden = !message;
  formError.textContent = message || "";
}

function formatWhen(isoLocal, timezone) {
  const label = isoLocal.replace("T", " ");
  return timezone ? `${label} · ${timezone}` : label;
}

function metric(label, value) {
  const wrap = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = value;
  wrap.append(dt, dd);
  return wrap;
}

function destroyChart() {
  if (chart) {
    chart.destroy();
    chart = null;
  }
}

function clearResultPanel() {
  currentCheckId = null;
  stamp.dataset.status = "";
  stampStatus.textContent = "";
  document.querySelector(".stamp-kicker").textContent = "";
  resultLocation.textContent = "";
  resultWhen.textContent = "";
  riskList.innerHTML = "";
  metricsEl.innerHTML = "";
  predictionsEl.innerHTML = "";
  mitigationEl.textContent = "";
  disclaimerEl.textContent = "";
  outcomeStatus.hidden = true;
  outcomeStatus.textContent = "";
  outcomeEl.hidden = true;
  watchEl.hidden = true;
  watchLog.innerHTML = "";
  destroyChart();
}

function showEmptyState() {
  const copy = COPY[currentProduct];
  document.querySelector("#empty-kicker").textContent = copy.emptyKicker;
  document.querySelector("#empty-body").textContent = copy.emptyBody;
  emptyState.hidden = false;
  resultBody.hidden = true;
  clearResultPanel();
}

function applyProductChrome(product) {
  const copy = COPY[product];
  const masonry = product === "masonry";
  document.querySelectorAll(".product-switch button").forEach((button) => {
    button.classList.toggle("active", button.dataset.product === product);
  });
  document.querySelector("#fields-concrete").hidden = masonry;
  document.querySelector("#fields-masonry").hidden = !masonry;
  document.querySelector("#ticket-heading").textContent = copy.heading;
  document.querySelector("#ticket-sub").textContent = copy.sub;
  document.querySelector("#date-label").textContent = copy.dateLabel;
  document.querySelector("#chart-title").textContent = copy.chartTitle;
  document.querySelector("#outcome-kicker").textContent = copy.outcomeKicker;
  const workspaceLabel = document.querySelector("#workspace-label");
  if (workspaceLabel) workspaceLabel.textContent = copy.workspace;
  document.body.dataset.product = product;
  if (inFlightProduct === product) {
    submitBtn.disabled = true;
    submitBtn.textContent = copy.checking;
  } else {
    submitBtn.disabled = false;
    submitBtn.textContent = copy.submit;
  }
}

function selectProduct(product) {
  stopWatchTimer();
  currentProduct = product;
  applyProductChrome(product);
  showError("");
  const entry = watchRoster.find((item) => item.product === product);
  if (entry) {
    openWatch(entry.id);
    return;
  }
  const cached = lastByProduct[product];
  if (cached) {
    paintResult(cached.payload, cached.pourDateValue);
    renderWatch(product);
    scheduleWatch();
  } else {
    selectedWatchId = null;
    showEmptyState();
    renderWatchList();
  }
}

function renderChart(hourly) {
  const canvas = document.querySelector("#forecast-chart");
  const labels = hourly.map((point) => point.time.slice(11, 16));
  const temps = hourly.map((point) => point.temp_f);
  const humidity = hourly.map((point) => point.relative_humidity_pct);
  const evaporation = hourly.map((point) => point.evaporation_rate_lbs_sqft_hr);

  destroyChart();

  const tick = { color: "#5c6570", font: { family: "IBM Plex Sans, system-ui, sans-serif", size: 11 } };
  chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Air °F",
          data: temps,
          borderColor: "#1c1e21",
          backgroundColor: "transparent",
          tension: 0.2,
          borderWidth: 1.5,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "RH %",
          data: humidity,
          borderColor: "#5c6570",
          backgroundColor: "transparent",
          borderDash: [4, 4],
          tension: 0.2,
          borderWidth: 1.25,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "Evap lb/ft²/hr",
          data: evaporation,
          borderColor: "#2f5d67",
          backgroundColor: "rgba(47, 93, 103, 0.08)",
          fill: true,
          tension: 0.2,
          borderWidth: 1.25,
          pointRadius: 0,
          yAxisID: "yEvap",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "#5c6570", boxWidth: 12, font: { family: "IBM Plex Sans, system-ui, sans-serif", size: 11 } },
        },
      },
      scales: {
        x: {
          ticks: { ...tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
          grid: { color: "#e8eaee" },
          border: { color: "#d0d4dc" },
        },
        y: {
          title: { display: true, text: "°F / % RH", color: "#5c6570", font: tick.font },
          ticks: tick,
          grid: { color: "#e8eaee" },
          border: { color: "#d0d4dc" },
        },
        yEvap: {
          position: "right",
          title: { display: true, text: "lb/ft²/hr", color: "#5c6570", font: tick.font },
          ticks: tick,
          grid: { drawOnChartArea: false },
          border: { color: "#d0d4dc" },
          suggestedMin: 0,
        },
      },
    },
  });
}

function paintResult(payload, pourDateValue) {
  emptyState.hidden = true;
  resultBody.hidden = false;

  const status = payload.go_no_go_status;
  stamp.dataset.status = status;
  stampStatus.textContent = status.replace("_", "-");
  document.querySelector(".stamp-kicker").textContent =
    payload.product === "masonry" ? "Lay advisory" : "Pour advisory";
  resultLocation.textContent = payload.location.name;
  resultWhen.textContent = formatWhen(pourDateValue, payload.location.timezone);

  riskList.innerHTML = "";
  const details = payload.risk_factor_details || [];
  if (!details.length) {
    const item = document.createElement("li");
    item.innerHTML =
      "<strong>No risk factors triggered</strong>Forecast conditions sit inside normal placement ranges.";
    riskList.append(item);
  } else {
    details.forEach((factor) => {
      const item = document.createElement("li");
      if (NOGO_CODES.has(factor.code)) item.dataset.severity = "nogo";
      else item.dataset.severity = "warn";
      item.innerHTML = `<strong>${factor.label}</strong>${factor.detail}`;
      riskList.append(item);
    });
  }

  const metrics = payload.metrics;
  const materialLabel = payload.product === "masonry" ? "Mortar temp" : "Concrete temp";
  const rainLabel = payload.product === "masonry" ? "Rain at lay-up" : "Rain at pour";
  metricsEl.innerHTML = "";
  metricsEl.append(
    metric("Air temp", `${metrics.ambient_temp_f} °F`),
    metric(materialLabel, `${metrics.concrete_temp_f} °F`),
    metric("Relative humidity", `${metrics.relative_humidity_pct}%`),
    metric("Wind", `${metrics.wind_speed_mph} mph`),
    metric("Evaporation", `${metrics.calculated_evaporation_rate_lbs_sqft_hr} lb/ft²/hr`),
    metric(rainLabel, `${metrics.precipitation_in} in`),
    metric("Min next 24h", metrics.min_temp_next_24h_f == null ? "—" : `${metrics.min_temp_next_24h_f} °F`),
    metric("Min next 48h", metrics.min_temp_next_48h_f == null ? "—" : `${metrics.min_temp_next_48h_f} °F`)
  );

  const predictions = payload.predictions;
  if (payload.product === "masonry") {
    const protect =
      predictions.protection_period_hours == null
        ? "Protection window: see TMS 602"
        : `Protect wall ~${predictions.protection_period_hours} hr after laying`;
    predictionsEl.innerHTML = `<span>${protect}</span><span>TMS 602 / ACI 530.1</span>`;
  } else {
    const timeTo500 =
      predictions.estimated_time_to_500_psi_hours == null
        ? "500 psi: not reached in forecast"
        : `500 psi: ~${predictions.estimated_time_to_500_psi_hours} hr`;
    const timeTo70 =
      predictions.estimated_days_to_70_percent_strength == null
        ? "70% strength: not reached in forecast"
        : `70% strength: ~${predictions.estimated_days_to_70_percent_strength} days`;
    predictionsEl.innerHTML = `<span>${timeTo500}</span><span>${timeTo70}</span>`;
  }

  mitigationEl.textContent = payload.recommended_mitigation;
  disclaimerEl.textContent = payload.disclaimer;
  renderChart(payload.hourly || []);

  currentCheckId = payload.check_id || null;
  outcomeStatus.hidden = true;
  outcomeStatus.textContent = "";
  outcomeEl.hidden = !currentCheckId;
  outcomeEl.querySelectorAll("button").forEach((button) => {
    button.disabled = false;
  });
}

function cacheResult(product, payload, pourDateValue) {
  lastByProduct[product] = { payload, pourDateValue };
}

function clockTime(value) {
  const when = value ? new Date(value) : new Date();
  if (Number.isNaN(when.getTime())) return "—";
  return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function stopWatchTimer() {
  if (watchTimer) {
    clearTimeout(watchTimer);
    watchTimer = null;
  }
}

function scheduleWatch() {
  stopWatchTimer();
  const open = watchRoster.filter((entry) => watchEntryState(entry) === "watching");
  if (!open.length) return;
  const wait = Math.min(...open.map((entry) => entry.watch.nextCheckSeconds || 180));
  watchTimer = setTimeout(() => pollAllWatches(), wait * 1000);
}

function renderWatch(product) {
  const watch = watchByProduct[product];
  const label = product === "masonry" ? "lay-up" : "pour";
  if (!watch.request) {
    watchEl.hidden = true;
    return;
  }
  watchEl.hidden = false;
  watchToggle.hidden = !watch.open;
  watchClose.hidden = !watch.open;
  watchToggle.textContent = watch.paused ? "Resume" : "Pause";

  let state = "watching";
  if (!watch.open) state = "closed";
  else if (watch.paused) state = "paused";
  else if (watch.error) state = "error";
  watchEl.dataset.state = state;

  if (!watch.open) {
    watchTitle.textContent = `Watch closed on this ${label}`;
    watchStatusEl.textContent = watch.closedByUser
      ? `You closed this watch. Last checked ${clockTime(watch.lastCheckedAt)}.`
      : `The protection window has passed. Last checked ${clockTime(watch.lastCheckedAt)}.`;
  } else if (watch.paused) {
    watchTitle.textContent = `Watch paused on this ${label}`;
    watchStatusEl.textContent = `Last checked ${clockTime(watch.lastCheckedAt)}. Resume to keep monitoring the forecast.`;
  } else if (watch.error) {
    watchTitle.textContent = `Watching this ${label}`;
    watchStatusEl.textContent = `Could not reach the forecast (${watch.error}). Retrying.`;
  } else if (!watch.lastCheckedAt) {
    watchTitle.textContent = `Watching this ${label}`;
    watchStatusEl.textContent = "Starting the watch…";
  } else {
    watchTitle.textContent = `Watching this ${label}`;
    const tail = watch.events.length
      ? `${watch.events.length} update${watch.events.length === 1 ? "" : "s"} so far`
      : "no material change";
    watchStatusEl.textContent = `Last checked ${clockTime(watch.lastCheckedAt)} · ${tail}`;
  }

  watchLog.innerHTML = "";
  watch.events.forEach((event) => {
    const item = document.createElement("li");
    if (event.code === "STATUS") item.dataset.severity = "status";
    item.innerHTML = `<strong>${event.label}</strong>${event.detail}`;
    const time = document.createElement("span");
    time.className = "watch-time";
    time.textContent = clockTime(event.at);
    item.append(time);
    watchLog.append(item);
  });
}

function startWatch(product, payload, requestBody, pourDateValue) {
  const watch = newWatch();
  watch.open = Boolean(payload.watching);
  watch.watchUntil = payload.watch_until || null;
  watch.nextCheckSeconds = payload.next_check_seconds || 180;
  watch.lastCheckedAt = new Date().toISOString();
  const checkId = payload.check_id || null;
  watch.request = { ...requestBody, check_id: checkId };
  watchByProduct[product] = watch;
  const id = checkId ? String(checkId) : `local-${Date.now()}`;
  selectedWatchId = id;
  upsertWatchEntry({
    id,
    product,
    location: payload.location?.name || requestBody.zip_code || "Unknown site",
    pourDateValue,
    request: watch.request,
    payload,
    watch,
  });
  if (product === currentProduct) {
    renderWatch(product);
    scheduleWatch();
  }
}

async function pollOneWatch(entry) {
  const watch = entry.watch;
  if (!watch.open || watch.paused || watch.polling || !watch.request) return;
  watch.polling = true;
  const body = { ...watch.request };
  if (!body.check_id && entry.payload) {
    body.baseline = {
      go_no_go_status: entry.payload.go_no_go_status,
      risk_factors: entry.payload.risk_factors,
      metrics: entry.payload.metrics,
    };
  }
  try {
    const response = await fetch("/v1/pour-watch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || response.statusText);
    }
    watch.error = null;
    watch.lastCheckedAt = payload.checked_at;
    if (watch.closedByUser) {
      watch.open = false;
    } else {
      watch.open = Boolean(payload.watching);
    }
    watch.nextCheckSeconds = payload.next_check_seconds || watch.nextCheckSeconds;
    watch.watchUntil = payload.watch_until || watch.watchUntil;
    if (payload.changed) {
      const at = payload.checked_at;
      watch.events = [...payload.changes.map((change) => ({ ...change, at })), ...watch.events].slice(0, 20);
      entry.payload = { ...payload, check_id: body.check_id };
      entry.unread = entry.id !== selectedWatchId;
      cacheResult(entry.product, entry.payload, entry.pourDateValue);
      if (entry.id === selectedWatchId) {
        paintResult(entry.payload, entry.pourDateValue);
        watchEl.classList.remove("flash");
        void watchEl.offsetWidth;
        watchEl.classList.add("flash");
        renderWatch(entry.product);
      }
    }
  } catch (error) {
    watch.error = error.message || "network error";
  } finally {
    watch.polling = false;
  }
}

async function pollAllWatches() {
  const open = watchRoster.filter((entry) => watchEntryState(entry) === "watching");
  for (const entry of open) {
    await pollOneWatch(entry);
  }
  persistWatchRoster();
  renderWatchList();
  const selected = watchRoster.find((entry) => entry.id === selectedWatchId);
  if (selected && selected.product === currentProduct) {
    renderWatch(selected.product);
  }
  scheduleWatch();
}

async function closeWatch(id) {
  const entry = watchRoster.find((item) => item.id === id);
  if (!entry || !entry.watch.open) return;
  entry.watch.open = false;
  entry.watch.paused = false;
  entry.watch.closedByUser = true;
  persistWatchRoster();
  const checkId = entry.watch.request?.check_id || entry.payload?.check_id || entry.id;
  if (isPersistedCheckId(checkId)) {
    try {
      await fetch(`/v1/pour-watch/${checkId}/close`, { method: "POST" });
    } catch {
      /* local close still stands */
    }
  }
  if (entry.id === selectedWatchId) {
    renderWatch(entry.product);
  }
  watchListFilter = "closed";
  persistWatchRoster();
  renderWatchList();
  scheduleWatch();
}

watchToggle.addEventListener("click", () => {
  const entry = watchRoster.find((item) => item.id === selectedWatchId);
  const watch = entry?.watch || watchByProduct[currentProduct];
  if (!watch.open) return;
  watch.paused = !watch.paused;
  persistWatchRoster();
  renderWatch(currentProduct);
  renderWatchList();
  if (watch.paused) {
    stopWatchTimer();
    scheduleWatch();
  } else {
    pollAllWatches();
  }
});

watchClose.addEventListener("click", () => {
  if (selectedWatchId) closeWatch(selectedWatchId);
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopWatchTimer();
  } else {
    scheduleWatch();
  }
});

async function checkPour(event) {
  event.preventDefault();
  const product = currentProduct;
  const copy = COPY[product];
  showError("");
  submitBtn.disabled = true;
  submitBtn.textContent = copy.checking;
  inFlightProduct = product;

  const zip = zipInput.value.trim();
  const pourDate = pourDateInput.value;
  const body = {
    product,
    zip_code: zip,
    pour_date: `${pourDate}:00`,
  };
  if (product === "masonry") {
    body.masonry_design = {
      unit_type: document.querySelector("#unit-type").value,
      mortar_type: document.querySelector("#mortar-type").value,
    };
    const mortarTemp = document.querySelector("#mortar-temp").value;
    if (mortarTemp) body.concrete_temp_f = Number(mortarTemp);
  } else {
    body.mix_design = {
      cement_type: document.querySelector("#cement").value,
      target_psi: Number(document.querySelector("#psi").value),
      thickness_inches: Number(document.querySelector("#thickness").value),
    };
    const concreteTemp = document.querySelector("#concrete-temp").value;
    if (concreteTemp) body.concrete_temp_f = Number(concreteTemp);
  }

  try {
    const response = await fetch("/v1/pour-readiness", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = payload.detail;
      const message = Array.isArray(detail) ? detail.map((item) => item.msg).join(" ") : detail || response.statusText;
      throw new Error(message);
    }
    cacheResult(product, payload, pourDate);
    if (currentProduct === product) {
      paintResult(payload, pourDate);
    }
    startWatch(product, payload, body, pourDate);
  } catch (error) {
    if (currentProduct === product) {
      showError(error.message || copy.error);
    }
  } finally {
    if (inFlightProduct === product) {
      inFlightProduct = null;
    }
    if (currentProduct === product) {
      submitBtn.disabled = false;
      submitBtn.textContent = COPY[currentProduct].submit;
    }
  }
}

presetButtons.forEach((button) => {
  button.addEventListener("click", () => {
    zipInput.value = button.dataset.zip;
    presetButtons.forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

pourDateInput.value = defaultPourTime();
document.querySelector('#site-presets button[data-zip="94612"]').classList.add("active");
form.addEventListener("submit", checkPour);

document.querySelectorAll(".product-switch button").forEach((button) => {
  button.addEventListener("click", () => {
    selectProduct(button.dataset.product);
  });
});

document.querySelectorAll("#watch-list-filter button").forEach((button) => {
  button.addEventListener("click", () => {
    watchListFilter = button.dataset.watchFilter;
    renderWatchList();
  });
});

watchListBody.addEventListener("click", (event) => {
  const closeBtn = event.target.closest("[data-close-id]");
  if (closeBtn) {
    event.preventDefault();
    closeWatch(closeBtn.dataset.closeId);
    return;
  }
  const button = event.target.closest("[data-watch-id]");
  if (!button) return;
  openWatch(button.dataset.watchId);
});

document.querySelectorAll("#outcome button").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!currentCheckId) return;
    outcomeEl.querySelectorAll("button").forEach((item) => {
      item.disabled = true;
    });
    try {
      const response = await fetch("/v1/pour-outcomes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ check_id: currentCheckId, outcome: button.dataset.outcome }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || response.statusText);
      }
      outcomeStatus.hidden = false;
      outcomeStatus.textContent = "Recorded. That helps validate the model.";
    } catch (error) {
      outcomeEl.querySelectorAll("button").forEach((item) => {
        item.disabled = false;
      });
      outcomeStatus.hidden = false;
      outcomeStatus.textContent = error.message || "Could not save that outcome.";
    }
  });
});

loadWatchRoster();
renderWatchList();
selectProduct("concrete");
