const BOUNDS = {west: -12.5, east: 3.5, south: 48.5, north: 61.5};

const state = {
  base: "../",
  historical: null,
  model: null,
  boundary: null,
  yearPayload: null,
  modelDayPayload: null,
  view: "observed",
  date: null,
  hour: 0,
  pulse: "lp",
  metric: "vid",
  modelMetric: "mtr_birds_km_h",
  showArrows: true,
  showUncertainty: false,
  visibleRows: [],
  modelFrame: null,
  points: [],
  animation: null,
};

(async function initialise() {
  const config = await fetchJson("config.json", {data_base_url: "../"});
  state.base = (config.data_base_url || "../").replace(/\/$/, "");
  const [historical, model] = await Promise.all([
    fetchJson(`${state.base}/latest/historical.json`, null),
    fetchJson(`${state.base}/latest/gam-era5.json`, null),
  ]);
  state.historical = historical && historical.data_available ? historical : null;
  state.model = model && model.data_available ? model : null;
  if (!state.historical && !state.model) {
    showUnavailable();
    return;
  }
  if (!state.historical && state.model) state.view = "modelled";
  const boundaryPath = (state.historical && state.historical.assets && state.historical.assets.boundary)
    || (state.model && state.model.assets && state.model.assets.boundary);
  state.boundary = await fetchJson(assetUrl(boundaryPath), null);
  state.pulse = (state.historical && state.historical.default_pulse) || "lp";
  setRangeForView();
  await loadCurrentData();
  configureControls();
  render();
  window.addEventListener("resize", drawMap);
  document.getElementById("mapCanvas").addEventListener("click", selectRadarAtPoint);
})();

async function fetchJson(url, fallback) {
  if (!url) return fallback;
  try {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } catch (_) {
    return fallback;
  }
}

function assetUrl(path) {
  if (!path) return "";
  return path.startsWith("http") ? path : `${state.base}/${path.replace(/^\//, "")}`;
}

function activeManifest() {
  return state.view === "modelled" ? state.model : state.historical;
}

function setRangeForView() {
  const manifest = activeManifest();
  if (!manifest) return;
  const first = state.view === "modelled" ? manifest.first_time_utc.slice(0, 10) : manifest.first_date;
  const latest = state.view === "modelled" ? manifest.latest_time_utc.slice(0, 10) : manifest.latest_date;
  state.date = latest;
  const input = document.getElementById("dateInput");
  if (input) {
    input.min = first;
    input.max = latest;
    input.value = latest;
  }
}

async function loadYear(year) {
  const path = state.historical && state.historical.assets.daily_by_year[String(year)];
  state.yearPayload = path ? await fetchJson(assetUrl(path), {year, rows: []}) : {year, rows: []};
}

async function loadModelDay() {
  const assets = state.model && state.model.assets && state.model.assets[state.pulse];
  const path = assets && assets[state.date];
  state.modelDayPayload = path ? await fetchJson(assetUrl(path), {frames: []}) : {frames: []};
  const frames = state.modelDayPayload.frames || [];
  const available = frames.map((frame) => new Date(frame.time_utc).getUTCHours());
  state.hour = available.includes(state.hour) ? state.hour : (available[0] ?? 0);
}

async function loadCurrentData() {
  if (state.view === "modelled") return loadModelDay();
  return loadYear(Number(state.date.slice(0, 4)));
}

function configureControls() {
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    button.addEventListener("click", async () => {
      const next = button.dataset.view;
      if (next === state.view || (next === "modelled" && !state.model) || (next === "observed" && !state.historical)) return;
      state.view = next;
      stopAnimation();
      setRangeForView();
      await loadCurrentData();
      render();
    });
  });
  const dateInput = document.getElementById("dateInput");
  dateInput.addEventListener("change", async () => {
    if (!dateInput.value) return;
    state.date = dateInput.value;
    await loadCurrentData();
    render();
  });
  bindSegment("pulseControl", "pulse", async () => { if (state.view === "modelled") await loadModelDay(); });
  bindSegment("modelMetricControl", "modelMetric");
  const metric = document.getElementById("metricSelect");
  metric.addEventListener("change", () => { state.metric = metric.value; render(); });
  const hour = document.getElementById("hourInput");
  hour.addEventListener("input", () => { state.hour = Number(hour.value); render(); });
  document.getElementById("arrowsToggle").addEventListener("change", (event) => { state.showArrows = event.target.checked; drawMap(); });
  document.getElementById("uncertaintyToggle").addEventListener("change", (event) => { state.showUncertainty = event.target.checked; drawMap(); });
  document.getElementById("playButton").addEventListener("click", toggleAnimation);
}

