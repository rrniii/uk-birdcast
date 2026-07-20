const FALLBACK_BOUNDS = {west: -11.5, east: 4.5, south: 46.5, north: 61.5};
const PALETTE = ["#101817", "#16484a", "#43887a", "#9fc57a", "#e5d17c", "#f2b863", "#e47b52", "#b94135"];
const PALETTE_POSITIONS = [0, .18, .38, .58, .75, .90, .95, 1];
const COLOUR_SCHEMES = {
  robin: {label: "Robin passage", palette: PALETTE, positions: PALETTE_POSITIONS},
  night: {label: "Night-flight", palette: ["#070917", "#151d41", "#294f7a", "#4b82a8", "#76aec6", "#b7d6d4", "#edf0ce"], positions: [0, .16, .34, .52, .70, .86, 1]},
  atlantic: {label: "Atlantic blue", palette: ["#061824", "#0a3856", "#11658a", "#1e91ad", "#50b9c6", "#9bd9d5", "#e4f2df"], positions: [0, .16, .34, .52, .70, .86, 1]},
  thermal: {label: "Thermal migration", palette: ["#1a1110", "#593126", "#9d4830", "#d46d3b", "#eea851", "#f4d98a", "#fff6d4"], positions: [0, .16, .34, .52, .70, .86, 1]},
  scientific: {label: "Viridis", palette: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"], positions: [0, .11, .22, .33, .44, .56, .67, .78, .89, 1]},
};

const state = {
  base: "../",
  historical: null,
  model: null,
  boundary: null,
  yearPayload: null,
  modelDayPayload: null,
  view: "observed",
  dates: {observed: null, modelled: null},
  date: null,
  hour: 0,
  pulse: "lp",
  metric: "vid",
  modelMetric: "mtr_birds_km_h",
  colourScheme: "robin",
  showArrows: true,
  showUncertainty: false,
  visibleRows: [],
  statusRows: [],
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
    configureControls();
    setViewAvailability();
    return;
  }
  if (!state.historical) state.view = "modelled";
  const boundaryPath = (state.historical && state.historical.assets && state.historical.assets.boundary)
    || (state.model && state.model.assets && state.model.assets.boundary);
  state.boundary = await fetchJson(assetUrl(boundaryPath), null);
  state.pulse = (state.historical && state.historical.default_pulse) || "lp";
  configureControls();
  setViewAvailability();
  setRangeForView();
  await loadCurrentData();
  render();
  window.addEventListener("resize", drawMap);
  const canvas = document.getElementById("mapCanvas");
  canvas.addEventListener("pointermove", showRadarTooltip);
  canvas.addEventListener("pointerleave", hideRadarTooltip);
  canvas.addEventListener("click", selectRadarAtPoint);
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

function setViewAvailability() {
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    const available = button.dataset.view === "modelled" ? Boolean(state.model) : Boolean(state.historical);
    button.disabled = !available;
    button.title = available ? "" : `${button.textContent.trim()} are not published`;
    button.setAttribute("aria-disabled", String(!available));
  });
}

function setRangeForView() {
  const manifest = activeManifest();
  if (!manifest) return;
  const first = state.view === "modelled" ? manifest.first_time_utc.slice(0, 10) : manifest.first_date;
  const latest = state.view === "modelled" ? manifest.latest_time_utc.slice(0, 10) : manifest.latest_date;
  const saved = state.dates[state.view];
  state.date = saved && first <= saved && saved <= latest ? saved : latest;
  state.dates[state.view] = state.date;
  const input = document.getElementById("dateInput");
  input.min = first;
  input.max = latest;
  input.value = state.date;
}

async function loadYear(year) {
  const path = state.historical && state.historical.assets.daily_by_year[String(year)];
  state.yearPayload = path ? await fetchJson(assetUrl(path), {year, rows: []}) : {year, rows: []};
}

