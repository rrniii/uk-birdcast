"use strict";

const PALETTES = {
  atlantic: ["#061a2d", "#0a4d68", "#168aad", "#52b69a", "#d9ed92"],
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  night: ["#111827", "#253b80", "#497ed2", "#9c85d8", "#f3c4f1"],
  thermal: ["#1d3557", "#457b9d", "#a8dadc", "#f4a261", "#e63946"],
};

const state = {
  base: "/europe-bird-maps/data",
  manifest: null,
  boundaries: null,
  day: null,
  dayCache: new Map(),
  date: null,
  frame: 0,
  palette: "atlantic",
  vectors: true,
  playing: false,
  timer: null,
  animationGeneration: 0,
  loadGeneration: 0,
  loadController: null,
  loading: false,
  view: {zoom: 1, dx: 0, dy: 0},
  drag: null,
  grid: null,
};

init();

async function init() {
  const config = await fetchJson("config.json", {});
  state.base = config.data_base_url || state.base;
  state.manifest = await fetchJson(`${state.base}/latest/relative-flow.json`, null);
  state.boundaries = await fetchJson("regional-boundaries.geojson", null);
  if (!state.manifest || !state.manifest.data_available) {
    unavailable();
    return;
  }
  state.date = manifestDate("latest_time_utc");
  configure();
  const loaded = await selectAvailableDate(publishedDates().reverse(), "first");
  if (loaded !== "selected") {
    unavailable("No readable relative-activity map days are currently published");
    return;
  }
  render();
  addEventListener("resize", draw);
}

async function fetchJson(url, fallback) {
  try {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(String(response.status));
    return await response.json();
  } catch (_) {
    return fallback;
  }
}

function asset(path) {
  return path && path.startsWith("http")
    ? path
    : `${state.base}/${String(path).replace(/^\//, "")}`;
}

function dayUrl(date) {
  return asset(state.manifest.assets.daily_template.replace("{date}", date));
}

function isoDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
    ? value
    : null;
}

function manifestDate(key) {
  const value = state.manifest && state.manifest[key];
  return isoDate(typeof value === "string" ? value.slice(0, 10) : value);
}

function publishedDates() {
  const first = manifestDate("first_time_utc");
  const latest = manifestDate("latest_time_utc");
  const declared = Array.isArray(state.manifest && state.manifest.available_dates)
    ? state.manifest.available_dates.map(isoDate).filter(Boolean)
    : [];
  if (declared.length) {
    return [...new Set(declared)]
      .filter((date) => (!first || date >= first) && (!latest || date <= latest))
      .sort();
  }
  if (!first || !latest || first > latest) return [];
  const dates = [];
  const end = Date.parse(`${latest}T00:00:00Z`);
  for (let timestamp = Date.parse(`${first}T00:00:00Z`); timestamp <= end; timestamp += 86400000) {
    dates.push(new Date(timestamp).toISOString().slice(0, 10));
  }
  return dates;
}

async function fetchDay(date, signal) {
  if (state.dayCache.has(date)) return state.dayCache.get(date);
  const response = await fetch(dayUrl(date), {cache: "no-store", signal});
  if (response.status === 404) {
    state.dayCache.set(date, null);
    return null;
  }
  if (!response.ok) throw new Error(`relative-activity map request failed (${response.status})`);
  const payload = await response.json();
  if (!payload || !Array.isArray(payload.frames)) {
    throw new Error("relative-activity map response has no frames array");
  }
  if (payload.date && payload.date !== date) {
    throw new Error("relative-activity map response date does not match the request");
  }
  const day = payload.frames.length ? payload : null;
  state.dayCache.set(date, day);
  return day;
}

function beginDayRequest() {
  if (state.loadController) state.loadController.abort();
  const request = {
    generation: ++state.loadGeneration,
    controller: new AbortController(),
  };
  state.loadController = request.controller;
  setLoading(true);
  return request;
}

function requestIsCurrent(request) {
  return request.generation === state.loadGeneration && !request.controller.signal.aborted;
}

