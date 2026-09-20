const form = document.querySelector("#pour-form");
const zipInput = document.querySelector("#zip");
const emailInput = document.querySelector("#notify-email");
const pourDateInput = document.querySelector("#pour-date");
const submitBtn = document.querySelector("#submit-btn");
const formError = document.querySelector("#form-error");
const emptyState = document.querySelector("#empty-state");
const resultBody = document.querySelector("#result-body");
const stamp = document.querySelector("#stamp");
const stampStatus = document.querySelector("#stamp-status");
const resultKicker = document.querySelector("#result-kicker");
const resultLocation = document.querySelector("#result-location");
const resultWhen = document.querySelector("#result-when");
const metricsEl = document.querySelector("#metrics");
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
    submit: "Check this pour",
    emptyKicker: "Waiting on a concrete ticket",
    dateLabel: "Pour date & time",
    chartTitle: "Pour window · 48 hours",
    checking: "Checking forecast…",
    error: "Could not check this pour.",
  },
  masonry: {
    heading: "Masonry ticket",
    sub: "Jobsite local time. Defaults are a typical CMU / Type N lay-up.",
    submit: "Check this lay-up",
    emptyKicker: "Waiting on a masonry ticket",
    dateLabel: "Lay-up date & time",
    chartTitle: "Lay-up window · 48 hours",
    checking: "Checking forecast…",
    error: "Could not check this lay-up.",
  },
};

let chart;
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
const EMAIL_STORE_KEY = "pi_notify_email";
const WATCH_STORE_MAX = 20;
let watchRoster = [];
let selectedWatchId = null;
let watchListFilter = "watching";
let watchTimer = null;
let currentPane = "ticket";

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

function loadNotifyEmail() {
  if (!emailInput) return;
  try {
    const saved = localStorage.getItem(EMAIL_STORE_KEY);
    if (saved) emailInput.value = saved;
  } catch {
    /* private mode */
  }
}