async function loadModelDay() {
  const assets = state.model && state.model.assets && state.model.assets[state.pulse];
  const path = assets && assets[state.date];
  state.modelDayPayload = path ? await fetchJson(assetUrl(path), {frames: []}) : {frames: []};
  const available = (state.modelDayPayload.frames || []).map((frame) => new Date(frame.time_utc).getUTCHours());
  state.hour = available.includes(state.hour) ? state.hour : (available[0] ?? 0);
}

async function loadCurrentData() {
  if (state.view === "modelled") {
    await loadModelDay();
    if (state.historical && state.historical.first_date <= state.date && state.date <= state.historical.latest_date) {
      await loadYear(Number(state.date.slice(0, 4)));
    } else {
      state.yearPayload = {rows: []};
    }
    return;
  }
  await loadYear(Number(state.date.slice(0, 4)));
}

function configureControls() {
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    button.addEventListener("click", async () => {
      const next = button.dataset.view;
      if (next === state.view || button.disabled) return;
      state.dates[state.view] = state.date;
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
    state.dates[state.view] = state.date;
    await loadCurrentData();
    render();
  });
  bindSegment("pulseControl", "pulse", async () => { await loadCurrentData(); });
  bindSegment("modelMetricControl", "modelMetric");
  document.getElementById("metricSelect").addEventListener("change", (event) => {
    state.metric = event.target.value;
    render();
  });
  document.getElementById("colourSchemeSelect").addEventListener("change", (event) => {
    state.colourScheme = event.target.value;
    render();
  });
  document.getElementById("hourInput").addEventListener("input", (event) => {
    state.hour = Number(event.target.value);
    render();
  });
  document.getElementById("arrowsToggle").addEventListener("change", (event) => {
    state.showArrows = event.target.checked;
    drawMap();
  });
  document.getElementById("uncertaintyToggle").addEventListener("change", (event) => {
    state.showUncertainty = event.target.checked;
    drawMap();
  });
  document.getElementById("playButton").addEventListener("click", toggleAnimation);
  document.getElementById("previousButton").addEventListener("click", () => stepHour(-1));
  document.getElementById("nextButton").addEventListener("click", () => stepHour(1));
  document.getElementById("resetButton").addEventListener("click", () => {
    const hours = availableHours();
    state.hour = hours[0] ?? 0;
    render();
  });
}