function finishDayRequest(request) {
  if (!requestIsCurrent(request)) return;
  state.loadController = null;
  setLoading(false);
}

function commitDay(date, day, edge) {
  state.date = date;
  state.day = day;
  state.frame = edge === "last" ? day.frames.length - 1 : 0;
  document.querySelector("#dateInput").value = date;
}

async function selectAvailableDate(dates, edge) {
  const request = beginDayRequest();
  try {
    for (const date of dates) {
      const day = await fetchDay(date, request.controller.signal);
      if (!requestIsCurrent(request)) return "cancelled";
      if (!day) continue;
      commitDay(date, day, edge);
      return "selected";
    }
    return requestIsCurrent(request) ? "none" : "cancelled";
  } catch (error) {
    if (error.name === "AbortError" || !requestIsCurrent(request)) return "cancelled";
    console.error(error);
    announce("The published relative-activity map could not be loaded. Please retry.");
    return "error";
  } finally {
    finishDayRequest(request);
  }
}

function configure() {
  const dateInput = document.querySelector("#dateInput");
  dateInput.min = manifestDate("first_time_utc") || "";
  dateInput.max = manifestDate("latest_time_utc") || "";
  dateInput.value = state.date || "";
  dateInput.onchange = async () => {
    stop();
    const requested = dateInput.value;
    const result = await selectAvailableDate([requested], "first");
    if (result === "selected") {
      render();
      announce(`Showing ${requested}`);
    } else {
      dateInput.value = state.date || "";
      if (result === "none") announce(`No published map is available for ${requested}`);
    }
  };
  document.querySelector("#colourSelect").onchange = (event) => {
    state.palette = event.target.value;
    render();
  };
  document.querySelector("#vectorsToggle").onchange = (event) => {
    state.vectors = event.target.checked;
    draw();
  };
  document.querySelector("#hourInput").oninput = (event) => {
    state.frame = Number(event.target.value);
    render();
  };
  document.querySelector("#play").onclick = () => (state.playing ? stop() : play());
  document.querySelector("#previous").onclick = async () => { await step(-1); };
  document.querySelector("#next").onclick = async () => { await step(1); };
  document.querySelector("#resetTime").onclick = async () => {
    stop();
    const result = await selectAvailableDate(publishedDates(), "first");
    if (result === "selected") {
      render();
      announce("Showing the first published hour");
    } else if (result === "none") {
      announce("No published map days are available");
    }
  };
  document.querySelector("#zoomIn").onclick = () => zoom(1.35);
  document.querySelector("#zoomOut").onclick = () => zoom(1 / 1.35);
  document.querySelector("#resetMap").onclick = () => {
    state.view = {zoom: 1, dx: 0, dy: 0};
    draw();
  };
  const canvas = document.querySelector("#mapCanvas");
  canvas.onwheel = (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
  };
  canvas.onpointerdown = (event) => {
    canvas.setPointerCapture(event.pointerId);
    state.drag = {x: event.clientX, y: event.clientY, dx: state.view.dx, dy: state.view.dy};
  };
  canvas.onpointermove = (event) => {
    if (!state.drag) return;
    state.view.dx = state.drag.dx + event.clientX - state.drag.x;
    state.view.dy = state.drag.dy + event.clientY - state.drag.y;
    draw();
  };
  canvas.onpointerup = canvas.onpointercancel = () => { state.drag = null; };
}

function current() {
  return state.day && state.day.frames[state.frame];
}