function bindSegment(id, key, beforeRender) {
  const root = document.getElementById(id);
  root.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      state[key] = button.dataset.value;
      if (beforeRender) await beforeRender();
      render();
    });
  });
}

function render() {
  document.querySelectorAll(".view-tabs button").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  document.querySelectorAll(".model-control").forEach((element) => { element.hidden = state.view !== "modelled"; });
  document.querySelectorAll(".observed-control").forEach((element) => { element.hidden = state.view === "modelled"; });
  document.querySelectorAll("#pulseControl button").forEach((button) => button.classList.toggle("active", button.dataset.value === state.pulse));
  document.querySelectorAll("#modelMetricControl button").forEach((button) => button.classList.toggle("active", button.dataset.value === state.modelMetric));
  document.getElementById("dateInput").value = state.date;
  document.getElementById("hourInput").value = state.hour;
  document.getElementById("hourValue").textContent = `${String(state.hour).padStart(2, "0")}:00`;
  document.getElementById("plotsSection").hidden = state.view === "modelled";
  if (state.view === "modelled") renderModelled(); else renderObserved();
}

function renderObserved() {
  const rows = state.yearPayload && state.yearPayload.rows || [];
  state.visibleRows = aggregateObservedRows(rows.filter((row) => row.date === state.date && row.pulse === state.pulse));
  state.modelFrame = null;
  const metric = observedMetric();
  const values = state.visibleRows.map((row) => Number(row[state.metric])).filter(Number.isFinite);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  document.getElementById("mapTitle").textContent = "Daily radar reanalysis";
  document.getElementById("mapSubtitle").textContent = `${formatDate(state.date)} · ${state.pulse.toUpperCase()} · all available hours · ${state.visibleRows.length} radars`;
  document.getElementById("legendUnit").textContent = metric.label;
  document.getElementById("networkValue").textContent = mean === null ? "No observations" : metric.format(mean);
  document.getElementById("networkUnit").textContent = mean === null ? "Choose another date or product" : `${metric.meanLabel} across reporting radars`;
  document.getElementById("radarHeading").textContent = "Reporting radars";
  renderStatus();
  renderRadarList(metric);
  renderPlots((state.historical.assets && state.historical.assets.plots) || []);
  drawMap();
}

function aggregateObservedRows(rows) {
  const byRadar = new Map();
  for (const row of rows) {
    const current = byRadar.get(row.radar) || {
      ...row,
      profiles: 0,
      vid: 0,
      _heightTotal: 0,
      _speedTotal: 0,
      _weightedProfiles: 0,
    };
    const profiles = Number(row.profiles) || 0;
    const vid = Number(row.vid);
    if (Number.isFinite(vid)) current.vid += vid;
    current.profiles += profiles;
    for (const [metric, total] of [["height_m", "_heightTotal"], ["speed_ms", "_speedTotal"]]) {
      const value = Number(row[metric]);
      if (Number.isFinite(value) && profiles > 0) current[total] += value * profiles;
    }
    current._weightedProfiles += profiles;
    byRadar.set(row.radar, current);
  }
  return [...byRadar.values()].map((row) => ({
    ...row,
    height_m: row._weightedProfiles ? row._heightTotal / row._weightedProfiles : null,
    speed_ms: row._weightedProfiles ? row._speedTotal / row._weightedProfiles : null,
  }));
}

function renderModelled() {
  const frames = state.modelDayPayload && state.modelDayPayload.frames || [];
  state.modelFrame = frames.find((frame) => new Date(frame.time_utc).getUTCHours() === state.hour) || null;
  state.visibleRows = [];
  const metric = modelMetric();
  const cells = state.modelFrame && state.modelFrame.cells || [];
  const values = cells.map((cell) => Number(cell[state.modelMetric])).filter(Number.isFinite);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  document.getElementById("mapTitle").textContent = "Modelled UK bird flow";
  document.getElementById("mapSubtitle").textContent = state.modelFrame
    ? `${formatDate(state.date)} · ${String(state.hour).padStart(2, "0")}:00 UTC · ${state.pulse.toUpperCase()} · ${state.model.model_family.toUpperCase()}`
    : `${formatDate(state.date)} · no modelled frame published for ${String(state.hour).padStart(2, "0")}:00 UTC`;
  document.getElementById("legendUnit").textContent = metric.label;
  document.getElementById("networkValue").textContent = mean === null ? "No modelled frame" : metric.format(mean);
  document.getElementById("networkUnit").textContent = "Spatial mean across supported ERA5 grid cells";
  document.getElementById("radarHeading").textContent = "Model status";
  document.getElementById("radarList").innerHTML = `<p>${escapeHtml(state.model.interpretation || "Historical modelled reanalysis.")}</p>`;
  renderStatus();
  drawMap();
}

