const state = {
  base: "../",
  manifest: null,
  frame: null,
  radars: [],
  index: 0,
  view: "reanalysis",
  playing: false,
  timer: null,
};

(async function () {
  const config = await fetchJson("config.json", {data_base_url: "../"});
  state.base = (config.data_base_url || "../").replace(/\/$/, "");
  const [status, radars, validation, forecast] = await Promise.all([
    fetchJson(`${state.base}/latest/status.json`, {}),
    fetchJson(`${state.base}/latest/radars.json`, {radars: []}),
    fetchJson(`${state.base}/latest/validation_status.json`, {}),
    fetchJson(`${state.base}/latest/forecast.json`, null),
  ]);
  state.radars = radars.radars || [];
  state.manifest = forecast;
  renderStatus(status, forecast);
  renderRadars(state.radars);
  renderValidation(validation);
  bindControls();
  await selectIndex(0);
})();

async function fetchJson(url, fallback) {
  try {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return await response.json();
  } catch (error) {
    return fallback === null ? {error: String(error), valid_times_utc: [], assets: {frames: []}} : fallback;
  }
}

function bindControls() {
  document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", async () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === button));
    const slider = document.getElementById("timeSlider");
    slider.disabled = state.view === "reanalysis";
    await selectIndex(state.view === "reanalysis" ? 0 : state.index);
  }));
  const slider = document.getElementById("timeSlider");
  const count = (state.manifest.valid_times_utc || []).length;
  slider.max = String(Math.max(0, count - 1));
  slider.addEventListener("input", () => selectIndex(Number(slider.value)));
  document.getElementById("uncertaintyToggle").addEventListener("change", drawMap);
  document.getElementById("playButton").addEventListener("click", togglePlayback);
}

async function selectIndex(index) {
  const frames = state.manifest.assets && state.manifest.assets.frames || [];
  state.index = Math.max(0, Math.min(index, frames.length - 1));
  document.getElementById("timeSlider").value = String(state.index);
  state.frame = frames.length ? await fetchJson(`${state.base}/${frames[state.index]}`, null) : null;
  const valid = state.manifest.valid_times_utc && state.manifest.valid_times_utc[state.index];
  document.getElementById("validTime").textContent = valid ? new Date(valid).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}) : "Forecast unavailable";
  document.getElementById("leadTime").textContent = valid ? `+${state.index * 6} h` : "";
  drawMap();
}

function togglePlayback() {
  state.playing = !state.playing;
  document.getElementById("playButton").textContent = state.playing ? "❚❚" : "▶";
  if (!state.playing) {
    clearInterval(state.timer);
    return;
  }
  state.view = "forecast";
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.view === "forecast"));
  document.getElementById("timeSlider").disabled = false;
  state.timer = setInterval(() => {
    const count = (state.manifest.valid_times_utc || []).length;
    selectIndex(count ? (state.index + 1) % count : 0);
  }, 900);
}

function renderStatus(status, forecast) {
  const badge = document.getElementById("statusBadge");
  const mode = forecast.mode || "unavailable";
  badge.textContent = mode.replace("_", " ");
  badge.className = `badge ${mode === "assimilated" ? "ok" : "waiting"}`;
  const banner = document.getElementById("staleBanner");
  if (mode !== "assimilated") {
    banner.hidden = false;
    banner.textContent = mode === "weather_only"
      ? `Radar observations are ${formatAge(forecast.radar_age_hours)} old. This run is weather-only and has wider uncertainty.`
      : `Radar observations are ${formatAge(forecast.radar_age_hours)} old. The previous state is being propagated.`;
  }
  const rows = [
    ["Issue", forecast.issue_time_utc || "not available"],
    ["Radar input", forecast.radar_observation_time_utc || status.latest_vpts_date || "not available"],
    ["Radar age", formatAge(forecast.radar_age_hours)],
    ["Weather cycle", forecast.weather_cycle_utc || "fallback"],
    ["Model", forecast.model_id || "not available"],
    ["Members", forecast.ensemble_members || "not available"],
  ];
  document.getElementById("statusList").innerHTML = rows.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function formatAge(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} h` : "unknown";
}

function renderRadars(radars) {
  document.getElementById("radarList").innerHTML = radars.map((radar) =>
    `<div><strong>${escapeHtml(radar.label)}</strong><span>${escapeHtml(radar.slug)}</span></div>`
  ).join("");
}

function renderValidation(validation) {
  document.getElementById("validationText").textContent = validation.bto_data_available
    ? "Aggregate BTO validation is available."
    : "BTO validation dataset request pending.";
}

function drawMap() {
  const canvas = document.getElementById("mapCanvas");
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#e9eeea";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!state.frame || !state.frame.density_p50) {
    ctx.fillStyle = "#26342e";
    ctx.font = "20px system-ui";
    ctx.fillText("No gridded forecast has been published.", 42, 64);
    drawOverlay(ctx, canvas.width, canvas.height, null);
    return;
  }
  const values = document.getElementById("uncertaintyToggle").checked
    ? state.frame.uncertainty_width
    : state.frame.density_p50;
  const rows = values.length;
  const cols = values[0].length;
  const cellWidth = canvas.width / cols;
  const cellHeight = canvas.height / rows;
  const flat = values.flat().filter(Number.isFinite).sort((a, b) => a - b);
  const high = flat[Math.floor(flat.length * 0.98)] || 1;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      ctx.fillStyle = densityColor(values[row][col] / high);
      ctx.fillRect(col * cellWidth, row * cellHeight, cellWidth + 1, cellHeight + 1);
    }
  }
  drawOverlay(ctx, canvas.width, canvas.height, state.frame);
}

function drawOverlay(ctx, width, height, frame) {
  const overlay = state.manifest.map_overlay || {coastlines: [], radars: []};
  const shape = frame && frame.shape || [230, 175];
  const stride = frame && frame.stride || 1;
  ctx.strokeStyle = "rgba(21, 39, 31, .82)";
  ctx.lineWidth = 2;
  for (const polygon of overlay.coastlines || []) {
    ctx.beginPath();
    polygon.forEach((point, index) => {
      const x = (point.col / stride) / shape[1] * width;
      const y = (point.row / stride) / shape[0] * height;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  for (const radar of overlay.radars || []) {
    const point = {
      x: (radar.col / stride) / shape[1] * width,
      y: (radar.row / stride) / shape[0] * height,
    };
    ctx.fillStyle = "#101814";
    ctx.beginPath();
    ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function densityColor(value) {
  const bounded = Math.max(0, Math.min(1, value));
  const stops = [[236, 240, 235], [80, 151, 126], [243, 184, 72], [190, 55, 48]];
  const scaled = bounded * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  const rgb = stops[index].map((channel, offset) => Math.round(channel + (stops[index + 1][offset] - channel) * fraction));
  return `rgb(${rgb.join(",")})`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}
