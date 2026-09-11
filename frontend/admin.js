const summaryEl = document.querySelector("#summary");
const checksBody = document.querySelector("#checks-body");
const keysBody = document.querySelector("#keys-body");
const newKeyEl = document.querySelector("#new-key");
const keyForm = document.querySelector("#key-form");

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
let productFilter = "all";

function filteredChecks() {
  if (productFilter === "all") return allChecks;
  return allChecks.filter((row) => (row.product || "concrete") === productFilter);
}

async function loadChecks() {
  const payload = await api("/admin/api/checks");
  allChecks = payload.checks;
  const summary = payload.summary;
  const byProduct = summary.by_product || {};
  summaryEl.innerHTML = "";
  summaryEl.append(
    metric("Checks", summary.total_checks),
    metric("Concrete", byProduct.concrete || 0),
    metric("Masonry", byProduct.masonry || 0),
    metric("With outcome", summary.with_outcome),
    metric("Pending", summary.pending_outcome),
    metric("GO + success", summary.go_and_success),
    metric("GO + cracked/delayed", summary.go_and_failed)
  );
  renderCheckRows();
}

function renderCheckRows() {
  const rows = filteredChecks();
  checksBody.innerHTML = "";
  if (!rows.length) {
    checksBody.innerHTML = '<tr><td colspan="6">No checks stored yet.</td></tr>';
    return;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const outcome = row.outcome || "pending";
    const product = row.product || "concrete";
    tr.innerHTML = `<td>${fmt(row.created_at)}</td>
      <td>${product}</td>
      <td>${row.location_name || row.zip_code || "—"}</td>
      <td><span class="pill" data-status="${row.go_no_go_status}">${row.go_no_go_status}</span></td>
      <td><span class="pill" data-outcome="${outcome}">${outcome}</span></td>
      <td>${row.source || "—"}</td>`;
    checksBody.append(tr);
  });
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
    const revoke = row.active
      ? `<button type="button" data-revoke="${row.id}">Revoke</button>`
      : "";
    tr.innerHTML = `<td>${row.name}</td>
      <td><code>${row.key_prefix}…</code></td>
      <td>${row.rate_limit_per_hour}/hr</td>
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

loadChecks().catch((error) => {
  checksBody.innerHTML = `<tr><td colspan="6">${error.message}</td></tr>`;
});
loadKeys().catch(() => {});

document.querySelectorAll("#product-filter button").forEach((button) => {
  button.addEventListener("click", () => {
    productFilter = button.dataset.filter;
    document.querySelectorAll("#product-filter button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderCheckRows();
  });
});
