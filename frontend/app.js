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
const presetButtons = document.querySelectorAll(".presets button");

const NOGO_CODES = new Set([
  "EXTREME_EVAPORATION_RATE",
  "FREEZING_BEFORE_500_PSI",
  "HEAVY_RAIN_DURING_POUR",
  "MASONRY_BELOW_20F",
]);

const COPY = {
  concrete: {
    heading: "Pour ticket",
    sub: "Jobsite local time. Defaults are a typical Type I slab.",
    submit: "Check this pour",
    emptyKicker: "Waiting on a concrete ticket",
    emptyBody:
      "Fill the concrete slab ticket and run a check. You’ll get an ACI 305R / 306R stamp, risk factors, a 48-hour forecast chart, and a mitigation note.",
    dateLabel: "Pour date & time",
    outcomeKicker: "How did this pour go?",
    chartTitle: "Pour window · 48 hours",
    checking: "Checking forecast…",
    error: "Could not check this pour.",
  },
  masonry: {
    heading: "Masonry ticket",
    sub: "Jobsite local time. TMS 602 / ACI 530.1 hot- and cold-weather masonry.",
    submit: "Check this lay-up",
    emptyKicker: "Waiting on a masonry ticket",
    emptyBody:
      "Fill the masonry ticket and run a check. You’ll get a TMS 602 stamp, risk factors, a 48-hour forecast chart, and a protection note.",
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
  currentProduct = product;
  applyProductChrome(product);
  showError("");
  const cached = lastByProduct[product];
  if (cached) {
    paintResult(cached.payload, cached.pourDateValue);
  } else {
    showEmptyState();
  }
}

function renderChart(hourly) {
  const canvas = document.querySelector("#forecast-chart");
  const labels = hourly.map((point) => point.time.slice(11, 16));
  const temps = hourly.map((point) => point.temp_f);
  const humidity = hourly.map((point) => point.relative_humidity_pct);
  const evaporation = hourly.map((point) => point.evaporation_rate_lbs_sqft_hr);

  destroyChart();

  chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Air °F",
          data: temps,
          borderColor: "#1c1812",
          backgroundColor: "transparent",
          tension: 0.25,
          yAxisID: "y",
        },
        {
          label: "RH %",
          data: humidity,
          borderColor: "#6f8f7a",
          backgroundColor: "transparent",
          borderDash: [4, 4],
          tension: 0.25,
          yAxisID: "y",
        },
        {
          label: "Evap lb/ft²/hr",
          data: evaporation,
          borderColor: "#b57914",
          backgroundColor: "rgba(181, 121, 20, 0.12)",
          fill: true,
          tension: 0.25,
          yAxisID: "yEvap",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { font: { family: "IBM Plex Mono", size: 11 } } },
      },
      scales: {
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
        y: {
          title: { display: true, text: "°F / % RH" },
          grid: { color: "rgba(28,24,18,0.08)" },
        },
        yEvap: {
          position: "right",
          title: { display: true, text: "lb/ft²/hr" },
          grid: { drawOnChartArea: false },
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
document.querySelector('.presets button[data-zip="94612"]').classList.add("active");
form.addEventListener("submit", checkPour);

document.querySelectorAll(".product-switch button").forEach((button) => {
  button.addEventListener("click", () => {
    selectProduct(button.dataset.product);
  });
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

selectProduct("concrete");