function persistNotifyEmail() {
  if (!emailInput) return;
  try {
    const value = emailInput.value.trim();
    if (value) localStorage.setItem(EMAIL_STORE_KEY, value);
    else localStorage.removeItem(EMAIL_STORE_KEY);
  } catch {
    /* private mode */
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
      watchRoster.length === 0 ? "No watches yet." : `No ${watchListFilter} watches.`;
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

function setPane(pane) {
  if (!pane) return;
  currentPane = pane;
  document.querySelectorAll(".pane").forEach((el) => {
    el.hidden = el.dataset.pane !== pane;
  });
  document.querySelectorAll(".side-nav [data-pane]").forEach((button) => {
    const active = button.dataset.pane === pane;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (pane === "ticket" && chart) {
    requestAnimationFrame(() => chart.resize());
  }
  window.scrollTo(0, 0);
}

function renderEventsPane() {
  const eventsEmpty = document.querySelector("#events-empty");
  const eventsLog = document.querySelector("#watch-log");
  if (!eventsEmpty || !eventsLog) return;
  const entry = watchRoster.find((item) => item.id === selectedWatchId);
  if (!entry) {
    eventsEmpty.hidden = false;
    eventsLog.hidden = true;
    eventsLog.innerHTML = "";
    return;
  }
  eventsEmpty.hidden = true;
  eventsLog.hidden = false;
  renderEventLog(entry.watch?.events || [], entry.location, entry.product);
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
  setPane("ticket");
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

const DEFAULT_ZIP = "94612";

function resetComposer(product) {
  zipInput.value = "";
  pourDateInput.value = "";
  if (emailInput) emailInput.value = "";
  presetButtons.forEach((item) => item.classList.remove("active"));
  if (product === "masonry") {
    document.querySelector("#unit-type").value = "";
    document.querySelector("#mortar-type").value = "";
    document.querySelector("#mortar-temp").value = "";
  } else {
    document.querySelector("#cement").value = "";
    document.querySelector("#psi").value = "";
    document.querySelector("#thickness").value = "";
    document.querySelector("#concrete-temp").value = "";
  }
}

function looksLikeDump(text) {
  if (typeof text !== "string") return true;
  const trimmed = text.trim();
  if (!trimmed) return true;
  if (/https?:\/\//i.test(trimmed)) return true;
  if (/open-meteo/i.test(trimmed)) return true;
  if (/mozilla/i.test(trimmed)) return true;
  if (/client error/i.test(trimmed)) return true;
  if (/traceback/i.test(trimmed)) return true;
  if (trimmed.length > 180) return true;
  return false;
}

function publicErrorText(payload, fallback) {
  const pieces = [];
  if (payload && typeof payload === "object") {
    if (typeof payload.message === "string") pieces.push(payload.message);
    const detail = payload.detail;
    if (typeof detail === "string") pieces.push(detail);
    if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.message === "string") {
      pieces.push(detail.message);
    }
    if (Array.isArray(detail)) {
      pieces.push(detail.map((item) => item && item.msg).filter(Boolean).join(" "));
    }
  }
  for (const piece of pieces) {
    if (!looksLikeDump(piece)) return piece.trim();
  }
  return fallback;
}

function showError(message) {
  const text = typeof message === "string" && !looksLikeDump(message) ? message.trim() : "";
  const show = Boolean(message);
  formError.hidden = !show;
  formError.textContent = show ? text || COPY[currentProduct].error : "";
}

function showWatchLimit(hit) {
  const note = document.querySelector("#watch-limit-note");
  if (!note) return;
  note.hidden = !hit;
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
  stamp.dataset.status = "";
  stampStatus.textContent = "";
  resultKicker.textContent = "";
  resultLocation.textContent = "";
  resultWhen.textContent = "";
  metricsEl.innerHTML = "";
  watchEl.hidden = true;
  watchLog.innerHTML = "";
  destroyChart();
}

function showEmptyState() {
  const copy = COPY[currentProduct];
  document.querySelector("#empty-kicker").textContent = copy.emptyKicker;
  emptyState.hidden = false;
  resultBody.hidden = true;
  clearResultPanel();
  renderEventsPane();
}

function applyProductChrome(product) {
  const copy = COPY[product];
  const masonry = product === "masonry";
  document.querySelectorAll(".product-switch button").forEach((button) => {
    button.classList.toggle("active", button.dataset.product === product);
  });
  document.querySelector("#fields-concrete").hidden = masonry;
  document.querySelector("#fields-concrete").disabled = masonry;
  document.querySelector("#fields-masonry").hidden = !masonry;
  document.querySelector("#fields-masonry").disabled = !masonry;
  document.querySelector("#ticket-heading").textContent = copy.heading;
  document.querySelector("#ticket-sub").textContent = copy.sub;
  document.querySelector("#date-label").textContent = copy.dateLabel;
  document.querySelector("#chart-title").textContent = copy.chartTitle;
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

  const sans = "Satoshi, system-ui, sans-serif";
  const chartInk = "#52525b";
  const tick = { color: chartInk, font: { family: sans, size: 14, weight: "400" } };
  const blue = "#4d74f7";
  chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Air °F",
          data: temps,
          borderColor: blue,
          backgroundColor: "transparent",
          tension: 0.25,
          borderWidth: 2,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "RH %",
          data: humidity,
          borderColor: "rgba(77, 116, 247, 0.45)",
          backgroundColor: "transparent",
          borderDash: [4, 4],
          tension: 0.25,
          borderWidth: 1.5,
          pointRadius: 0,
          yAxisID: "y",
        },
        {
          label: "Evap lb/ft²/hr",
          data: evaporation,
          borderColor: "rgba(77, 116, 247, 0.7)",
          backgroundColor: "rgba(77, 116, 247, 0.08)",
          fill: true,
          tension: 0.25,
          borderWidth: 1.5,
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
          labels: {
            color: chartInk,
            boxWidth: 8,
            boxHeight: 8,
            padding: 16,
            font: { family: sans, size: 14, weight: "400" },
          },
        },
      },
      scales: {
        x: {
          ticks: { ...tick, maxRotation: 0, autoSkip: true, maxTicksLimit: window.innerWidth < 700 ? 5 : 10 },
          grid: { color: "#eef0f3" },
          border: { display: false },
        },
        y: {
          title: { display: true, text: "°F / % RH", color: chartInk, font: tick.font },
          ticks: tick,
          grid: { color: "#eef0f3" },
          border: { display: false },
        },
        yEvap: {
          position: "right",
          title: { display: true, text: "lb/ft²/hr", color: chartInk, font: tick.font },
          ticks: tick,
          grid: { drawOnChartArea: false },
          border: { display: false },
          suggestedMin: 0,
        },
      },
    },
  });
}

