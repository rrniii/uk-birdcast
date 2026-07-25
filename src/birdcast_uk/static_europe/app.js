"use strict";

const PALETTES = {
  robin:["#133e43","#58a89b","#d4d99b","#f0b24d","#cf5138"],
  night:["#111827","#253b80","#497ed2","#9c85d8","#f3c4f1"],
  atlantic:["#061a2d","#0a4d68","#168aad","#52b69a","#d9ed92"],
  thermal:["#1d3557","#457b9d","#a8dadc","#f4a261","#e63946"],
  viridis:["#440154","#3b528b","#21918c","#5ec962","#fde725"]
};
const METRICS = {
  mtr_birds_km_h:{title:"Migration traffic rate",unit:"birds km⁻¹ h⁻¹ · log scale",ticks:[0.1,0.5,1,2,5,10,20,50,100]},
  vid_birds_per_km2:{title:"Vertically integrated density",unit:"birds km⁻² · log scale",ticks:[0.1,0.5,1,2,5,10,20,50,100]}
};
const state={base:"/birdcast-euro/data",manifest:null,grid:null,radars:[],day:null,frame:0,date:null,metric:"mtr_birds_km_h",palette:"robin",vectors:true,uncertainty:false,support:true,playing:false,timer:null,view:{zoom:1,dx:0,dy:0},drag:null,boundaries:null};