function observedMetric() {
  return {
    vid: {label: "VID passage index (birds km-2)", meanLabel: "Mean VID", format: (value) => `${value.toFixed(1)} birds km⁻²`},
    height_m: {label: "Mean flight height (m)", meanLabel: "Mean flight height", format: (value) => `${Math.round(value)} m`},
    speed_ms: {label: "Mean ground speed (m s-1)", meanLabel: "Mean ground speed", format: (value) => `${value.toFixed(1)} m s⁻¹`},
  }[state.metric];
}

function modelMetric() {
  return {
    mtr_birds_km_h: {label: "Migration traffic rate (birds km-1 h-1)", format: (value) => `${value.toFixed(1)} birds km⁻¹ h⁻¹`},
    vid_birds_per_km2: {label: "Vertically integrated density (birds km-2)", format: (value) => `${value.toFixed(1)} birds km⁻²`},
  }[state.modelMetric];
}

function renderStatus() {
  const badge = document.getElementById("statusBadge");
  const manifest = activeManifest();
  badge.textContent = state.view === "modelled" ? "Modelled reanalysis" : "Historical archive";
  badge.className = "badge ok";
  const rows = state.view === "modelled"
    ? [["Coverage", `${formatDate(manifest.first_time_utc.slice(0, 10))} to ${formatDate(manifest.latest_time_utc.slice(0, 10))}`], ["Model", manifest.model_family.toUpperCase()], ["Cadence", "Hourly UTC"], ["Time terms", "None"], ["Grid", "ERA5 native 0.25°"], ["Support", "Toggle uncertainty to inspect"]]
    : [["Coverage", `${formatDate(manifest.first_date)} to ${formatDate(manifest.latest_date)}`], ["Radars", manifest.radars.length], ["VPTS files", formatInteger(manifest.source.files_seen)], ["Profiles", formatInteger(manifest.source.profiles_seen)], ["Altitude", `${manifest.metric.altitude_min_m}–${manifest.metric.altitude_max_m} m`]];
  document.getElementById("statusList").innerHTML = rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function renderRadarList(metric) {
  const bySlug = new Map(state.visibleRows.map((row) => [row.radar, row]));
  document.getElementById("radarList").innerHTML = state.historical.radars.map((radar) => {
    const row = bySlug.get(radar.slug); const value = row && Number(row[state.metric]);
    return `<button type="button" data-radar="${escapeHtml(radar.slug)}"><span>${escapeHtml(radar.label)}</span><strong>${Number.isFinite(value) ? escapeHtml(metric.format(value)) : "No data"}</strong></button>`;
  }).join("");
  document.querySelectorAll("#radarList button").forEach((button) => button.addEventListener("click", () => {
    const point = state.points.find((item) => item.radar.slug === button.dataset.radar); if (point) highlightRadar(point);
  }));
}

function renderPlots(paths) {
  const labels = [["Annual nocturnal passage", "Network change through time"], ["Activity by solar period", "Day, twilight, and night"], ["Nocturnal phenology", "Median migration timing"], ["Radar archive coverage", "Availability by site and year"]];
  document.getElementById("plotGrid").innerHTML = paths.map((path, index) => `<figure><figcaption><strong>${escapeHtml(labels[index][0])}</strong><span>${escapeHtml(labels[index][1])}</span></figcaption><img src="${escapeHtml(assetUrl(path))}" alt="${escapeHtml(labels[index][0])}"></figure>`).join("");
}

function drawMap() {
  const canvas = document.getElementById("mapCanvas"); const rect = canvas.getBoundingClientRect(); if (!rect.width || !rect.height) return;
  const ratio = Math.min(3, window.devicePixelRatio || 1); canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); const {width, height} = rect;
  ctx.fillStyle = "#dce9e7"; ctx.fillRect(0, 0, width, height); drawGraticule(ctx, width, height); drawBoundary(ctx, width, height, true);
  if (state.view === "modelled") drawModelledField(ctx, width, height); else drawRadarValues(ctx, width, height);
  drawBoundary(ctx, width, height, false);
}