function render() {
  const frame = current();
  const frames = state.day ? state.day.frames : [];
  const slider = document.querySelector("#hourInput");
  slider.max = Math.max(0, frames.length - 1);
  slider.value = state.frame;
  const timestamp = frame
    ? new Intl.DateTimeFormat("en-GB", {dateStyle: "medium", timeStyle: "short", timeZone: "UTC"}).format(new Date(frame.time_utc)) + " UTC"
    : "No frame";
  document.querySelector("#hourOutput").textContent = frame
    ? new Date(frame.time_utc).toISOString().slice(11, 16)
    : "--";
  document.querySelector("#timestamp").textContent = timestamp;
  const values = frame
    ? (frame.radars || []).map((radar) => radar.activity_index).filter(Number.isFinite)
    : [];
  const mean = values.length ? values.reduce((left, right) => left + right, 0) / values.length : null;
  document.querySelector("#networkValue").textContent = mean === null
    ? "No reporting radars"
    : `${mean.toFixed(0)} relative activity index`;
  document.querySelector("#statusBadge").textContent = "Relative research product";
  document.querySelector("#subtitle").textContent = `${frame ? (frame.radars || []).length : 0} reporting radars · activity normalised separately at every radar`;
  document.querySelector("#coverage").textContent = `${manifestDate("first_time_utc") || "unknown"} to ${manifestDate("latest_time_utc") || "unknown"} · ${state.manifest.radar_count} European reporting radars`;
  document.querySelector("#legendRamp").style.background = `linear-gradient(to top,${PALETTES[state.palette].join(",")})`;
  announce(frame
    ? `${timestamp}. ${values.length} reporting radar${values.length === 1 ? "" : "s"} with relative activity data.`
    : "No relative-activity frame is available.");
  draw();
}

function draw() {
  const canvas = document.querySelector("#mapCanvas");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(3, devicePixelRatio || 1);
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = "#050807";
  context.fillRect(0, 0, rect.width, rect.height);
  const project = projection(rect.width, rect.height);
  graticule(context, project);
  relativeField(context, project);
  boundary(context, project);
  radars(context, project);
}

function projection(width, height) {
  const bounds = state.manifest.map.bounds;
  const padding = 46;
  const base = Math.min(
    (width - padding * 2) / (bounds.east - bounds.west),
    (height - padding * 2) / (bounds.north - bounds.south),
  );
  const scale = base * state.view.zoom;
  const centreLongitude = (bounds.west + bounds.east) / 2;
  const centreLatitude = (bounds.south + bounds.north) / 2;
  return (longitude, latitude) => [
    width / 2 + (longitude - centreLongitude) * scale + state.view.dx,
    height / 2 - (latitude - centreLatitude) * scale + state.view.dy,
  ];
}

function graticule(context, project) {
  const bounds = state.manifest.map.bounds;
  context.strokeStyle = "#344039";
  context.lineWidth = .65;
  context.beginPath();
  for (let longitude = Math.floor(bounds.west / 5) * 5; longitude <= bounds.east + 5; longitude += 5) {
    const start = project(longitude, bounds.south - 3);
    const end = project(longitude, bounds.north + 3);
    context.moveTo(...start);
    context.lineTo(...end);
  }
  for (let latitude = Math.floor(bounds.south / 5) * 5; latitude <= bounds.north + 5; latitude += 5) {
    const start = project(bounds.west - 3, latitude);
    const end = project(bounds.east + 3, latitude);
    context.moveTo(...start);
    context.lineTo(...end);
  }
  context.stroke();
}

function rings(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  if (geometry.type === "LineString") return [geometry.coordinates];
  if (geometry.type === "MultiLineString") return geometry.coordinates;
  return [];
}

function boundary(context, project) {
  if (!state.boundaries) return;
  context.strokeStyle = "#dce5df";
  context.lineWidth = 1.05;
  for (const feature of state.boundaries.features || []) {
    for (const ring of rings(feature.geometry)) {
      context.beginPath();
      ring.forEach(([longitude, latitude], index) => {
        if (index) context.lineTo(...project(longitude, latitude));
        else context.moveTo(...project(longitude, latitude));
      });
      context.stroke();
    }
  }
}

function haversine(latitudeA, longitudeA, latitudeB, longitudeB) {
  const radians = Math.PI / 180;
  const latitudeDelta = (latitudeB - latitudeA) * radians;
  const longitudeDelta = (longitudeB - longitudeA) * radians;
  const fraction = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(latitudeA * radians) * Math.cos(latitudeB * radians)
    * Math.sin(longitudeDelta / 2) ** 2;
  return 6371.0088 * 2 * Math.atan2(Math.sqrt(fraction), Math.sqrt(1 - fraction));
}

