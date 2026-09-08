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
const presetButtons = document.querySelectorAll(".presets button");

const NOGO_CODES = new Set([
  "EXTREME_EVAPORATION_RATE",
  "FREEZING_BEFORE_500_PSI",
  "HEAVY_RAIN_DURING_POUR",
]);

let chart;

function pad(value) {
  return String(value).padStart(2, "0");
}

function defaultPourTime() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(8, 0, 0, 0);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
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

function renderChart(hourly) {
  const canvas = document.querySelector("#forecast-chart");
  const labels = hourly.map((point) => point.time.slice(11, 16));
  const temps = hourly.map((point) => point.temp_f);
  const humidity = hourly.map((point) => point.relative_humidity_pct);
  const evaporation = hourly.map((point) => point.evaporation_rate_lbs_sqft_hr);

  if (chart) {
    chart.destroy();
  }

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

function renderResult(payload, pourDateValue) {
  emptyState.hidden = true;
  resultBody.hidden = false;

  const status = payload.go_no_go_status;
  stamp.dataset.status = status;
  stampStatus.textContent = status.replace("_", "-");
  resultLocation.textContent = payload.location.name;
  resultWhen.textContent = formatWhen(pourDateValue, payload.location.timezone);

  riskList.innerHTML = "";
  if (!payload.risk_factor_details.length) {
    const item = document.createElement("li");
    item.innerHTML = "<strong>No risk factors triggered</strong>Forecast conditions sit inside normal placement ranges.";
    riskList.append(item);
  } else {
    payload.risk_factor_details.forEach((factor) => {
      const item = document.createElement("li");
      if (NOGO_CODES.has(factor.code)) item.dataset.severity = "nogo";
      item.innerHTML = `<strong>${factor.label}</strong>${factor.detail}`;
      riskList.append(item);
    });
  }

  const metrics = payload.metrics;
  metricsEl.innerHTML = "";
  metricsEl.append(
    metric("Air temp", `${metrics.ambient_temp_f} °F`),
    metric("Concrete temp", `${metrics.concrete_temp_f} °F`),
    metric("Relative humidity", `${metrics.relative_humidity_pct}%`),
    metric("Wind", `${metrics.wind_speed_mph} mph`),
    metric("Evaporation", `${metrics.calculated_evaporation_rate_lbs_sqft_hr} lb/ft²/hr`),
    metric("Rain at pour", `${metrics.precipitation_in} in`),
    metric("Min next 24h", metrics.min_temp_next_24h_f == null ? "—" : `${metrics.min_temp_next_24h_f} °F`),
    metric("Min next 48h", metrics.min_temp_next_48h_f == null ? "—" : `${metrics.min_temp_next_48h_f} °F`)
  );

  const predictions = payload.predictions;
  const timeTo500 =
    predictions.estimated_time_to_500_psi_hours == null
      ? "500 psi: not reached in forecast"
      : `500 psi: ~${predictions.estimated_time_to_500_psi_hours} hr`;
  const timeTo70 =
    predictions.estimated_days_to_70_percent_strength == null
      ? "70% strength: not reached in forecast"
      : `70% strength: ~${predictions.estimated_days_to_70_percent_strength} days`;
  predictionsEl.innerHTML = `<span>${timeTo500}</span><span>${timeTo70}</span>`;

  mitigationEl.textContent = payload.recommended_mitigation;
  disclaimerEl.textContent = payload.disclaimer;
  renderChart(payload.hourly);
}

async function checkPour(event) {
  event.preventDefault();
  showError("");
  submitBtn.disabled = true;
  submitBtn.textContent = "Checking forecast…";

  const zip = zipInput.value.trim();
  const pourDate = pourDateInput.value;
  const concreteTemp = document.querySelector("#concrete-temp").value;

  const body = {
    zip_code: zip,
    pour_date: `${pourDate}:00`,
    mix_design: {
      cement_type: document.querySelector("#cement").value,
      target_psi: Number(document.querySelector("#psi").value),
      thickness_inches: Number(document.querySelector("#thickness").value),
    },
  };
  if (concreteTemp) {
    body.concrete_temp_f = Number(concreteTemp);
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
    renderResult(payload, pourDate);
  } catch (error) {
    showError(error.message || "Could not check this pour.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Check this pour";
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
