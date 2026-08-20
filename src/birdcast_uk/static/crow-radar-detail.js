/* CROW UK Bird Maps embed: a raw-VPTS custom element for static dashboards. */
(function () {
  "use strict";
  const QC = { altitudeMin: 200, altitudeMax: 4000, sdVvpMin: 2, rainDbzh: 7, rainFraction: .8 };
  const METRICS = { mtr: ["Migration traffic rate", "birds km-1 h-1"], rtr: ["Reflectivity traffic rate", "cm2 km-1 h-1"], vid: ["Vertically integrated density", "birds km-2"], vir: ["Vertically integrated reflectivity", "cm2 km-2"] };
  const number = (value) => { if (value === null || value === undefined || (typeof value === "string" && !value.trim())) return NaN; const result = Number(value); return Number.isFinite(result) ? result : NaN; };
  const mean = (values) => { const usable = values.filter(Number.isFinite); return usable.length ? usable.reduce((a, b) => a + b, 0) / usable.length : NaN; };
  const day = (value) => value.toISOString().slice(0, 10);
  const plusDays = (value, offset) => { const result = new Date(value.getTime()); result.setUTCDate(result.getUTCDate() + offset); return result; };
  const urlFor = (template, radar, pulse, value) => template.replaceAll("{radar}", encodeURIComponent(radar)).replaceAll("{pulse}", encodeURIComponent(pulse)).replaceAll("{yyyy}", String(value.getUTCFullYear())).replaceAll("{yyyymmdd}", day(value).replaceAll("-", ""));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const formatAxisHour = (timestamp) => `${String(new Date(timestamp).getUTCHours()).padStart(2, "0")}:00`;
  const formatAxisDate = (timestamp) => { const value = new Date(timestamp); return `${value.getUTCDate()} ${MONTHS[value.getUTCMonth()]}`; };
  function drawTimeAxis(ctx, width, height, margin, first, last, intervalHours) {
    const step = (intervalHours === 72 ? 6 : 3) * 60 * 60 * 1000;
    const plotWidth = width - margin.l - margin.r;
    const axisY = height - margin.b;
    const firstTick = Math.ceil(first / step) * step;
    ctx.save();
    ctx.font = "10px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let timestamp = firstTick; timestamp < last; timestamp += step) {
      const value = new Date(timestamp);
      const x = margin.l + (timestamp - first) / (last - first) * plotWidth;
      ctx.strokeStyle = value.getUTCHours() === 0 ? "#536159" : "#29332d";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, margin.t);
      ctx.lineTo(x, axisY);
      ctx.stroke();
      ctx.fillStyle = "#bdc8c0";
      ctx.fillText(formatAxisHour(timestamp), x, axisY + 5);
      if (value.getUTCHours() === 0) {
        ctx.fillStyle = "#87958d";
        ctx.fillText(formatAxisDate(timestamp), x, axisY + 19);
      }
    }
    ctx.strokeStyle = "#536159";
    ctx.beginPath();
    ctx.moveTo(margin.l, axisY);
    ctx.lineTo(width - margin.r, axisY);
    ctx.stroke();
    ctx.fillStyle = "#87958d";
    ctx.textAlign = "right";
    ctx.fillText("UTC", width - margin.r, axisY + 19);
    ctx.restore();
  }
  function rowsFromCsv(text) { const lines = text.trim().split(/\r?\n/); const headers = lines.shift().split(",").map((header) => header.replace(/"/g, "").trim()); return lines.map((line) => Object.fromEntries(headers.map((header, index) => [header, (line.split(",")[index] || "").replace(/"/g, "").trim()]))); }
  function profileMetrics(rows) {
    const valid = rows.filter((row) => row.sdVvp >= QC.sdVvpMin && row.height >= QC.altitudeMin && row.height <= QC.altitudeMax && !row.gap);
    if (!valid.length) return { mtr: NaN, rtr: NaN, vid: NaN, vir: NaN };
    const heights = [...new Set(valid.map((row) => row.height))].sort((a, b) => a - b);
    const widthKm = (heights.length > 1 ? mean(heights.slice(1).map((height, index) => height - heights[index])) : 200) / 1000;
    const low = valid.filter((row) => row.height <= 2000); const rain = low.filter((row) => row.dbzh >= QC.rainDbzh);
    const integrate = (key) => {
      const values = valid.map((row) => row[key]).filter(Number.isFinite);
      return values.length ? values.reduce((total, value) => total + value, 0) * widthKm : NaN;
    };
    const traffic = (key) => {
      const pairs = valid.filter((row) => Number.isFinite(row.ff) && Number.isFinite(row[key]));
      return pairs.length ? pairs.reduce((total, row) => total + row.ff * row[key] * 3.6, 0) * widthKm : NaN;
    };
    const sums = { vid: integrate("dens"), vir: integrate("eta") };
    if (rain.length >= 3 && rain.length / Math.max(low.length, 1) >= QC.rainFraction) return { mtr: NaN, rtr: NaN, ...sums };
    return { mtr: traffic("dens"), rtr: traffic("eta"), ...sums };
  }
  class CrowRadarDetail extends HTMLElement {
    static get observedAttributes() { return ["radar", "radar-label", "date", "pulse", "interval-hours", "object-url-template"]; }
    constructor() { super(); this.attachShadow({ mode: "open" }); this.metric = "mtr"; this.data = null; this.request = 0; this.loadQueued = false; this.abortController = null; }
    connectedCallback() { this.queueLoad(); }
    disconnectedCallback() { if (this.abortController) this.abortController.abort(); }
    attributeChangedCallback() { if (this.isConnected) this.queueLoad(); }
    queueLoad() {
      if (this.loadQueued) return;
      this.loadQueued = true;
      queueMicrotask(() => {
        this.loadQueued = false;
        if (this.isConnected) this.load();
      });
    }
    interval() { return Number(this.getAttribute("interval-hours")) === 72 ? 72 : 24; }
    render(message, error) {
      const label = this.getAttribute("radar-label") || this.getAttribute("radar") || "Radar"; const interval = this.interval();
      this.shadowRoot.innerHTML = `<style>:host{display:block;color:#e9efeb;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.box{border:1px solid #34403a;background:#0d1210}.head{display:flex;justify-content:space-between;align-items:start;flex-wrap:wrap;gap:9px;padding:12px 14px;border-bottom:1px solid #34403a}.title{font-weight:700;font-size:15px}.meta,.note{color:#aeb9b2;font-size:11px}.tools{display:flex;gap:6px}.metric-control{display:block}button,select{padding:6px 8px;background:#151c18;color:#edf4ef;border:1px solid #526159;font:inherit;font-size:11px}button.active{background:#e0a62e;color:#111;border-color:#e0a62e}button:focus-visible,select:focus-visible{outline:3px solid #75d5df;outline-offset:2px}.status{padding:11px 14px;color:#bdc8c0;font-size:12px}.status.error{color:#ffb1a6}.chart{padding:10px 14px 14px}.chart[hidden]{display:none}.chart h4{margin:0 0 6px;font-size:12px}.canvas-wrap{overflow-x:auto}canvas{display:block;width:100%;min-width:540px;background:#080c0b;border:1px solid #263029}.note{padding:0 14px 13px}.visually-hidden{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}@media(max-width:650px){.tools{width:100%;justify-content:space-between}}</style><section class="box" aria-labelledby="detail-title"><header class="head"><div><div id="detail-title" class="title"></div><div class="meta"></div></div><div class="tools"><button type="button" data-i="24">1 day</button><button type="button" data-i="72">3 days</button><label class="metric-control"><span class="visually-hidden">Profile metric</span><select></select></label></div></header><div class="status" role="status" aria-live="polite" aria-atomic="true"></div><div class="chart" hidden><h4></h4><div class="canvas-wrap"><canvas class="line" height="170" role="img"></canvas></div></div><div class="chart" hidden><h4>Vertical profile density</h4><div class="canvas-wrap"><canvas class="heat" height="240" role="img"></canvas></div></div><p class="note">The raw VPTS archive stays in the Object Store. Rain-contaminated profiles are retained for density but excluded from MTR/RTR.</p></section>`;
      this.shadowRoot.querySelector(".title").textContent = `${label} radar detail`;
      this.shadowRoot.querySelector(".meta").textContent = `CROW metrics · 200–4000 m · ${(this.getAttribute("pulse") || "lp").toUpperCase()} · 10-minute UTC`;
      const status = this.shadowRoot.querySelector(".status");
      status.textContent = String(message || "");
      status.classList.toggle("error", Boolean(error));
      const hasProfiles = Boolean(this.data && this.data.profiles.length);
      this.shadowRoot.querySelectorAll(".chart").forEach((chart) => { chart.hidden = !hasProfiles; });
      this.shadowRoot.querySelector(".chart h4").textContent = `${METRICS[this.metric][0]} (${METRICS[this.metric][1]})`;
      this.shadowRoot.querySelector(".line").setAttribute("aria-label", `${METRICS[this.metric][0]} time series for ${label}`);
      this.shadowRoot.querySelector(".heat").setAttribute("aria-label", `Vertical profile density for ${label}`);
      const select = this.shadowRoot.querySelector("select");
      for (const [key, value] of Object.entries(METRICS)) {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = `${key.toUpperCase()} · ${value[0]}`;
        option.selected = key === this.metric;
        select.append(option);
      }
      select.addEventListener("change", (event) => { this.metric = event.target.value; this.render(message, error); this.draw(); });
      this.shadowRoot.querySelectorAll("button[data-i]").forEach((button) => {
        const active = Number(button.dataset.i) === interval;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
        button.addEventListener("click", () => this.setAttribute("interval-hours", button.dataset.i));
      });
    }
    async load() {
      if (this.abortController) this.abortController.abort();
      const id = ++this.request;
      const radar = this.getAttribute("radar"); const template = this.getAttribute("object-url-template"); const selected = this.getAttribute("date");
      if (!radar || !template || !selected) { this.data = null; this.render("Select a radar and date to load raw VPTS profiles."); return; }
      this.abortController = new AbortController();
      const signal = this.abortController.signal;
      const pulse = this.getAttribute("pulse") || "lp";
      const intervalHours = this.interval();
      this.data = null; this.render("Loading raw VPTS profiles from the Object Store…"); this.dispatchEvent(new CustomEvent("crow-loading", { bubbles: true }));
      const centre = new Date(`${selected}T00:00:00Z`);
      const first = intervalHours === 72 ? plusDays(centre, -1) : centre;
      const days = Array.from({ length: intervalHours / 24 }, (_, index) => plusDays(first, index));
      try {
        const text = await Promise.all(days.map(async (value) => {
          const response = await fetch(urlFor(template, radar, pulse, value), {signal});
          if (response.status === 404) return "";
          if (!response.ok) throw new Error(`${response.status} for ${day(value)}`);
          return response.text();
        }));
        if (id !== this.request) return;
        this.data = this.normalise(text.flatMap((value) => value ? rowsFromCsv(value) : []));
        this.data.axisFirst = first.getTime();
        this.data.axisLast = plusDays(first, days.length).getTime();
        const message = this.data.profiles.length
          ? `${this.data.profiles.length} ten-minute profiles loaded. ${day(first)} to ${day(plusDays(first, days.length - 1))}.`
          : `No ten-minute profiles are available from ${day(first)} to ${day(plusDays(first, days.length - 1))}.`;
        this.render(message);
        this.draw();
        this.dispatchEvent(new CustomEvent("crow-ready", { detail: { radar, profiles: this.data.profiles.length, intervalHours }, bubbles: true }));
      } catch (error) {
        if (error.name === "AbortError") return;
        if (id === this.request) {
          this.render(`The selected VPTS files could not be loaded: ${error.message}`, true);
          this.dispatchEvent(new CustomEvent("crow-error", { detail: { radar, message: error.message }, bubbles: true }));
        }
      }
    }
    normalise(rows) { const layers = new Map(); rows.forEach((row) => { const timestamp = Math.floor(Date.parse(`${row.datetime.replace(" ", "T")}Z`) / 600000) * 600000; const height = number(row.height); if (!Number.isFinite(timestamp) || !Number.isFinite(height)) return; const key = `${timestamp}:${height}`; const entries = layers.get(key) || []; entries.push({ height, dens:number(row.dens), eta:number(row.eta), ff:number(row.ff), sdVvp:number(row.sd_vvp), dbzh:number(row.DBZH || row.dbz), gap:String(row.gap).toLowerCase() === "true" }); layers.set(key, entries); }); const profiles = new Map(); layers.forEach((entries, key) => { const [timestamp] = key.split(":"); const values = profiles.get(+timestamp) || []; values.push({ height:entries[0].height, dens:mean(entries.map((entry) => entry.dens)), eta:mean(entries.map((entry) => entry.eta)), ff:mean(entries.map((entry) => entry.ff)), sdVvp:mean(entries.map((entry) => entry.sdVvp)), dbzh:mean(entries.map((entry) => entry.dbzh)), gap:entries.some((entry) => entry.gap) }); profiles.set(+timestamp, values); }); const list = [...profiles.entries()].sort(([a], [b]) => a-b).map(([timestamp, values]) => ({timestamp, values, metrics:profileMetrics(values)})); return {profiles:list, first:list[0] && list[0].timestamp, last:list[list.length-1] && list[list.length-1].timestamp}; }
    context(selector) { const canvas = this.shadowRoot.querySelector(selector); const width = Math.max(540, Math.round(canvas.getBoundingClientRect().width || 760)); const height = +canvas.getAttribute("height"); const ratio = Math.min(2, devicePixelRatio || 1); canvas.width = width * ratio; canvas.height = height * ratio; const ctx = canvas.getContext("2d"); ctx.setTransform(ratio,0,0,ratio,0,0); return {ctx,width,height}; }
    draw() { if (!this.data || !this.data.profiles.length) return; const first=this.data.axisFirst ?? this.data.first,last=this.data.axisLast ?? this.data.last ?? first+1,interval=this.interval(); const renderCanvas=(selector,fn)=>{const c=this.context(selector);c.ctx.fillStyle="#080c0b";c.ctx.fillRect(0,0,c.width,c.height);fn(c.ctx,c.width,c.height);}; renderCanvas(".line",(ctx,width,height)=>{const m={l:52,r:12,t:12,b:44},values=this.data.profiles.map((profile)=>profile.metrics[this.metric]).filter(Number.isFinite),maximum=Math.max(...values,1);ctx.strokeStyle="#29332d";for(let i=0;i<4;i+=1){const y=m.t+i*(height-m.t-m.b)/3;ctx.beginPath();ctx.moveTo(m.l,y);ctx.lineTo(width-m.r,y);ctx.stroke();}drawTimeAxis(ctx,width,height,m,first,last,interval);ctx.strokeStyle="#e0a62e";ctx.lineWidth=1.7;ctx.beginPath();let started=false;this.data.profiles.forEach((profile)=>{const value=profile.metrics[this.metric];if(!Number.isFinite(value)){started=false;return;}const x=m.l+(profile.timestamp-first)/(last-first||1)*(width-m.l-m.r),y=height-m.b-value/maximum*(height-m.t-m.b);if(started)ctx.lineTo(x,y);else{ctx.moveTo(x,y);started=true;}});ctx.stroke();ctx.fillStyle="#bdc8c0";ctx.font="10px system-ui";[0,maximum/2,maximum].forEach((value,index)=>ctx.fillText(value.toPrecision(3),4,height-m.b-index*(height-m.t-m.b)/2+3));}); renderCanvas(".heat",(ctx,width,height)=>{const m={l:52,r:12,t:12,b:44},rows=this.data.profiles.flatMap((profile)=>profile.values.map((value)=>({...value,timestamp:profile.timestamp}))).filter((row)=>row.sdVvp>=QC.sdVvpMin&&!row.gap&&row.height>=QC.altitudeMin&&row.height<=QC.altitudeMax&&Number.isFinite(row.dens)),maximum=Math.max(...rows.map((row)=>row.dens),1),cellWidth=Math.max(2,(width-m.l-m.r)/(interval*6)+1);drawTimeAxis(ctx,width,height,m,first,last,interval);rows.forEach((row)=>{const fraction=Math.max(0,Math.min(1,Math.log1p(Math.max(0,row.dens))/Math.log1p(maximum))),x=m.l+(row.timestamp-first)/(last-first||1)*(width-m.l-m.r),y=height-m.b-(row.height-QC.altitudeMin)/(QC.altitudeMax-QC.altitudeMin)*(height-m.t-m.b);ctx.fillStyle=`hsl(${250-190*fraction},80%,${25+40*fraction}%)`;ctx.fillRect(x,y-3,cellWidth,5);});ctx.strokeStyle="#536159";ctx.strokeRect(m.l,m.t,width-m.l-m.r,height-m.t-m.b);ctx.fillStyle="#bdc8c0";ctx.font="10px system-ui";[200,2000,4000].forEach((metres)=>ctx.fillText(`${metres} m`,4,height-m.b-(metres-QC.altitudeMin)/(QC.altitudeMax-QC.altitudeMin)*(height-m.t-m.b)+3));}); }
  }
  if (!customElements.get("crow-radar-detail")) customElements.define("crow-radar-detail", CrowRadarDetail);
}());