function paintResult(payload, pourDateValue) {
  emptyState.hidden = true;
  resultBody.hidden = false;

  const product = payload.product === "masonry" ? "masonry" : "concrete";
  const status = payload.go_no_go_status;
  stamp.dataset.status = status;
  stampStatus.textContent = status.replace("_", "-");
  resultKicker.textContent = COPY[product].heading;
  resultLocation.textContent = payload.location.name;
  resultWhen.textContent = formatWhen(pourDateValue, payload.location.timezone);

  const metrics = payload.metrics;
  const predictions = payload.predictions || {};
  const materialLabel = product === "masonry" ? "Mortar temp" : "Concrete temp";
  const rainLabel = product === "masonry" ? "Rain at lay-up" : "Rain at pour";
  metricsEl.innerHTML = "";
  const items = [
    metric("Air temp", `${metrics.ambient_temp_f} °F`),
    metric(materialLabel, `${metrics.concrete_temp_f} °F`),
    metric("RH", `${metrics.relative_humidity_pct}%`),
    metric("Wind", `${metrics.wind_speed_mph} mph`),
    metric("Evaporation", `${metrics.calculated_evaporation_rate_lbs_sqft_hr} lb/ft²/hr`),
    metric(rainLabel, `${metrics.precipitation_in} in`),
    metric("Min 24h", metrics.min_temp_next_24h_f == null ? "—" : `${metrics.min_temp_next_24h_f} °F`),
    metric("Min 48h", metrics.min_temp_next_48h_f == null ? "—" : `${metrics.min_temp_next_48h_f} °F`),
  ];
  if (product === "masonry") {
    items.push(
      metric(
        "Protection period",
        predictions.protection_period_hours == null ? "—" : `${predictions.protection_period_hours} hr`
      )
    );
  } else {
    items.push(
      metric(
        "500 psi",
        predictions.estimated_time_to_500_psi_hours == null
          ? "—"
          : `~${predictions.estimated_time_to_500_psi_hours} hr`
      ),
      metric(
        "70% strength",
        predictions.estimated_days_to_70_percent_strength == null
          ? "—"
          : `~${predictions.estimated_days_to_70_percent_strength} days`
      )
    );
  }
  metricsEl.append(...items);
  renderChart(payload.hourly || []);
}

function cacheResult(product, payload, pourDateValue) {
  lastByProduct[product] = { payload, pourDateValue };
}

function clockTime(value) {
  const when = value ? new Date(value) : new Date();
  if (Number.isNaN(when.getTime())) return "—";
  return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
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

function renderEventLog(events, location, product) {
  const productLabel = product === "masonry" ? "Masonry" : "Concrete";
  const secondary = [location, productLabel].filter(Boolean).join(" · ");
  watchLog.innerHTML = "";
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "event-empty";
    item.textContent = "No events yet.";
    watchLog.append(item);
    return;
  }
  events.forEach((event) => {
    const severity = eventSeverity(event);
    const item = document.createElement("li");
    item.dataset.severity = severity;
    const sev = document.createElement("span");
    sev.className = "event-sev";
    sev.textContent = severity === "critical" ? "Critical" : severity === "warning" ? "Warning" : "Info";
    const time = document.createElement("span");
    time.className = "event-time";
    time.textContent = clockTime(event.at);
    const body = document.createElement("span");
    body.className = "event-body";
    const fact = document.createElement("span");
    fact.className = "event-fact";
    fact.textContent = eventFact(event);
    body.append(fact);
    if (secondary) {
      const sub = document.createElement("span");
      sub.className = "event-sub";
      sub.textContent = secondary;
      body.append(sub);
    }
    item.append(sev, time, body);
    watchLog.append(item);
  });
}

function pushWatchEvent(watch, event) {
  const next = { at: new Date().toISOString(), ...event };
  watch.events = [next, ...watch.events].slice(0, 20);
}

function seedWatchEvents(payload) {
  const at = new Date().toISOString();
  const status = payload.go_no_go_status || "";
  const details = payload.risk_factor_details || [];
  const events = [];
  details.forEach((factor) => {
    events.push({ code: factor.code, label: factor.label || factor.code, at, kind: "risk" });
  });
  events.push({
    code: "STATUS",
    label: "Stamp",
    current: status,
    previous: "",
    at,
  });
  events.push({ code: "WATCH_STARTED", label: "Watch started", at });
  return events.slice(0, 20);
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
  if (!watch.request) {
    watchEl.hidden = true;
    renderEventsPane();
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
    watchTitle.textContent = "Closed";
    watchStatusEl.textContent = `Last checked ${clockTime(watch.lastCheckedAt)}`;
  } else if (watch.paused) {
    watchTitle.textContent = "Paused";
    watchStatusEl.textContent = `Last checked ${clockTime(watch.lastCheckedAt)}`;
  } else if (watch.error) {
    watchTitle.textContent = "Watching";
    watchStatusEl.textContent = `Retrying (${watch.error})`;
  } else if (!watch.lastCheckedAt) {
    watchTitle.textContent = "Watching";
    watchStatusEl.textContent = "Starting…";
  } else {
    watchTitle.textContent = "Watching";
    watchStatusEl.textContent = `Last checked ${clockTime(watch.lastCheckedAt)}`;
  }

  renderEventsPane();
}