function drawGraticule(ctx, width, height) {
  ctx.strokeStyle = "rgba(66, 101, 91, .14)"; ctx.lineWidth = 1;
  for (let lon = -12; lon <= 3; lon += 3) { const top = project(lon, BOUNDS.north, width, height); const bottom = project(lon, BOUNDS.south, width, height); ctx.beginPath(); ctx.moveTo(top.x, top.y); ctx.lineTo(bottom.x, bottom.y); ctx.stroke(); }
  for (let lat = 50; lat <= 60; lat += 2) { const left = project(BOUNDS.west, lat, width, height); const right = project(BOUNDS.east, lat, width, height); ctx.beginPath(); ctx.moveTo(left.x, left.y); ctx.lineTo(right.x, right.y); ctx.stroke(); }
}

function drawBoundary(ctx, width, height, fill) {
  if (!state.boundary) return;
  for (const feature of state.boundary.features || []) {
    const polygons = feature.geometry.type === "MultiPolygon" ? feature.geometry.coordinates : [feature.geometry.coordinates]; ctx.beginPath();
    for (const polygon of polygons) for (const ring of polygon) { ring.forEach(([lon, lat], index) => { const point = project(lon, lat, width, height); if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y); }); ctx.closePath(); }
    if (fill) { ctx.fillStyle = feature.properties.ADM0_A3 === "GBR" ? "#f7f8f5" : "#eef2ed"; ctx.fill("evenodd"); }
    else { ctx.strokeStyle = feature.properties.ADM0_A3 === "GBR" ? "#253a32" : "#849188"; ctx.lineWidth = feature.properties.ADM0_A3 === "GBR" ? 1.25 : 0.8; ctx.stroke(); }
  }
}

