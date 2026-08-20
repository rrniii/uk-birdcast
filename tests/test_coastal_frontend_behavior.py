from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
APP = Path(__file__).parents[1] / "src" / "birdcast_uk" / "static_coastal" / "app.js"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_coastal_day_selection_and_playback_runtime_contract() -> None:
    script = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace("\ninit();\n", "\n");
source += `
globalThis.__coastalTest = {
  state, publishedDates, selectAvailableDate, move, play, playTick
};`;

function makeElement() {
  return {
    attrs: {},
    disabled: false,
    style: {},
    textContent: "",
    title: "",
    value: "",
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; },
    getBoundingClientRect() { return {width: 0, height: 0}; },
  };
}

const elements = new Map();
const document = {
  querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, makeElement());
    return elements.get(selector);
  },
  querySelectorAll() { return []; },
};
const scheduled = [];
class TestAbortController {
  constructor() { this.signal = {aborted: false}; }
  abort() { this.signal.aborted = true; }
}
const context = {
  AbortController: global.AbortController || TestAbortController,
  Date,
  Intl,
  Map,
  Math,
  Number,
  Promise,
  console,
  document,
  fetch: null,
  setTimeout(callback) { scheduled.push(callback); return scheduled.length; },
  clearTimeout() {},
};
vm.createContext(context);
new vm.Script(source, {filename: process.argv[1]}).runInContext(context);
const api = context.__coastalTest;

function manifest(availableDates) {
  return {
    data_available: true,
    first_time_utc: "2026-07-01T00:00:00Z",
    latest_time_utc: "2026-07-03T00:00:00Z",
    available_dates: availableDates,
    assets: {daily_template: "days/{date}.json"},
  };
}
function day(date, hour = "00") {
  return {date, frames: [{time_utc: `${date}T${hour}:00:00Z`, radars: []}]};
}
function response(payload) {
  return {status: 200, ok: true, async json() { return payload; }};
}

(async () => {
  api.state.base = "/data";
  api.state.manifest = manifest(["2026-07-01", "2026-07-03"]);
  api.state.date = "2026-07-01";
  api.state.day = day("2026-07-01");

  const deferred = new Map();
  context.fetch = (url) => new Promise((resolve) => deferred.set(url, resolve));
  const older = api.selectAvailableDate(["2026-07-01"], "first");
  const newer = api.selectAvailableDate(["2026-07-03"], "first");
  deferred.get("/data/days/2026-07-03.json")(response(day("2026-07-03")));
  assert.strictEqual(await newer, "selected");
  deferred.get("/data/days/2026-07-01.json")(response(day("2026-07-01", "12")));
  assert.strictEqual(await older, "cancelled");
  assert.strictEqual(api.state.date, "2026-07-03");
  assert.strictEqual(document.querySelector("#dateInput").value, "2026-07-03");
  assert.strictEqual(document.querySelector("#mapCanvas").attrs["aria-busy"], "false");

  api.state.dayCache.clear();
  api.state.dayCache.set("2026-07-01", day("2026-07-01"));
  api.state.dayCache.set("2026-07-03", day("2026-07-03"));
  context.fetch = () => { throw new Error("sparse navigation fetched an undeclared date"); };
  api.state.date = "2026-07-01";
  api.state.day = api.state.dayCache.get("2026-07-01");
  api.state.frame = 0;
  assert.strictEqual(await api.move(1), "selected");
  assert.strictEqual(api.state.date, "2026-07-03");
  assert.strictEqual(await api.move(-1), "selected");
  assert.strictEqual(api.state.date, "2026-07-01");

  delete api.state.manifest.available_dates;
  assert.strictEqual(
    JSON.stringify(api.publishedDates()),
    JSON.stringify(["2026-07-01", "2026-07-02", "2026-07-03"]),
  );

  api.state.manifest.available_dates = ["2026-07-01", "2026-07-03"];
  api.state.date = "2026-07-03";
  api.state.day = api.state.dayCache.get("2026-07-03");
  api.state.frame = 0;
  api.play();
  assert.strictEqual(scheduled.length, 1);
  assert.strictEqual(document.querySelector("#play").textContent, "Pause");
  assert.strictEqual(document.querySelector("#play").attrs["aria-pressed"], "true");
  await scheduled.shift()();
  assert.strictEqual(api.state.playing, false);
  assert.strictEqual(api.state.timer, null);
  assert.strictEqual(document.querySelector("#play").textContent, "Play");
  assert.strictEqual(document.querySelector("#play").attrs["aria-pressed"], "false");
  assert.match(document.querySelector("#mapStatus").textContent, /last published hour/);
})().then(
  () => process.stdout.write("ok\n"),
  (error) => { console.error(error); process.exitCode = 1; },
);
"""
    result = subprocess.run(
        [NODE, "-e", script, str(APP)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok\n"