function bindSegment(id, key, beforeRender) {
  document.getElementById(id).querySelectorAll("button").forEach((button) => {
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
  document.getElementById("colourSchemeSelect").value = state.colourScheme;
  document.getElementById("dateInput").value = state.date;
  document.getElementById("hourInput").value = state.hour;
  document.getElementById("hourValue").textContent = `${String(state.hour).padStart(2, "0")}:00`;
  document.getElementById("plotsSection").hidden = state.view === "modelled";
  if (state.view === "modelled") renderModelled(); else renderObserved();
}

function renderObserved() {
  const rows = state.yearPayload && state.yearPayload.rows || [];
  state.visibleRows = aggregateObservedRows(rows.filter((row) => row.date === state.date && row.pulse === state.pulse));
  state.statusRows = state.visibleRows;
  state.modelFrame = null;
  const metric = observedMetric();
  const values = state.visibleRows.map((row) => Number(row[state.metric])).filter(Number.isFinite);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  document.getElementById("mapTitle").textContent = "Radar observations";
  document.getElementById("mapSubtitle").textContent = `${state.pulse.toUpperCase()} · all available hours · ${state.visibleRows.length} reporting radars`;
  document.getElementById("mapTimestamp").textContent = `${formatDate(state.date)} · UTC`;
  document.getElementById("networkValue").textContent = mean === null ? "No observations" : metric.format(mean);
  document.getElementById("networkUnit").textContent = mean === null ? "Choose another date or pulse" : `${metric.meanLabel} across reporting radars`;
  document.getElementById("radarHeading").textContent = "Reporting radars";
  renderStatus();
  renderRadarList(metric);
  renderPlots((state.historical.assets && state.historical.assets.plots) || []);
  setLegend(activeScale(values), metric);
  drawMap();
}

function aggregateObservedRows(rows) {
  const byRadar = new Map();
  for (const row of rows) {
    const current = byRadar.get(row.radar) || {...row, profiles: 0, vid: 0, _heightTotal: 0, _speedTotal: 0, _weightedProfiles: 0};
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
  const historicalRows = state.yearPayload && state.yearPayload.rows || [];
  state.statusRows = aggregateObservedRows(historicalRows.filter((row) => row.date === state.date && row.pulse === state.pulse));
  state.visibleRows = [];
  const metric = modelMetric();
  const cells = state.modelFrame && state.modelFrame.cells || [];
  const values = cells.map((cell) => Number(cell[state.modelMetric])).filter(Number.isFinite);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  document.getElementById("mapTitle").textContent = "Modelled migration";
  document.getElementById("mapSubtitle").textContent = state.modelFrame
    ? `Historical ERA5 reanalysis · ${state.pulse.toUpperCase()} · ${state.model.model_family.toUpperCase()}`
    : `No historical frame published for ${String(state.hour).padStart(2, "0")}:00 UTC`;
  document.getElementById("mapTimestamp").textContent = `${formatDate(state.date)} · ${String(state.hour).padStart(2, "0")} UTC`;
  document.getElementById("networkValue").textContent = mean === null ? "No modelled frame" : metric.format(mean);
  document.getElementById("networkUnit").textContent = "Spatial mean across in-range ERA5 cells";
  document.getElementById("radarHeading").textContent = "Radar status";
  renderStatus();
  renderRadarListForStatus();
  setLegend(activeScale(values), metric);
  drawMap();
}

function observedMetric() {
  return {
    vid: {title: "Vertically integrated density", label: "VID passage index", units: "birds km⁻²", meanLabel: "Mean VID", format: (value) => `${value.toFixed(1)} birds km⁻²`},
    height_m: {title: "Mean flight height", label: "Mean flight height", units: "m", meanLabel: "Mean flight height", format: (value) => `${Math.round(value)} m`},
    speed_ms: {title: "Mean ground speed", label: "Mean ground speed", units: "m s⁻¹", meanLabel: "Mean ground speed", format: (value) => `${value.toFixed(1)} m s⁻¹`},
  }[state.metric];
}

function modelMetric() {
  return {
    mtr_birds_km_h: {title: "Migration traffic rate", label: "Migration traffic rate", units: "birds km⁻¹ h⁻¹", format: (value) => `${value.toFixed(1)} birds km⁻¹ h⁻¹`},
    vid_birds_per_km2: {title: "Vertically integrated density", label: "Vertically integrated density", units: "birds km⁻²", format: (value) => `${value.toFixed(1)} birds km⁻²`},
  }[state.modelMetric];
}

function activeScale(values) {
  const manifest = activeManifest();
  const key = state.view === "modelled" ? state.modelMetric : state.metric;
  const published = manifest && manifest.colour_scales && manifest.colour_scales[key];
  return applyColourScheme(published || fallbackScale(values, ["vid", "mtr_birds_km_h", "vid_birds_per_km2"].includes(key) ? "log10" : "linear"));
}

function applyColourScheme(scale) {
  const scheme = COLOUR_SCHEMES[state.colourScheme] || COLOUR_SCHEMES.robin;
  return {...scale, palette: [...scheme.palette], palette_positions: [...scheme.positions], zero_colour: scheme.palette[0]};
}

function fallbackScale(values, transform) {
  const finite = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (transform === "log10") {
    const positive = finite.filter((value) => value > 0);
    const minimum = positive[Math.floor(positive.length * .01)] || 1;
    const maximum = positive[Math.min(positive.length - 1, Math.floor(positive.length * .99))] || 10;
    return {transform, minimum, maximum: Math.max(maximum, minimum * 10), ticks: logTicks(minimum, Math.max(maximum, minimum * 10)), palette: PALETTE, palette_positions: PALETTE_POSITIONS, zero_colour: PALETTE[0]};
  }
  const minimum = finite[Math.floor(finite.length * .05)] || 0;
  const maximum = finite[Math.min(finite.length - 1, Math.floor(finite.length * .95))] || minimum + 1;
  return {transform, minimum, maximum, ticks: [minimum, (minimum + maximum) / 2, maximum], palette: PALETTE, palette_positions: PALETTE_POSITIONS};
}

function setLegend(scale, metric) {
  document.getElementById("legendTitle").textContent = metric.title;
  document.getElementById("legendUnit").textContent = `${metric.units} · ${scale.transform === "log10" ? "log scale" : "linear scale"}`;
  const palette = scale.palette || PALETTE;
  const positions = scale.palette_positions || PALETTE_POSITIONS;
  document.getElementById("legendRamp").style.background = `linear-gradient(to top, ${palette.map((colour, index) => `${colour} ${(Number(positions[index]) * 100).toFixed(1)}%`).join(", ")})`;
  const ticks = (scale.ticks || [scale.minimum, scale.maximum]).filter((value) => Number(value) >= Number(scale.minimum) && Number(value) <= Number(scale.maximum));
  document.getElementById("legendTicks").innerHTML = ticks.map((value) => {
    const position = scalePosition(Number(value), scale) * 100;
    return `<span style="bottom:${position.toFixed(3)}%">${escapeHtml(formatTick(Number(value)))}</span>`;
  }).join("");
}

function renderStatus() {
  const badge = document.getElementById("statusBadge");
  const manifest = activeManifest();
  badge.textContent = state.view === "modelled" ? "Historical ERA5 reanalysis" : "Historical radar archive";
  badge.className = "badge ok";
  const rows = state.view === "modelled"
    ? [["Coverage", `${formatDate(manifest.first_time_utc.slice(0, 10))} to ${formatDate(manifest.latest_time_utc.slice(0, 10))}`], ["Model", manifest.model_family.toUpperCase()], ["Cadence", "Hourly UTC"], ["Grid", manifest.grid.resolution || "ERA5 native 0.25°"], ["Domain", "Physical radar range; land and water"]]
    : [["Coverage", `${formatDate(manifest.first_date)} to ${formatDate(manifest.latest_date)}`], ["Radars", manifest.radars.length], ["VPTS files", formatInteger(manifest.source.files_seen)], ["Profiles", formatInteger(manifest.source.profiles_seen)], ["Altitude", `${manifest.metric.altitude_min_m}–${manifest.metric.altitude_max_m} m`]];
  document.getElementById("statusList").innerHTML = rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function renderRadarList(metric) {
  const bySlug = new Map(state.visibleRows.map((row) => [row.radar, row]));
  document.getElementById("radarList").innerHTML = state.historical.radars.map((radar) => {
    const row = bySlug.get(radar.slug);
    const value = row && Number(row[state.metric]);
    return `<button type="button" data-radar="${escapeHtml(radar.slug)}"><span>${escapeHtml(radar.label)}</span><strong>${Number.isFinite(value) ? escapeHtml(metric.format(value)) : "Unavailable"}</strong></button>`;
  }).join("");
  bindRadarList();
}

function renderRadarListForStatus() {
  const bySlug = new Map(state.statusRows.map((row) => [row.radar, row]));
  const radars = (state.historical && state.historical.radars) || [];
  document.getElementById("radarList").innerHTML = radars.map((radar) => `<button type="button" data-radar="${escapeHtml(radar.slug)}"><span>${escapeHtml(radar.label)}</span><strong>${bySlug.has(radar.slug) ? "Available" : "Unavailable"}</strong></button>`).join("");
  bindRadarList();
}

function bindRadarList() {
  document.querySelectorAll("#radarList button").forEach((button) => button.addEventListener("click", () => {
    const point = state.points.find((item) => item.radar.slug === button.dataset.radar);
    if (point) highlightRadar(point);
  }));
}

function renderPlots(paths) {
  const labels = [["Annual nocturnal passage", "Network change through time"], ["Activity by solar period", "Day, twilight, and night"], ["Nocturnal phenology", "Median migration timing"], ["Radar archive coverage", "Availability by site and year"]];
  document.getElementById("plotGrid").innerHTML = paths.map((path, index) => `<figure><figcaption><strong>${escapeHtml(labels[index][0])}</strong><span>${escapeHtml(labels[index][1])}</span></figcaption><img src="${escapeHtml(assetUrl(path))}" alt="${escapeHtml(labels[index][0])}"></figure>`).join("");
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
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, rect.width, rect.height);
  drawGraticule(ctx, rect.width, rect.height);
  drawBoundary(ctx, rect.width, rect.height, true);
  if (state.view === "modelled") drawModelledField(ctx, rect.width, rect.height);
  else drawRadarValues(ctx, rect.width, rect.height);
  drawBoundary(ctx, rect.width, rect.height, false);
  drawRadarMarkers(ctx, rect.width, rect.height);
}

function currentBounds() {
  const bounds = state.model && state.model.grid && state.model.grid.bounds;
  if (bounds && ["west", "east", "south", "north"].every((key) => Number.isFinite(Number(bounds[key])))) {
    return Object.fromEntries(Object.entries(bounds).map(([key, value]) => [key, Number(value)]));
  }
  const radars = state.historical && state.historical.radars || [];
  if (radars.length && radars.every((radar) => Number.isFinite(Number(radar.max_range_m)))) {
    let west = 180, east = -180, south = 90, north = -90;
    for (const radar of radars) {
      const rangeKm = Number(radar.max_range_m) / 1000;
      const latDelta = rangeKm / 111.195;
      const lonDelta = rangeKm / (111.195 * Math.max(Math.cos(Number(radar.latitude) * Math.PI / 180), .01));
      west = Math.min(west, Number(radar.longitude) - lonDelta);
      east = Math.max(east, Number(radar.longitude) + lonDelta);
      south = Math.min(south, Number(radar.latitude) - latDelta);
      north = Math.max(north, Number(radar.latitude) + latDelta);
    }
    return {west: west - .25, east: east + .25, south: south - .25, north: north + .25};
  }
  return FALLBACK_BOUNDS;
}

function drawGraticule(ctx, width, height) {
  const bounds = currentBounds();
  ctx.strokeStyle = "rgba(210, 225, 217, .13)";
  ctx.lineWidth = 1;
  for (let lon = Math.ceil(bounds.west / 2) * 2; lon <= bounds.east; lon += 2) {
    const top = project(lon, bounds.north, width, height);
    const bottom = project(lon, bounds.south, width, height);
    ctx.beginPath(); ctx.moveTo(top.x, top.y); ctx.lineTo(bottom.x, bottom.y); ctx.stroke();
  }
  for (let lat = Math.ceil(bounds.south / 2) * 2; lat <= bounds.north; lat += 2) {
    const left = project(bounds.west, lat, width, height);
    const right = project(bounds.east, lat, width, height);
    ctx.beginPath(); ctx.moveTo(left.x, left.y); ctx.lineTo(right.x, right.y); ctx.stroke();
  }
}

function drawBoundary(ctx, width, height, fill) {
  if (!state.boundary) return;
  for (const feature of state.boundary.features || []) {
    traceGeometry(ctx, feature.geometry, width, height);
    if (fill) {
      ctx.fillStyle = feature.properties.ADM0_A3 === "GBR" ? "#101512" : "#090c0b";
      ctx.fill("evenodd");
    } else {
      ctx.strokeStyle = feature.properties.ADM0_A3 === "GBR" ? "#f2f5f3" : "#87928c";
      ctx.lineWidth = feature.properties.ADM0_A3 === "GBR" ? 1.3 : .75;
      ctx.stroke();
    }
  }
}

function traceGeometry(ctx, geometry, width, height) {
  const polygons = geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
  ctx.beginPath();
  for (const polygon of polygons) for (const ring of polygon) {
    ring.forEach(([lon, lat], index) => {
      const point = project(lon, lat, width, height);
      if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
    });
    ctx.closePath();
  }
}

function drawModelledField(ctx, width, height) {
  const cells = state.modelFrame && state.modelFrame.cells || [];
  if (!cells.length) return;
  const scale = activeScale(cells.map((cell) => Number(cell[state.modelMetric])));
  const grid = state.modelDayPayload && state.modelDayPayload.grid || {};
  const lonStep = Number(grid.longitude_step || .25);
  const latStep = Number(grid.latitude_step || .25);
  for (const cell of cells) {
    const value = Number(cell[state.modelMetric]);
    if (!Number.isFinite(value)) continue;
    const northWest = project(Number(cell.longitude) - lonStep / 2, Number(cell.latitude) + latStep / 2, width, height);
    const southEast = project(Number(cell.longitude) + lonStep / 2, Number(cell.latitude) - latStep / 2, width, height);
    const support = Number(cell.support);
    ctx.globalAlpha = state.showUncertainty && Number.isFinite(support) ? .2 + .8 * Math.max(0, Math.min(1, support)) : .94;
    ctx.fillStyle = quantitativeColor(value, scale);
    ctx.fillRect(northWest.x, northWest.y, southEast.x - northWest.x + 1, southEast.y - northWest.y + 1);
    if (state.showUncertainty && Number.isFinite(support) && support < .5) {
      ctx.globalAlpha = .42;
      ctx.strokeStyle = "#dce4df";
      ctx.lineWidth = .45;
      ctx.beginPath(); ctx.moveTo(northWest.x, southEast.y); ctx.lineTo(southEast.x, northWest.y); ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  if (state.showArrows) drawVectors(ctx, width, height, cells, scale);
}

function drawVectors(ctx, width, height, cells, scale) {
  ctx.strokeStyle = "#f2b632";
  ctx.fillStyle = "#f2b632";
  ctx.lineWidth = 1.8;
  cells.forEach((cell, index) => {
    if (index % 4) return;
    const u = Number(cell.bird_u_ms), v = Number(cell.bird_v_ms), intensity = Number(cell.mtr_birds_km_h);
    if (!Number.isFinite(u) || !Number.isFinite(v) || !Number.isFinite(intensity) || scalePosition(intensity, scale) < .08) return;
    const start = project(Number(cell.longitude), Number(cell.latitude), width, height);
    const magnitude = Math.hypot(u, v);
    const length = Math.min(22, 7 + magnitude * .8);
    const end = {x: start.x + u * length / Math.max(1, magnitude), y: start.y - v * length / Math.max(1, magnitude)};
    ctx.beginPath(); ctx.moveTo(start.x, start.y); ctx.lineTo(end.x, end.y); ctx.stroke();
    const angle = Math.atan2(end.y - start.y, end.x - start.x);
    ctx.beginPath(); ctx.moveTo(end.x, end.y); ctx.lineTo(end.x - 5 * Math.cos(angle - .5), end.y - 5 * Math.sin(angle - .5)); ctx.lineTo(end.x - 5 * Math.cos(angle + .5), end.y - 5 * Math.sin(angle + .5)); ctx.closePath(); ctx.fill();
  });
}

function drawRadarValues(ctx, width, height) {
  const rows = new Map(state.visibleRows.map((row) => [row.radar, row]));
  const scale = activeScale(state.visibleRows.map((row) => Number(row[state.metric])));
  const radars = state.historical && state.historical.radars || [];
  for (const radar of radars) {
    const row = rows.get(radar.slug);
    const value = row && Number(row[state.metric]);
    if (!Number.isFinite(value)) continue;
    const point = project(radar.longitude, radar.latitude, width, height);
    const glow = ctx.createRadialGradient(point.x, point.y, 1, point.x, point.y, 20);
    const colour = quantitativeColor(value, scale);
    glow.addColorStop(0, colour);
    glow.addColorStop(.35, colour);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(point.x, point.y, 20, 0, Math.PI * 2); ctx.fill();
  }
}

function drawRadarMarkers(ctx, width, height) {
  const radars = (state.historical && state.historical.radars) || [];
  const available = new Set(state.statusRows.map((row) => row.radar));
  state.points = [];
  for (const radar of radars) {
    const point = project(radar.longitude, radar.latitude, width, height);
    const isAvailable = available.has(radar.slug);
    drawRadarIcon(ctx, point.x, point.y, isAvailable ? "#22ed5a" : "#f14640");
    state.points.push({radar, row: state.statusRows.find((row) => row.radar === radar.slug), x: point.x, y: point.y, available: isAvailable});
  }
}

function drawRadarIcon(ctx, x, y, colour) {
  ctx.save();
  ctx.translate(x, y);
  ctx.strokeStyle = colour;
  ctx.fillStyle = colour;
  ctx.lineWidth = 1.15;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // Satellite-dish silhouette: a heavy lower bowl, light rim, feed, and flat base.
  ctx.rotate(-.55);
  ctx.beginPath();
  ctx.ellipse(-1, 0, 4.8, 2, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.lineWidth = 3.3;
  ctx.beginPath();
  ctx.ellipse(-1, 0, 4.8, 2, 0, Math.PI * .62, Math.PI * 1.4);
  ctx.stroke();
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(1.4, -1); ctx.lineTo(4.2, -4.5);
  ctx.moveTo(1.9, 1.2); ctx.lineTo(4.2, 4.7);
  ctx.stroke();
  ctx.beginPath(); ctx.arc(4.2, -4.5, 1.45, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
  ctx.beginPath(); ctx.moveTo(x + 1.7, y + 5.6); ctx.lineTo(x + 8.3, y + 5.6); ctx.lineTo(x + 5, y + 2.1); ctx.closePath(); ctx.fill();
}

function project(lon, lat, width, height) {
  const bounds = currentBounds();
  const plot = mapPlotBounds(width, height);
  return {
    x: plot.left + ((lon - bounds.west) / (bounds.east - bounds.west)) * (plot.right - plot.left),
    y: plot.top + ((bounds.north - lat) / (bounds.north - bounds.south)) * (plot.bottom - plot.top),
  };
}

function mapPlotBounds(width, height) {
  const compact = width <= 850;
  const left = compact ? 10 : Math.max(20, width * .035);
  const rightRail = compact ? 122 : Math.max(180, width * .18);
  const topRail = compact ? 108 : 112;
  const bottomRail = compact ? 106 : 142;
  const top = Math.min(height - 120, topRail);
  return {
    left,
    right: Math.max(left + 120, width - rightRail),
    top,
    bottom: Math.max(top + 160, height - bottomRail),
  };
}

function scalePosition(value, scale) {
  const minimum = Number(scale.minimum);
  const maximum = Number(scale.maximum);
  if (!Number.isFinite(value) || maximum <= minimum) return 0;
  if (value <= 0 && scale.transform === "log10") return 0;
  const raw = scale.transform === "log10"
    ? (Math.log10(Math.max(value, minimum)) - Math.log10(minimum)) / (Math.log10(maximum) - Math.log10(minimum))
    : (value - minimum) / (maximum - minimum);
  return Math.max(0, Math.min(1, raw));
}

function quantitativeColor(value, scale) {
  if (!Number.isFinite(value)) return "rgba(0,0,0,0)";
  if (value <= 0 && scale.transform === "log10") return scale.zero_colour || "#000";
  const palette = scale.palette || PALETTE;
  const positions = Array.isArray(scale.palette_positions) && scale.palette_positions.length === palette.length
    ? scale.palette_positions.map(Number)
    : palette.length === PALETTE.length
      ? PALETTE_POSITIONS
      : palette.map((_, index) => index / (palette.length - 1));
  const position = scalePosition(value, scale);
  const index = Math.max(0, positions.findIndex((stop) => stop >= position) - 1);
  const endIndex = Math.min(palette.length - 1, index + 1);
  const span = Math.max(positions[endIndex] - positions[index], Number.EPSILON);
  const fraction = Math.max(0, Math.min(1, (position - positions[index]) / span));
  const start = hexToRgb(palette[index]);
  const end = hexToRgb(palette[endIndex]);
  return `rgb(${start.map((channel, offset) => Math.round(channel + (end[offset] - channel) * fraction)).join(",")})`;
}

function hexToRgb(value) {
  const text = value.replace("#", "");
  const expanded = text.length === 3 ? text.split("").map((part) => part + part).join("") : text;
  return [0, 2, 4].map((offset) => parseInt(expanded.slice(offset, offset + 2), 16));
}

function logTicks(minimum, maximum) {
  const ticks = [];
  for (let exponent = Math.floor(Math.log10(minimum)); exponent <= Math.ceil(Math.log10(maximum)); exponent += 1) {
    for (const factor of [1, 2, 5]) {
      const value = factor * 10 ** exponent;
      if (minimum <= value && value <= maximum) ticks.push(value);
    }
  }
  return ticks;
}

function availableHours() {
  return (state.modelDayPayload && state.modelDayPayload.frames || []).map((frame) => new Date(frame.time_utc).getUTCHours());
}

function stepHour(direction) {
  const hours = availableHours();
  if (!hours.length) return;
  const index = Math.max(0, hours.indexOf(state.hour));
  state.hour = hours[(index + direction + hours.length) % hours.length];
  render();
}

function toggleAnimation() {
  if (state.animation) return stopAnimation();
  const button = document.getElementById("playButton");
  button.textContent = "❚❚";
  button.classList.add("active");
  state.animation = window.setInterval(() => stepHour(1), 900);
}

function stopAnimation() {
  if (state.animation) window.clearInterval(state.animation);
  state.animation = null;
  const button = document.getElementById("playButton");
  if (button) { button.textContent = "▶"; button.classList.remove("active"); }
}

function nearestRadar(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  return state.points.map((point) => ({
    point,
    distance: Math.hypot(point.x - (event.clientX - rect.left), point.y - (event.clientY - rect.top)),
  })).sort((a, b) => a.distance - b.distance)[0];
}

function showRadarTooltip(event) {
  const nearest = nearestRadar(event);
  const tooltip = document.getElementById("mapTooltip");
  if (!nearest || nearest.distance > 22) return hideRadarTooltip();
  const row = nearest.point.row;
  const valueKey = state.view === "observed" ? state.metric : null;
  const value = valueKey && row ? Number(row[valueKey]) : null;
  const formatted = Number.isFinite(value) ? observedMetric().format(value) : nearest.point.available ? "Observation available" : "No observation for selected date";
  tooltip.innerHTML = `<strong>${escapeHtml(nearest.point.radar.label)}</strong>${escapeHtml(formatted)}<br>${escapeHtml(state.pulse.toUpperCase())} · ${escapeHtml(state.date)}`;
  tooltip.hidden = false;
  const rect = event.currentTarget.getBoundingClientRect();
  tooltip.style.left = `${Math.min(rect.width - 220, event.clientX - rect.left + 12)}px`;
  tooltip.style.top = `${Math.max(8, event.clientY - rect.top - 48)}px`;
}

function hideRadarTooltip() {
  document.getElementById("mapTooltip").hidden = true;
}

function selectRadarAtPoint(event) {
  const nearest = nearestRadar(event);
  if (nearest && nearest.distance < 26) highlightRadar(nearest.point);
}

function highlightRadar(point) {
  document.querySelectorAll("#radarList button").forEach((button) => button.classList.toggle("selected", button.dataset.radar === point.radar.slug));
  if (state.view === "observed") {
    const value = point.row && Number(point.row[state.metric]);
    document.getElementById("networkValue").textContent = Number.isFinite(value) ? observedMetric().format(value) : "No observation";
    document.getElementById("networkUnit").textContent = point.radar.label;
  }
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
function formatInteger(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "Unknown"; }
function formatTick(value) {
  if (Math.abs(value) >= 1000) return `${Number((value / 1000).toPrecision(3))}k`;
  if (Math.abs(value) >= 1) return String(Number(value.toPrecision(4)));
  return String(Number(value.toPrecision(2)));
}
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char])); }