function grid() {
  if (state.grid) return state.grid;
  const map = state.manifest.map;
  const bounds = map.bounds;
  const metadata = state.manifest.radars || [];
  const cells = [];
  for (let latitude = Math.ceil(bounds.south / map.resolution_degrees) * map.resolution_degrees; latitude <= bounds.north; latitude += map.resolution_degrees) {
    for (let longitude = Math.ceil(bounds.west / map.resolution_degrees) * map.resolution_degrees; longitude <= bounds.east; longitude += map.resolution_degrees) {
      const nearby = metadata.map((radar) => ({
        radar: radar.radar,
        distance: haversine(latitude, longitude, Number(radar.latitude), Number(radar.longitude)),
      })).filter((radar) => radar.distance <= map.support_radius_km);
      if (nearby.length) cells.push({longitude, latitude, nearby});
    }
  }
  state.grid = cells;
  return state.grid;
}

function relativeField(context, project) {
  const frame = current();
  if (!frame) return;
  const observations = new Map((frame.radars || []).map((radar) => [radar.radar, radar]));
  const stepSize = state.manifest.map.resolution_degrees;
  for (const cell of grid()) {
    let weight = 0;
    let activity = 0;
    let u = 0;
    let v = 0;
    let flowWeight = 0;
    let nearest = Infinity;
    for (const near of cell.nearby) {
      const observation = observations.get(near.radar);
      if (!observation || !Number.isFinite(observation.activity_index)) continue;
      const observationWeight = 1 / Math.max(25, near.distance) ** 2;
      weight += observationWeight;
      activity += observationWeight * observation.activity_index;
      nearest = Math.min(nearest, near.distance);
      if (Number.isFinite(observation.bird_u_ms) && Number.isFinite(observation.bird_v_ms)) {
        const vectorWeight = observationWeight * (.2 + observation.activity_index / 100);
        u += vectorWeight * observation.bird_u_ms;
        v += vectorWeight * observation.bird_v_ms;
        flowWeight += vectorWeight;
      }
    }
    if (!weight) continue;
    activity /= weight;
    const palettePosition = Math.max(0, Math.min(1, activity / 100));
    const northWest = project(cell.longitude - stepSize / 2, cell.latitude + stepSize / 2);
    const southEast = project(cell.longitude + stepSize / 2, cell.latitude - stepSize / 2);
    context.globalAlpha = .22 + .68 * Math.max(0, 1 - nearest / state.manifest.map.support_radius_km);
    context.fillStyle = color(PALETTES[state.palette], palettePosition);
    context.fillRect(
      northWest[0],
      northWest[1],
      southEast[0] - northWest[0] + 1,
      southEast[1] - northWest[1] + 1,
    );
    if (state.vectors && flowWeight && activity >= 10
      && Math.round(cell.longitude * 4 + cell.latitude * 4) % 4 === 0) {
      arrow(
        context,
        (northWest[0] + southEast[0]) / 2,
        (northWest[1] + southEast[1]) / 2,
        u / flowWeight,
        v / flowWeight,
      );
    }
  }
  context.globalAlpha = 1;
}

function radars(context, project) {
  for (const radar of state.manifest.radars || []) {
    const longitude = Number(radar.longitude);
    const latitude = Number(radar.latitude);
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) continue;
    const [x, y] = project(longitude, latitude);
    context.fillStyle = "#d7f4ea";
    context.strokeStyle = "#0b1512";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x, y, 3.4, 0, Math.PI * 2);
    context.fill();
    context.stroke();
  }
}

function arrow(context, x, y, u, v) {
  const speed = Math.hypot(u, v);
  if (speed < .2) return;
  const length = 13;
  const angle = Math.atan2(-v, u);
  const endX = x + Math.cos(angle) * length;
  const endY = y + Math.sin(angle) * length;
  context.strokeStyle = "#020303";
  context.fillStyle = "#020303";
  context.lineWidth = 1.6;
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(endX, endY);
  context.stroke();
  context.beginPath();
  context.moveTo(endX, endY);
  context.lineTo(endX - 5 * Math.cos(angle - .55), endY - 5 * Math.sin(angle - .55));
  context.lineTo(endX - 5 * Math.cos(angle + .55), endY - 5 * Math.sin(angle + .55));
  context.closePath();
  context.fill();
}

