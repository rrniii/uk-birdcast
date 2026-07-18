const BOUNDS = {west: -12.5, east: 3.5, south: 48.5, north: 61.5};

const state = {
  base: "../",
  manifest: null,
  boundary: null,
  yearPayload: null,
  date: null,
  pulse: "lp",
  period: "night",
  metric: "vid",
  visibleRows: [],
  points: [],
};

(async function initialise() {
  const config = await fetchJson("config.json", {data_base_url: "../"});
  state.base = (config.data_base_url || "../").replace(/\/$/, "");
  state.manifest = await fetchJson(`${state.base}/latest/historical.json`, null);
  if (!state.manifest || !state.manifest.data_available) {
    showUnavailable();
    return;
  }
  state.date = state.manifest.latest_date;
  state.pulse = state.manifest.default_pulse || "lp";
  const assets = state.manifest.assets || {};
  [state.boundary] = await Promise.all([
    fetchJson(assetUrl(assets.boundary), null),
    loadYear(Number(state.date.slice(0, 4))),
  ]);
  configureControls();
  renderStatus();
  renderPlots(assets.plots || []);
  renderSelection();
  window.addEventListener("resize", drawMap);
  document.getElementById("mapCanvas").addEventListener("click", selectRadarAtPoint);
})();

async function fetchJson(url, fallback) {
  try {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } catch (error) {
    return fallback;
  }
}

function assetUrl(path) {
  if (!path) return "";
  return path.startsWith("http") ? path : `${state.base}/${path.replace(/^\//, "")}`;
}

async function loadYear(year) {
  const path = state.manifest.assets.daily_by_year[String(year)];
  state.yearPayload = path ? await fetchJson(assetUrl(path), {year, rows: []}) : {year, rows: []};
}

function configureControls() {
  const dateInput = document.getElementById("dateInput");
  dateInput.min = state.manifest.first_date;
  dateInput.max = state.manifest.latest_date;
  dateInput.value = state.date;
  dateInput.addEventListener("change", async () => {
    const next = dateInput.value;
    if (!next) return;
    if (!state.yearPayload || state.yearPayload.year !== Number(next.slice(0, 4))) {
      await loadYear(Number(next.slice(0, 4)));
    }
    state.date = next;
    renderSelection();
  });
  bindSegment("pulseControl", "pulse");
  bindSegment("periodControl", "period");
  const metric = document.getElementById("metricSelect");
  metric.value = state.metric;
  metric.addEventListener("change", () => {
    state.metric = metric.value;
    renderSelection();
  });
}

function bindSegment(id, key) {
  const root = document.getElementById(id);
  root.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === state[key]);
    button.addEventListener("click", () => {
      state[key] = button.dataset.value;
      root.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
      renderSelection();
    });
  });
}

function renderSelection() {
  const rows = state.yearPayload && state.yearPayload.rows || [];
  state.visibleRows = rows.filter((row) =>
    row.date === state.date && row.pulse === state.pulse && row.period === state.period
  );
  const metric = metricDefinition();
  const values = state.visibleRows.map((row) => Number(row[state.metric])).filter(Number.isFinite);
  const mean = values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
  document.getElementById("mapSubtitle").textContent =
    `${formatDate(state.date)} · ${state.pulse.toUpperCase()} · ${formatPeriod(state.period)} · ${state.visibleRows.length} radars`;
  document.getElementById("legendUnit").textContent = metric.label;
  document.getElementById("networkValue").textContent = mean === null ? "No observations" : metric.format(mean);
  document.getElementById("networkUnit").textContent =
    mean === null ? "Choose another date or product" : `${metric.meanLabel} across reporting radars`;
  renderRadarList(metric);
  drawMap();
}