init();
async function init(){
  const config=await fetchJson("config.json",{}); state.base=config.data_base_url||state.base;
  state.manifest=await fetchJson(`${state.base}/latest/reanalysis.json`,null);
  state.boundaries=await fetchJson("regional-boundaries.geojson",null);
  if(!state.manifest||!state.manifest.data_available){unavailable();return}
  state.grid=await fetchJson(asset(state.manifest.assets.grid),null);
  state.radars=state.manifest.assets.radars?(await fetchJson(asset(state.manifest.assets.radars),{})).radars||[]:[];
  state.date=state.manifest.latest_time_utc.slice(0,10);
  configure(); await loadDay(); render();
  addEventListener("resize",draw);
}
async function fetchJson(url,fallback){try{const r=await fetch(url,{cache:"no-store"});if(!r.ok)throw Error(r.status);return await r.json()}catch(_){return fallback}}
function asset(path){return path&&path.startsWith("http")?path:`${state.base}/${String(path).replace(/^\//,"")}`}
function dailyUrl(date){return asset(state.manifest.assets.daily_template.replace("{date}",date))}
async function loadDay(){state.day=await fetchJson(dailyUrl(state.date),{frames:[]});state.frame=Math.min(state.frame,Math.max(0,state.day.frames.length-1))}
function configure(){
  const date=document.querySelector("#dateInput");date.min=state.manifest.first_time_utc.slice(0,10);date.max=state.manifest.latest_time_utc.slice(0,10);date.value=state.date;
  date.onchange=async()=>{stop();state.date=date.value;state.frame=0;await loadDay();render()};
  document.querySelector("#metricSelect").onchange=e=>{state.metric=e.target.value;render()};
  document.querySelector("#colourSelect").onchange=e=>{state.palette=e.target.value;render()};
  document.querySelector("#vectorsToggle").onchange=e=>{state.vectors=e.target.checked;draw()};
  document.querySelector("#uncertaintyToggle").onchange=e=>{state.uncertainty=e.target.checked;draw()};
  document.querySelector("#supportToggle").onchange=e=>{state.support=e.target.checked;draw()};
  document.querySelector("#hourInput").oninput=e=>{state.frame=+e.target.value;render()};
  document.querySelector("#play").onclick=()=>state.playing?stop():play();
  document.querySelector("#previous").onclick=()=>step(-1);document.querySelector("#next").onclick=()=>step(1);
  document.querySelector("#resetTime").onclick=async()=>{stop();state.date=state.manifest.first_time_utc.slice(0,10);state.frame=0;document.querySelector("#dateInput").value=state.date;await loadDay();render()};
  document.querySelector("#zoomIn").onclick=()=>zoom(1.35);document.querySelector("#zoomOut").onclick=()=>zoom(1/1.35);document.querySelector("#resetMap").onclick=()=>{state.view={zoom:1,dx:0,dy:0};draw()};
  const canvas=document.querySelector("#mapCanvas");canvas.onwheel=e=>{e.preventDefault();zoom(e.deltaY<0?1.15:1/1.15)};
  canvas.onpointerdown=e=>{canvas.setPointerCapture(e.pointerId);state.drag={x:e.clientX,y:e.clientY,dx:state.view.dx,dy:state.view.dy}};
  canvas.onpointermove=e=>{if(state.drag){state.view.dx=state.drag.dx+e.clientX-state.drag.x;state.view.dy=state.drag.dy+e.clientY-state.drag.y;draw()}};
  canvas.onpointerup=canvas.onpointercancel=()=>{state.drag=null};
}
function frame(){return state.day&&state.day.frames[state.frame]}
function render(){
  const f=frame(),frames=state.day?state.day.frames:[];
  const slider=document.querySelector("#hourInput");slider.max=Math.max(0,frames.length-1);slider.value=state.frame;
  document.querySelector("#hourOutput").textContent=f?new Date(f.time_utc).toISOString().slice(11,16):"--";
  document.querySelector("#timestamp").textContent=f?new Intl.DateTimeFormat("en-GB",{dateStyle:"medium",timeStyle:"short",timeZone:"UTC"}).format(new Date(f.time_utc))+" UTC":"No frame";
  const values=f?(f[state.metric]||[]).filter(Number.isFinite):[];
  document.querySelector("#meanValue").textContent=values.length?`${mean(values).toFixed(1)} ${METRICS[state.metric].unit.split(" · ")[0]}`:"No supported cells";
  document.querySelector("#legendTitle").textContent=METRICS[state.metric].title;document.querySelector("#legendUnit").textContent=METRICS[state.metric].unit;
  document.querySelector("#statusBadge").textContent=state.manifest.release_status.replace("-"," ");
  document.querySelector("#coverage").textContent=`${state.manifest.first_time_utc.slice(0,10)} to ${state.manifest.latest_time_utc.slice(0,10)} · ${state.manifest.aloft_radar_count} Aloft + ${state.manifest.uk_sp_radar_count} UK SP radars`;
  document.querySelector("#model").textContent=`${state.manifest.model_id} · ${state.manifest.reference_scale} scale`;
  legend();draw();
}
function legend(){
  const metric=METRICS[state.metric],ramp=document.querySelector("#legendRamp");ramp.style.background=`linear-gradient(to top,${PALETTES[state.palette].join(",")})`;
  const ticks=document.querySelector("#legendTicks");ticks.innerHTML="";const lo=Math.log10(metric.ticks[0]),hi=Math.log10(metric.ticks.at(-1));
  metric.ticks.forEach(v=>{const li=document.createElement("li");li.textContent=v;li.style.bottom=`${100*(Math.log10(v)-lo)/(hi-lo)}%`;ticks.append(li)});
}
function draw(){
  if(!state.grid)return;const canvas=document.querySelector("#mapCanvas"),rect=canvas.getBoundingClientRect(),ratio=Math.min(2,devicePixelRatio||1);
  canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);const c=canvas.getContext("2d");c.setTransform(ratio,0,0,ratio,0,0);c.clearRect(0,0,rect.width,rect.height);
  const project=projection(rect.width,rect.height);gridLines(c,project,rect);boundaries(c,project);cells(c,project);radars(c,project);
}
function projection(width,height){
  const bounds={west:-25,east:45,south:33,north:72},pad=45,base=Math.min((width-pad*2)/(bounds.east-bounds.west),(height-pad*2)/(bounds.north-bounds.south)),scale=base*state.view.zoom;
  const cx=(bounds.west+bounds.east)/2,cy=(bounds.south+bounds.north)/2;
  return(lon,lat)=>[width/2+(lon-cx)*scale+state.view.dx,height/2-(lat-cy)*scale+state.view.dy];
}
function gridLines(c,p,r){c.strokeStyle="#344039";c.lineWidth=.65;c.beginPath();for(let lon=-30;lon<=50;lon+=5){const a=p(lon,30),b=p(lon,75);c.moveTo(...a);c.lineTo(...b)}for(let lat=30;lat<=75;lat+=5){const a=p(-30,lat),b=p(50,lat);c.moveTo(...a);c.lineTo(...b)}c.stroke()}
function boundaries(c,p){if(!state.boundaries)return;c.strokeStyle="#dce5df";c.lineWidth=1.15;for(const f of state.boundaries.features||[]){for(const ring of rings(f.geometry)){c.beginPath();ring.forEach(([x,y],i)=>i?c.lineTo(...p(x,y)):c.moveTo(...p(x,y)));c.stroke()}}}
function rings(g){if(!g)return[];if(g.type==="Polygon")return g.coordinates;if(g.type==="MultiPolygon")return g.coordinates.flat();if(g.type==="LineString")return[g.coordinates];if(g.type==="MultiLineString")return g.coordinates;return[]}
function cells(c,p){
  const f=frame();if(!f)return;const values=f[state.metric]||[],ticks=METRICS[state.metric].ticks,lo=Math.log10(ticks[0]),hi=Math.log10(ticks.at(-1)),palette=PALETTES[state.palette];
  const a=p(0,0),b=p(.25,.25),w=Math.max(2,Math.abs(b[0]-a[0])+1),h=Math.max(2,Math.abs(b[1]-a[1])+1);
  state.grid.cells.forEach((cell,i)=>{const v=values[i];if(!Number.isFinite(v)||v<ticks[0])return;const t=clamp((Math.log10(v)-lo)/(hi-lo));const [x,y]=p(cell.longitude,cell.latitude);let alpha=.88;
    if(state.uncertainty&&Number.isFinite(f.uncertainty[i]))alpha*=1-clamp(f.uncertainty[i]/3)*.65;c.globalAlpha=alpha;c.fillStyle=color(palette,t);c.fillRect(x-w/2,y-h/2,w,h);
    if(state.support&&f.support[i]==="extrapolation"){c.strokeStyle="#f0b343";c.lineWidth=.45;c.strokeRect(x-w/2,y-h/2,w,h)}
    if(state.vectors&&Number.isFinite(f.bird_u_ms[i])&&Number.isFinite(f.bird_v_ms[i]))arrow(c,x,y,f.bird_u_ms[i],f.bird_v_ms[i]);
  });c.globalAlpha=1;
}
function arrow(c,x,y,u,v){const speed=Math.hypot(u,v);if(speed<.25)return;const len=Math.min(15,5+speed*.55),angle=Math.atan2(u,-v),ex=x+Math.sin(angle)*len,ey=y-Math.cos(angle)*len;c.strokeStyle="#050706";c.fillStyle="#050706";c.lineWidth=1.4;c.beginPath();c.moveTo(x,y);c.lineTo(ex,ey);c.stroke();c.beginPath();c.moveTo(ex,ey);c.lineTo(ex-4*Math.sin(angle-.65),ey+4*Math.cos(angle-.65));c.lineTo(ex-4*Math.sin(angle+.65),ey+4*Math.cos(angle+.65));c.closePath();c.fill()}
function radars(c,p){for(const r of state.radars){const lon=+(r.longitude??r.lon),lat=+(r.latitude??r.lat);if(!Number.isFinite(lon)||!Number.isFinite(lat))continue;const[x,y]=p(lon,lat);c.strokeStyle="#43ee7a";c.lineWidth=1.2;c.beginPath();c.ellipse(x,y,5,2.4,-.45,0,Math.PI*2);c.stroke();c.beginPath();c.moveTo(x+2,y-2);c.lineTo(x+5,y-6);c.stroke();c.beginPath();c.arc(x+5,y-6,1.6,0,Math.PI*2);c.fillStyle="#43ee7a";c.fill()}}
function color(p,t){const x=clamp(t)*(p.length-1),i=Math.min(p.length-2,Math.floor(x)),f=x-i,a=rgb(p[i]),b=rgb(p[i+1]);return`rgb(${a.map((v,j)=>Math.round(v+(b[j]-v)*f)).join(",")})`}
function rgb(h){return[parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)]}function clamp(v){return Math.max(0,Math.min(1,v))}function mean(a){return a.reduce((x,y)=>x+y,0)/a.length}
function zoom(f){state.view.zoom=clampZoom(state.view.zoom*f);draw()}function clampZoom(v){return Math.max(.65,Math.min(8,v))}
async function step(delta){stop();await move(delta);render()}async function move(delta){let n=state.frame+delta;if(n>=0&&n<(state.day.frames||[]).length){state.frame=n;return}const d=new Date(`${state.date}T00:00:00Z`);d.setUTCDate(d.getUTCDate()+(delta>0?1:-1));const next=d.toISOString().slice(0,10),min=state.manifest.first_time_utc.slice(0,10),max=state.manifest.latest_time_utc.slice(0,10);if(next<min||next>max)return;state.date=next;document.querySelector("#dateInput").value=next;await loadDay();state.frame=delta>0?0:Math.max(0,state.day.frames.length-1)}
function play(){state.playing=true;document.querySelector("#play").textContent="Ⅱ";state.timer=setInterval(async()=>{await move(1);render()},650)}function stop(){state.playing=false;clearInterval(state.timer);state.timer=null;document.querySelector("#play").textContent="▶"}
function unavailable(){document.querySelector("#statusBadge").textContent="Validation pending";document.querySelector("#subtitle").textContent="European reanalysis withheld pending independent model validation";document.querySelector("#coverage").textContent="Source, ERA5, training and grid reconstruction audits passed";document.querySelector("#model").textContent="External Aloft transfer validation is required before map data are published";document.querySelectorAll("input,select,button").forEach(e=>e.disabled=true)}