function color(palette, position) {
  const scaled = Math.max(0, Math.min(1, position)) * (palette.length - 1);
  const index = Math.min(palette.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  const start = rgb(palette[index]);
  const end = rgb(palette[index + 1]);
  return `rgb(${start.map((value, channel) => Math.round(value + (end[channel] - value) * fraction)).join(",")})`;
}

function rgb(hex) {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function zoom(factor) {
  state.view.zoom = Math.max(.65, Math.min(8, state.view.zoom * factor));
  draw();
}

function adjacentDates(direction) {
  const dates = publishedDates();
  return direction > 0
    ? dates.filter((date) => date > state.date)
    : dates.filter((date) => date < state.date).reverse();
}

async function move(direction) {
  const frames = state.day && Array.isArray(state.day.frames) ? state.day.frames : [];
  const next = state.frame + direction;
  if (next >= 0 && next < frames.length) {
    state.frame = next;
    return "selected";
  }
  return selectAvailableDate(adjacentDates(direction), direction > 0 ? "first" : "last");
}

async function step(direction) {
  stop();
  const result = await move(direction);
  if (result === "selected") {
    render();
    announce(direction > 0 ? "Showing the next published hour" : "Showing the previous published hour");
  } else if (result === "none") {
    announce(direction > 0
      ? "You have reached the last published hour"
      : "You have reached the first published hour");
  }
}

function play() {
  if (state.loading || state.playing) return;
  state.playing = true;
  const generation = ++state.animationGeneration;
  updatePlayButton();
  state.timer = setTimeout(() => playTick(generation), 650);
}

async function playTick(generation) {
  state.timer = null;
  if (!state.playing || generation !== state.animationGeneration) return;
  const result = await move(1);
  if (!state.playing || generation !== state.animationGeneration) return;
  if (result !== "selected") {
    stop();
    if (result === "none") announce("Playback stopped at the last published hour");
    return;
  }
  render();
  state.timer = setTimeout(() => playTick(generation), 650);
}

function stop() {
  state.playing = false;
  state.animationGeneration += 1;
  if (state.timer !== null) clearTimeout(state.timer);
  state.timer = null;
  if (state.loadController) {
    state.loadController.abort();
    state.loadController = null;
    state.loadGeneration += 1;
    setLoading(false);
  }
  updatePlayButton();
}

function updatePlayButton() {
  const button = document.querySelector("#play");
  if (!button) return;
  button.textContent = state.playing ? "Pause" : "Play";
  button.title = state.playing ? "Pause animation" : "Play hourly animation";
  button.setAttribute("aria-label", button.title);
  button.setAttribute("aria-pressed", String(state.playing));
}

function setLoading(loading) {
  state.loading = loading;
  const canvas = document.querySelector("#mapCanvas");
  canvas.setAttribute("aria-busy", String(loading));
  for (const id of ["dateInput", "resetTime", "previous", "next", "hourInput"]) {
    const control = document.querySelector(`#${id}`);
    if (control) control.disabled = loading;
  }
  const playButton = document.querySelector("#play");
  if (playButton) playButton.disabled = loading && !state.playing;
  if (loading) {
    document.querySelector("#statusBadge").textContent = "Loading map day";
    announce("Loading published relative-activity map data");
  } else if (state.manifest && state.manifest.data_available) {
    document.querySelector("#statusBadge").textContent = "Relative research product";
  }
}

function announce(message) {
  const status = document.querySelector("#mapStatus");
  if (status) status.textContent = message;
}

function unavailable(message) {
  stop();
  document.querySelector("#statusBadge").textContent = "Unavailable";
  document.querySelector("#subtitle").textContent = message || "Relative European analysis has not been published";
  document.querySelector("#mapCanvas").setAttribute("aria-busy", "false");
  document.querySelectorAll("input, select, button").forEach((element) => { element.disabled = true; });
  announce(message || "Relative European analysis is not available");
}