function metricDefinition() {
  const metrics = {
    vid: {
      label: "VID passage index (birds km-2)",
      meanLabel: "Mean VID",
      format: (value) => `${value.toFixed(1)} birds km⁻²`,
    },
    height_m: {
      label: "Mean flight height (m)",
      meanLabel: "Mean flight height",
      format: (value) => `${Math.round(value)} m`,
    },
    speed_ms: {
      label: "Mean ground speed (m s-1)",
      meanLabel: "Mean ground speed",
      format: (value) => `${value.toFixed(1)} m s⁻¹`,
    },
  };
  return metrics[state.metric];
}

function renderStatus() {
  const manifest = state.manifest;
  const badge = document.getElementById("statusBadge");
  badge.textContent = "Historical archive";
  badge.className = "badge ok";
  const rows = [
    ["Coverage", `${formatDate(manifest.first_date)} to ${formatDate(manifest.latest_date)}`],
    ["Radars", manifest.radars.length],
    ["VPTS files", formatInteger(manifest.source.files_seen)],
    ["Profiles", formatInteger(manifest.source.profiles_seen)],
    ["Failed files", formatInteger(manifest.source.failure_count)],
    ["Altitude", `${manifest.metric.altitude_min_m}–${manifest.metric.altitude_max_m} m`],
  ];
  document.getElementById("statusList").innerHTML = rows.map(([key, value]) =>
    `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`
  ).join("");
}

function renderRadarList(metric) {
  const bySlug = new Map(state.visibleRows.map((row) => [row.radar, row]));
  document.getElementById("radarList").innerHTML = state.manifest.radars.map((radar) => {
    const row = bySlug.get(radar.slug);
    const value = row && Number(row[state.metric]);
    return `<button type="button" data-radar="${escapeHtml(radar.slug)}">
      <span>${escapeHtml(radar.label)}</span>
      <strong>${Number.isFinite(value) ? escapeHtml(metric.format(value)) : "No data"}</strong>
    </button>`;
  }).join("");
  document.querySelectorAll("#radarList button").forEach((button) => button.addEventListener("click", () => {
    const point = state.points.find((item) => item.radar.slug === button.dataset.radar);
    if (point) highlightRadar(point);
  }));
}

function renderPlots(paths) {
  const labels = [
    ["Annual nocturnal passage", "Network change through time"],
    ["Activity by solar period", "Day, twilight, and night"],
    ["Nocturnal phenology", "Median migration timing"],
    ["Radar archive coverage", "Availability by site and year"],
  ];
  document.getElementById("plotGrid").innerHTML = paths.map((path, index) => `
    <figure>
      <figcaption><strong>${escapeHtml(labels[index][0])}</strong><span>${escapeHtml(labels[index][1])}</span></figcaption>
      <img src="${escapeHtml(assetUrl(path))}" alt="${escapeHtml(labels[index][0])}">
    </figure>
  `).join("");
}

function drawMap() {
  const canvas = document.getElementById("mapCanvas");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(3, window.devicePixelRatio || 1);
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.fillStyle = "#dce9e7";
  ctx.fillRect(0, 0, width, height);
  drawGraticule(ctx, width, height);
  drawBoundary(ctx, width, height);
  drawRadarValues(ctx, width, height);
}

function drawGraticule(ctx, width, height) {
  ctx.strokeStyle = "rgba(66, 101, 91, .14)";
  ctx.lineWidth = 1;
  for (let lon = -12; lon <= 3; lon += 3) {
    const top = project(lon, BOUNDS.north, width, height);
    const bottom = project(lon, BOUNDS.south, width, height);
    ctx.beginPath();
    ctx.moveTo(top.x, top.y);
    ctx.lineTo(bottom.x, bottom.y);
    ctx.stroke();
  }
  for (let lat = 50; lat <= 60; lat += 2) {
    const left = project(BOUNDS.west, lat, width, height);
    const right = project(BOUNDS.east, lat, width, height);
    ctx.beginPath();
    ctx.moveTo(left.x, left.y);
    ctx.lineTo(right.x, right.y);
    ctx.stroke();
  }
}