function drawModelledField(ctx, width, height) {
  const cells = state.modelFrame && state.modelFrame.cells || []; if (!cells.length) return;
  const values = cells.map((cell) => Number(cell[state.modelMetric])).filter(Number.isFinite).sort((a, b) => a - b);
  const high = values[Math.min(values.length - 1, Math.floor(values.length * .9))] || 1;
  const grid = state.modelDayPayload && state.modelDayPayload.grid || {}; const lonStep = Number(grid.longitude_step || .25); const latStep = Number(grid.latitude_step || .25);
  for (const cell of cells) {
    const value = Number(cell[state.modelMetric]); if (!Number.isFinite(value)) continue;
    const northWest = project(Number(cell.longitude) - lonStep / 2, Number(cell.latitude) + latStep / 2, width, height); const southEast = project(Number(cell.longitude) + lonStep / 2, Number(cell.latitude) - latStep / 2, width, height);
    const support = Number(cell.support); const alpha = state.showUncertainty && Number.isFinite(support) ? .18 + .82 * Math.max(0, Math.min(1, support)) : .82;
    ctx.globalAlpha = alpha; ctx.fillStyle = densityColor(value / high); ctx.fillRect(northWest.x, northWest.y, southEast.x - northWest.x + 1, southEast.y - northWest.y + 1);
    if (state.showUncertainty && Number.isFinite(support) && support < .5) { ctx.globalAlpha = .34; ctx.strokeStyle = "#253a32"; ctx.lineWidth = .5; ctx.beginPath(); ctx.moveTo(northWest.x, southEast.y); ctx.lineTo(southEast.x, northWest.y); ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
  if (state.showArrows) drawVectors(ctx, width, height, cells, lonStep, latStep, high);
}

function drawVectors(ctx, width, height, cells, lonStep, latStep, high) {
  ctx.strokeStyle = "rgba(20, 37, 31, .72)"; ctx.fillStyle = "rgba(20, 37, 31, .72)"; ctx.lineWidth = 1.2;
  cells.forEach((cell, index) => {
    if (index % 3) return; const u = Number(cell.bird_u_ms), v = Number(cell.bird_v_ms), intensity = Number(cell.mtr_birds_km_h); if (!Number.isFinite(u) || !Number.isFinite(v) || !Number.isFinite(intensity) || intensity <= high * .03) return;
    const start = project(Number(cell.longitude), Number(cell.latitude), width, height); const scale = Math.min(18, Math.hypot(u, v) * 1.2); const end = {x: start.x + u * scale / Math.max(1, Math.hypot(u, v)), y: start.y - v * scale / Math.max(1, Math.hypot(u, v))};
    ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.lineTo(end.x, end.y); ctx.stroke(); const angle = Math.atan2(end.y - start.y, end.x - start.x); ctx.beginPath(); ctx.moveTo(end.x, end.y); ctx.lineTo(end.x - 4 * Math.cos(angle - .5), end.y - 4 * Math.sin(angle - .5)); ctx.lineTo(end.x - 4 * Math.cos(angle + .5), end.y - 4 * Math.sin(angle + .5)); ctx.closePath(); ctx.fill();
  });
}

function drawRadarValues(ctx, width, height) {
  const radarRows = new Map(state.visibleRows.map((row) => [row.radar, row])); const values = state.visibleRows.map((row) => Number(row[state.metric])).filter(Number.isFinite).sort((a, b) => a - b); const high = values[Math.min(values.length - 1, Math.floor(values.length * .9))] || 1; state.points = [];
  for (const radar of state.historical.radars) {
    const point = project(radar.longitude, radar.latitude, width, height); const row = radarRows.get(radar.slug); const value = row && Number(row[state.metric]); state.points.push({radar, row, value, x: point.x, y: point.y}); ctx.fillStyle = Number.isFinite(value) ? densityColor(value / high) : "#ffffff"; ctx.strokeStyle = Number.isFinite(value) ? "#ffffff" : "#89958e"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(point.x, point.y, Number.isFinite(value) ? 9 : 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
}

function project(lon, lat, width, height) { const padding = Math.max(16, Math.min(width, height) * .045); return {x: padding + ((lon - BOUNDS.west) / (BOUNDS.east - BOUNDS.west)) * (width - padding * 2), y: padding + ((BOUNDS.north - lat) / (BOUNDS.north - BOUNDS.south)) * (height - padding * 2)}; }
function selectRadarAtPoint(event) { if (state.view !== "observed") return; const rect = event.currentTarget.getBoundingClientRect(); const nearest = state.points.map((point) => ({point, distance: Math.hypot(point.x - event.clientX + rect.left, point.y - event.clientY + rect.top)})).sort((a, b) => a.distance - b.distance)[0]; if (nearest && nearest.distance < 28) highlightRadar(nearest.point); }
function highlightRadar(point) { const metric = observedMetric(); document.querySelectorAll("#radarList button").forEach((button) => button.classList.toggle("selected", button.dataset.radar === point.radar.slug)); document.getElementById("networkValue").textContent = Number.isFinite(point.value) ? metric.format(point.value) : "No observation"; document.getElementById("networkUnit").textContent = point.radar.label; }
function toggleAnimation() { if (state.animation) return stopAnimation(); const button = document.getElementById("playButton"); button.textContent = "❚❚"; button.classList.add("active"); state.animation = window.setInterval(() => { const available = (state.modelDayPayload.frames || []).map((frame) => new Date(frame.time_utc).getUTCHours()); const index = Math.max(0, available.indexOf(state.hour)); state.hour = available[(index + 1) % available.length]; render(); }, 900); }
function stopAnimation() { if (state.animation) window.clearInterval(state.animation); state.animation = null; const button = document.getElementById("playButton"); if (button) { button.textContent = "▶"; button.classList.remove("active"); } }
function densityColor(value) { const bounded = Math.max(0, Math.min(1, value)); const stops = [[66, 139, 116], [224, 185, 75], [198, 66, 53]]; const scaled = bounded * (stops.length - 1); const index = Math.min(stops.length - 2, Math.floor(scaled)); const fraction = scaled - index; const rgb = stops[index].map((channel, offset) => Math.round(channel + (stops[index + 1][offset] - channel) * fraction)); return `rgb(${rgb.join(",")})`; }
function showUnavailable() { const badge = document.getElementById("statusBadge"); badge.textContent = "Historical data unavailable"; badge.className = "badge waiting"; document.getElementById("mapSubtitle").textContent = "Historical artifacts have not been published."; }
function formatDate(value) { if (!value) return "Unknown"; return new Date(`${value}T12:00:00Z`).toLocaleDateString([], {day: "numeric", month: "short", year: "numeric"}); }
function formatPeriod(value) { return value.replace("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatInteger(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "Unknown"; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char])); }