function startWatch(product, payload, requestBody, pourDateValue) {
  const watch = newWatch();
  watch.open = Boolean(payload.watching);
  watch.watchUntil = payload.watch_until || null;
  watch.nextCheckSeconds = payload.next_check_seconds || 180;
  watch.lastCheckedAt = new Date().toISOString();
  const checkId = payload.check_id || null;
  watch.request = { ...requestBody, check_id: checkId };
  watch.events = seedWatchEvents(payload);
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
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error("Could not refresh this watch.");
    }
    if (!response.ok) {
      throw new Error(publicErrorText(payload, "Could not refresh this watch."));
    }
    watch.error = null;
    watch.lastCheckedAt = payload.checked_at;
    const wasOpen = watch.open && !watch.closedByUser;
    if (watch.closedByUser) {
      watch.open = false;
    } else {
      watch.open = Boolean(payload.watching);
    }
    watch.nextCheckSeconds = payload.next_check_seconds || watch.nextCheckSeconds;
    watch.watchUntil = payload.watch_until || watch.watchUntil;
    if (wasOpen && !watch.open && !watch.closedByUser) {
      pushWatchEvent(watch, { code: "WATCH_CLOSED", label: "Watch closed", at: payload.checked_at });
    }
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
    const raw = error && error.message;
    watch.error = looksLikeDump(raw) ? "briefly unavailable" : raw;
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
  pushWatchEvent(entry.watch, { code: "WATCH_CLOSED", label: "Watch closed" });
  entry.watch.open = false;
  entry.watch.paused = false;
  entry.watch.closedByUser = true;
  persistWatchRoster();
  const checkId = entry.watch.request?.check_id || entry.payload?.check_id || entry.id;
  if (isPersistedCheckId(checkId)) {
    try {
      await fetch(`/v1/pour-watch/${checkId}/close`, { method: "POST", credentials: "include" });
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
  pushWatchEvent(watch, {
    code: watch.paused ? "WATCH_PAUSED" : "WATCH_RESUMED",
    label: watch.paused ? "Watch paused" : "Watch resumed",
  });
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
  persistNotifyEmail();
  const body = {
    product,
    zip_code: zip,
    pour_date: `${pourDate}:00`,
  };
  const email = (emailInput?.value || "").trim();
  if (email) body.email = email;
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
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(copy.error);
    }
    if (!response.ok) {
      throw new Error(publicErrorText(payload, copy.error));
    }
    cacheResult(product, payload, pourDate);
    if (currentProduct === product) {
      setPane("ticket");
      paintResult(payload, pourDate);
    }
    startWatch(product, payload, body, pourDate);
    showWatchLimit(Boolean(payload.watch_limit_reached));
    if (currentProduct === product) {
      resetComposer(product);
    }
  } catch (error) {
    if (currentProduct === product) {
      showError(looksLikeDump(error.message) ? copy.error : error.message || copy.error);
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
document.querySelector(`#site-presets button[data-zip="${DEFAULT_ZIP}"]`).classList.add("active");
form.addEventListener("submit", checkPour);

document.querySelectorAll(".side-nav [data-pane]").forEach((button) => {
  button.addEventListener("click", () => {
    setPane(button.dataset.pane);
  });
});

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

async function requireAccount() {
  try {
    const response = await fetch("/v1/auth/me", { credentials: "include" });
    if (response.status === 401) {
      window.location.replace("/login");
      return null;
    }
    if (!response.ok) return {};
    return await response.json();
  } catch {
    return {};
  }
}

function wireAccountBar(me) {
  const emailEl = document.querySelector("#account-email");
  if (emailEl && me && me.email) emailEl.textContent = me.email;
  const signOut = document.querySelector("#sign-out");
  if (signOut) {
    signOut.addEventListener("click", async () => {
      await fetch("/v1/auth/logout", { method: "POST", credentials: "include" });
      window.location.replace("/login");
    });
  }
}

requireAccount().then((me) => {
  if (!me) return;
  wireAccountBar(me);
  loadWatchRoster();
  loadNotifyEmail();
  renderWatchList();
  selectProduct("concrete");
});