function drawBoundary(ctx, width, height) {
  if (!state.boundary) return;
  for (const feature of state.boundary.features || []) {
    const polygons = feature.geometry.type === "MultiPolygon"
      ? feature.geometry.coordinates
      : [feature.geometry.coordinates];
    ctx.beginPath();
    for (const polygon of polygons) {
      for (const ring of polygon) {
        ring.forEach(([lon, lat], index) => {
          const point = project(lon, lat, width, height);
          if (index === 0) ctx.moveTo(point.x, point.y);
          else ctx.lineTo(point.x, point.y);
        });
        ctx.closePath();
      }
    }
    const code = feature.properties.ADM0_A3;
    ctx.fillStyle = code === "GBR" ? "#f7f8f5" : "#eef2ed";
    ctx.fill("evenodd");
    ctx.strokeStyle = code === "GBR" ? "#253a32" : "#849188";
    ctx.lineWidth = code === "GBR" ? 1.25 : 0.8;
    ctx.stroke();
  }
}

function drawRadarValues(ctx, width, height) {
  const radarRows = new Map(state.visibleRows.map((row) => [row.radar, row]));
  const values = state.visibleRows.map((row) => Number(row[state.metric])).filter(Number.isFinite).sort((a, b) => a - b);
  const high = values.length ? values[Math.min(values.length - 1, Math.floor(values.length * 0.9))] : 1;
  state.points = [];
  for (const radar of state.manifest.radars) {
    const point = project(radar.longitude, radar.latitude, width, height);
    const row = radarRows.get(radar.slug);
    const value = row && Number(row[state.metric]);
    state.points.push({radar, row, value, x: point.x, y: point.y});
    if (!Number.isFinite(value)) {
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = "#89958e";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      continue;
    }
    const radius = 7 + Math.min(1, value / Math.max(high, 0.0001)) * 8;
    ctx.fillStyle = densityColor(value / Math.max(high, 0.0001));
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
}

function project(lon, lat, width, height) {
  const padding = Math.max(16, Math.min(width, height) * 0.045);
  return {
    x: padding + ((lon - BOUNDS.west) / (BOUNDS.east - BOUNDS.west)) * (width - padding * 2),
    y: padding + ((BOUNDS.north - lat) / (BOUNDS.north - BOUNDS.south)) * (height - padding * 2),
  };
}

function selectRadarAtPoint(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const nearest = state.points
    .map((point) => ({point, distance: Math.hypot(point.x - x, point.y - y)}))
    .sort((a, b) => a.distance - b.distance)[0];
  if (nearest && nearest.distance < 28) highlightRadar(nearest.point);
}

function highlightRadar(point) {
  const metric = metricDefinition();
  document.querySelectorAll("#radarList button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.radar === point.radar.slug);
  });
  document.getElementById("networkValue").textContent =
    Number.isFinite(point.value) ? metric.format(point.value) : "No observation";
  document.getElementById("networkUnit").textContent = point.radar.label;
}

function densityColor(value) {
  const bounded = Math.max(0, Math.min(1, value));
  const stops = [[66, 139, 116], [224, 185, 75], [198, 66, 53]];
  const scaled = bounded * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  const rgb = stops[index].map((channel, offset) =>
    Math.round(channel + (stops[index + 1][offset] - channel) * fraction)
  );
  return `rgb(${rgb.join(",")})`;
}

function showUnavailable() {
  const badge = document.getElementById("statusBadge");
  badge.textContent = "Historical data unavailable";
  badge.className = "badge waiting";
  document.getElementById("mapSubtitle").textContent = "Historical artifacts have not been published.";
}

function formatDate(value) {
  if (!value) return "Unknown";
  return new Date(`${value}T12:00:00Z`).toLocaleDateString([], {day: "numeric", month: "short", year: "numeric"});
}

function formatPeriod(value) {
  return value.replace("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatInteger(value) {
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "Unknown";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
