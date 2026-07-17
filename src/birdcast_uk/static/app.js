(async function () {
  const config = await fetchJson("config.json", {data_base_url: "../"});
  const base = (config.data_base_url || "../").replace(/\/$/, "");
  const status = await fetchJson(`${base}/latest/status.json`, null);
  const radars = await fetchJson(`${base}/latest/radars.json`, {radars: []});
  const observed = await fetchJson(`${base}/latest/latest_observed.geojson`, {features: []});
  const validation = await fetchJson(`${base}/latest/validation_status.json`, null);

  renderStatus(status);
  renderRadars(radars.radars || []);
  renderValidation(validation);
  renderMap(radars.radars || [], observed.features || []);
})();

async function fetchJson(url, fallback) {
  try {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } catch (error) {
    if (fallback !== null) return fallback;
    return {error: String(error), data_available: false};
  }
}

function renderStatus(status) {
  const badge = document.getElementById("statusBadge");
  const list = document.getElementById("statusList");
  const available = Boolean(status.data_available);
  badge.textContent = available ? "Observed data available" : "Waiting for VPTS";
  badge.className = available ? "badge ok" : "badge waiting";
  const rows = [
    ["Generated", status.generated_at_utc || "unknown"],
    ["Latest VPTS", status.latest_vpts_date || "not available"],
    ["Latest ERA5", status.latest_era5_date || "not available"],
    ["BTO validation", status.latest_bto_validation_date || "not available"],
    ["Version", status.processing_version || "unknown"],
  ];
  list.innerHTML = rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function renderRadars(radars) {
  const container = document.getElementById("radarList");
  container.innerHTML = radars.map((radar) => {
    const coord = radar.latitude === null || radar.longitude === null
      ? "coordinates pending"
      : `${Number(radar.latitude).toFixed(3)}, ${Number(radar.longitude).toFixed(3)}`;
    return `<div><strong>${escapeHtml(radar.label)}</strong><span>${escapeHtml(radar.slug)} ${escapeHtml(coord)}</span></div>`;
  }).join("");
}

function renderValidation(validation) {
  const target = document.getElementById("validationText");
  if (validation.error) {
    target.textContent = "Validation status is not published yet.";
    return;
  }
  target.textContent = validation.bto_data_available
    ? "BTO validation data are available for derived checks."
    : "BTO data request and validation products are pending.";
}

function renderMap(radars, features) {
  const canvas = document.getElementById("mapCanvas");
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#f5f7f3";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawUkFrame(ctx, canvas.width, canvas.height);
  drawFeatures(ctx, features, canvas.width, canvas.height);
  drawRadars(ctx, radars, canvas.width, canvas.height);
}

function drawUkFrame(ctx, width, height) {
  ctx.strokeStyle = "#6c756b";
  ctx.lineWidth = 2;
  ctx.strokeRect(80, 50, width - 160, height - 110);
  ctx.fillStyle = "#25302b";
  ctx.font = "15px system-ui, sans-serif";
  ctx.fillText("UK migration intensity layer", 96, 80);
  ctx.fillStyle = "#69736b";
  ctx.fillText("Real points appear when VPTS-derived products are published.", 96, 104);
}

function drawFeatures(ctx, features, width, height) {
  for (const feature of features) {
    if (!feature.geometry || feature.geometry.type !== "Point") continue;
    const [lon, lat] = feature.geometry.coordinates;
    const point = project(lon, lat, width, height);
    const traffic = Number(
      feature.properties && feature.properties.migration_traffic_birds_per_km
    ) || 0;
    ctx.fillStyle = colorForTraffic(traffic);
    ctx.beginPath();
    ctx.arc(point.x, point.y, 6 + Math.min(18, Math.log10(traffic + 1) * 4), 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawRadars(ctx, radars, width, height) {
  ctx.font = "12px system-ui, sans-serif";
  for (const radar of radars) {
    if (radar.latitude === null || radar.longitude === null) continue;
    const point = project(Number(radar.longitude), Number(radar.latitude), width, height);
    ctx.fillStyle = "#1f4d5a";
    ctx.beginPath();
    ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText(radar.label, point.x + 7, point.y + 4);
  }
}

function project(lon, lat, width, height) {
  const west = -11.5;
  const east = 3.0;
  const south = 49.0;
  const north = 61.5;
  return {
    x: 80 + ((lon - west) / (east - west)) * (width - 160),
    y: 50 + ((north - lat) / (north - south)) * (height - 110),
  };
}

function colorForTraffic(traffic) {
  if (traffic > 10000) return "rgba(181, 44, 34, 0.72)";
  if (traffic > 3000) return "rgba(216, 121, 43, 0.68)";
  if (traffic > 500) return "rgba(69, 126, 91, 0.62)";
  return "rgba(76, 111, 150, 0.48)";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}
