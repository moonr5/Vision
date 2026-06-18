/*
 ============================================================================
   ____                          _     __  __             _ _
  / ___|   _ _ __ _ __ ___ _ __ | |_  |  \/  | ___  _ __ (_) |_ ___  _ __
 | |  | | | | '__| '__/ _ \ '_ \| __| | |\/| |/ _ \| '_ \| | __/ _ \| '__|
 | |__| |_| | |  | | |  __/ | | | |_  | |  | | (_) | | | | | || (_) | |
  \____\__,_|_|  |_|  \___|_| |_|\__| |_|  |_|\___/|_| |_|_|\__\___/|_|
 ============================================================================
   PROJECT   : Current Monitor  (ESP32 + INA226 power monitor)
   AUTHOR    : Ahmed Yousef Saeed Khalifa
   COPYRIGHT : © Ahmed Yousef Saeed Khalifa — All rights reserved.
   INDUSTRY  : Logistics
 ----------------------------------------------------------------------------
   WHAT THIS FILE DOES:
     Reads an INA226 power monitor over I2C about 10 times a second to measure:
       - current : how much the device is drawing (or the solar panel is
                   delivering) through a shunt resistor, in amperes
       - voltage : the bus (battery) voltage, in volts
       - power   : voltage x current, in watts
     It also tracks the running PEAK current and an ACCUMULATED charge in
     amp-hours (by coulomb-counting), keeps a 200-sample live current waveform
     and a 1-minute history log, and streams everything to a live web
     dashboard over a WebSocket. The dashboard can also export the full log
     as a CSV file.

   HARDWARE / WIRING:
     Board  : ESP32 dev board
     Sensor : INA226 high-side current + voltage monitor (I2C address 0x40)
                SDA -> GPIO14
                SCL -> GPIO27
                VCC -> 3.3V
                GND -> GND
     Shunt  : 0.1 ohm resistor across the INA226 IN+ / IN- terminals.
              The current is worked out from the tiny voltage across this
              shunt, so SHUNT_RESISTOR_OHMS below MUST match the real part.

   HOW TO CONNECT (the "inputs"):
     The ESP32 does NOT create its own hotspot. It JOINS the laptop's Windows
     "Mobile hotspot" named below and pins itself to a FIXED IP, so the
     dashboard address never changes:
            Hotspot SSID : Razer16
            Password     : A3695009a
            Dashboard    : http://192.168.137.50   (or http://battery.local)

   WEB ENDPOINTS (served by the ESP32):
     GET  /          -> the dashboard HTML page
     GET  /download  -> the 1-minute history log as a downloadable CSV file
     GET  /histjson  -> history log as JSON (used to backfill after reconnect)
     GET  /wavejson  -> last 200 current samples as JSON (waveform backfill)
     WS   port 81    -> live values pushed ~10 times per second
 ============================================================================
*/

#include <Arduino.h>
#include <Wire.h>
#include <INA226.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WebSocketsServer.h>   // <-- arduinoWebSockets by Markus Sattler


// ============================================================
//  CONFIG — Wi-Fi station (the hotspot this device joins)
// ============================================================
const char* STA_SSID     = "Razer16";
const char* STA_PASSWORD = "A3695009a";
const char* MDNS_HOST    = "battery";          // -> http://battery.local


// ============================================================
//  CONFIG — Fixed (static) IP — never changes, never guess again
// ============================================================
// A Windows laptop "Mobile hotspot" ALWAYS uses the 192.168.137.x
// network with the laptop itself at 192.168.137.1 (hard-coded in
// Windows — it does not change between reboots). So we pin the ESP
// to one address on that network:
//
//        >>>  DASHBOARD IS ALWAYS:  http://192.168.137.50  <<<
//
// If you ever can't reach it: on the laptop run `ipconfig`, find
// the hotspot adapter's IPv4 (almost always 192.168.137.1) and
// make the first three numbers below match it. If 192.168.137.50
// ever clashes with another device, change the last number (50).
IPAddress STA_IP     (192, 168, 137,  50);
IPAddress STA_GATEWAY(192, 168, 137,   1);
IPAddress STA_SUBNET (255, 255, 255,   0);
IPAddress STA_DNS    (192, 168, 137,   1);


// ============================================================
//  CONFIG — I2C bus & INA226 sensor
// ============================================================
#define I2C_SDA 14
#define I2C_SCL 27
INA226 ina(0x40);                          // INA226 at I2C address 0x40
const float SHUNT_RESISTOR_OHMS = 0.1f;    // change to your actual shunt value.
                                           //   Current = shunt voltage / this value,
                                           //   so a wrong number scales every reading.


// ============================================================
//  CONFIG — How often we sample and snapshot
// ============================================================
const uint32_t FAST_SAMPLE_MS   = 100;    // 10 Hz INA226 read -> WebSocket push.
                                          //   Smaller = smoother live graph, more CPU/WiFi.
const uint32_t HISTORY_SAVE_MS  = 60000;  // 1 min snapshot -> CSV history row.


// ============================================================
//  STATE — Latest live measurements
//  (volatile: touched from the main loop and read elsewhere)
// ============================================================
volatile float g_voltage  = 0.0f;
volatile float g_current  = 0.0f;
volatile float g_power    = 0.0f;
volatile float g_peak     = 0.0f;
volatile float g_areaAs   = 0.0f;   // Coulomb counter (amp-seconds); /3600 -> amp-hours


// ============================================================
//  STATE — History vault (one row per minute, up to ~50 h)
// ============================================================
const int MAX_HISTORY = 3000;       // 3000 minutes = 50 hours of 1-min rows
float hVoltage[MAX_HISTORY];
float hCurrent[MAX_HISTORY];
float hPower[MAX_HISTORY];
float hAh[MAX_HISTORY];
float hPeak[MAX_HISTORY];
int   historyCount = 0;


// ============================================================
//  STATE — Waveform ring buffer (last 200 fast samples)
// ============================================================
// Lets a reconnecting browser backfill the graph instead of
// showing a blank chart for the gap it missed.
const int WAVE_LEN = 200;
float waveBuf[WAVE_LEN];
int   waveCount = 0;   // valid samples so far (caps at WAVE_LEN)
int   waveHead  = 0;   // next write index (ring)


// ============================================================
//  STATE — Timing (millis() stamps for the scheduled jobs)
// ============================================================
unsigned long lastFastMs   = 0;
unsigned long lastHistoryMs= 0;
unsigned long startMs      = 0;
unsigned long lastWsMs     = 0;
unsigned long lastWifiMs   = 0;


// ============================================================
//  STATE — Interval accumulators (build the 1-minute average)
// ============================================================
float     intSumA    = 0.0f;   // sum of current samples this minute
int       intSamples = 0;      // how many samples we summed
float     intPeak    = 0.0f;   // highest current seen this minute


// ============================================================
//  STATE — Run-time string + server objects
// ============================================================
char runTimeStr[9] = "00:00:00";

WebServer      httpServer(80);
WebSocketsServer wsServer(81);


// ============================================================
//  DASHBOARD HTML — 100% self-contained, zero external requests
//  No CDN, no Google Fonts — works offline on ESP32 hotspot.
//  This whole block is sent to the browser exactly as-is, so do
//  NOT edit anything inside the R"=====( ... )=====" literal
//  unless you mean to change the served page.
// ============================================================
const char INDEX_HTML[] PROGMEM = R"=====(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Battery Charge Monitor</title>
<style>
  :root{
    --bg:#0a0c10;--panel:#12161e;--border:#1e2533;
    --accent:#00e5ff;--warn:#ff9100;--danger:#ff1744;--ok:#00e676;
    --txt:#c8d6e5;--dim:#4a5568;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);
       font-family:monospace;
       min-height:100vh;padding:16px;
       display:flex;flex-direction:column;align-items:center}

  h1{font-size:1.1rem;letter-spacing:.3em;color:var(--accent);
     text-transform:uppercase;margin:8px 0 18px;opacity:.9}

  /* STATUS BAR */
  .sb{display:flex;justify-content:space-between;align-items:center;
      width:100%;max-width:920px;background:var(--panel);
      border:1px solid var(--border);border-radius:8px;
      padding:8px 16px;margin-bottom:14px;font-size:.82rem;flex-wrap:wrap;gap:6px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--danger);
       display:inline-block;margin-right:6px;vertical-align:middle}
  .dot.ok{background:var(--ok);box-shadow:0 0 7px var(--ok)}
  .hi{color:var(--accent)}

  /* GAUGES */
  .gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
          gap:12px;width:100%;max-width:920px;margin-bottom:14px}
  .g{background:var(--panel);border:1px solid var(--border);border-radius:10px;
     padding:16px 12px;text-align:center}
  .gl{font-size:.6rem;letter-spacing:.18em;text-transform:uppercase;
      color:var(--dim);margin-bottom:8px}
  .gv{font-size:2.1rem;font-weight:700;line-height:1;transition:color .25s}
  .gu{font-size:.65rem;color:var(--dim);margin-top:4px;letter-spacing:.1em}
  .gbar{height:3px;border-radius:2px;background:var(--border);margin-top:10px;overflow:hidden}
  .gfill{height:100%;border-radius:2px;transition:width .12s linear,background .3s}

  /* WAVEFORM */
  .cc{width:100%;max-width:920px;background:var(--panel);
      border:1px solid var(--border);border-radius:10px;
      padding:14px 16px;margin-bottom:14px}
  .ct{font-size:.6rem;letter-spacing:.18em;text-transform:uppercase;
      color:var(--dim);margin-bottom:8px}
  canvas{width:100%!important;height:170px!important;display:block;cursor:crosshair}

  /* HISTORY */
  .hc{width:100%;max-width:920px;background:var(--panel);
      border:1px solid var(--border);border-radius:10px;
      padding:14px 16px;margin-bottom:14px}
  .ht{font-size:.6rem;letter-spacing:.18em;text-transform:uppercase;
      color:var(--dim);margin-bottom:10px;
      display:flex;justify-content:space-between;align-items:center}
  a.dl{font-size:.7rem;color:var(--ok);text-decoration:none;
       border:1px solid var(--ok);padding:3px 10px;border-radius:4px}
  a.dl:hover{background:var(--ok);color:#000}
  table{width:100%;border-collapse:collapse;font-size:.77rem}
  th{color:var(--dim);font-weight:400;letter-spacing:.1em;
     text-transform:uppercase;padding:5px 8px;
     border-bottom:1px solid var(--border);text-align:right}
  th:first-child{text-align:center}
  td{padding:4px 8px;border-bottom:1px solid rgba(30,37,51,.6);
     text-align:right;color:var(--txt)}
  td:first-child{text-align:center;color:var(--dim)}
  tr:hover td{background:rgba(0,229,255,.04)}
</style>
</head>
<body>

<h1>&#9889; Battery Charge Monitor</h1>

<div class="sb">
  <span><span class="dot" id="dot"></span><span id="cs" style="color:var(--dim)">Connecting&#8230;</span></span>
  <span>Run&nbsp;time:&nbsp;<span class="hi" id="rt">00:00:00</span></span>
  <span>Accumulated:&nbsp;<span class="hi" id="ah">0.0000</span>&nbsp;Ah</span>
</div>

<div class="gauges">
  <div class="g">
    <div class="gl">Bus Voltage</div>
    <div class="gv" id="gV" style="color:var(--accent)">---</div>
    <div class="gu">Volts</div>
    <div class="gbar"><div class="gfill" id="bV" style="width:0%;background:var(--accent)"></div></div>
  </div>
  <div class="g">
    <div class="gl">Live Current</div>
    <div class="gv" id="gI" style="color:var(--warn)">---</div>
    <div class="gu">Amperes</div>
    <div class="gbar"><div class="gfill" id="bI" style="width:0%;background:var(--warn)"></div></div>
  </div>
  <div class="g">
    <div class="gl">Live Power</div>
    <div class="gv" id="gP" style="color:var(--ok)">---</div>
    <div class="gu">Watts</div>
    <div class="gbar"><div class="gfill" id="bP" style="width:0%;background:var(--ok)"></div></div>
  </div>
  <div class="g">
    <div class="gl">Peak Current</div>
    <div class="gv" id="gPk" style="color:var(--danger)">---</div>
    <div class="gu">Amperes</div>
    <div class="gbar"><div class="gfill" id="bPk" style="width:0%;background:var(--danger)"></div></div>
  </div>
</div>

<div class="cc">
  <div class="ct">Real-time current waveform &mdash; last 200 samples &middot; 100 ms/pt</div>
  <canvas id="cv"></canvas>
</div>

<div class="cc">
  <div class="ct">Average current trend &mdash; whole run &middot; <span id="trRes">1 min</span>/pt &middot; <span id="trDur">0m</span></div>
  <canvas id="cv2"></canvas>
</div>

<div class="hc">
  <div class="ht">
    <span>1-Minute History Log</span>
    <a class="dl" href="/download">&#128229; CSV</a>
  </div>
  <table>
    <thead><tr>
      <th>Min</th><th>Voltage V</th><th>Avg A</th><th>Power W</th><th>Ah</th><th>Peak A</th>
    </tr></thead>
    <tbody id="hb"></tbody>
  </table>
</div>

<script>
// =====================================================
//  BUILT-IN CANVAS WAVEFORM  (no Chart.js needed)
// =====================================================
const WLEN = 200;
const buf  = new Float32Array(WLEN).fill(0);
let   wMax = 2.0;   // auto-scale ceiling

const cv  = document.getElementById('cv');
const ctx = cv.getContext('2d');

function resizeCv(){
  cv.width  = cv.offsetWidth  * devicePixelRatio;
  cv.height = cv.offsetHeight * devicePixelRatio;
}
resizeCv();
window.addEventListener('resize', function(){
  resizeCv(); resizeCv2(); drawTrend();
});

function drawWave(){
  const W = cv.width, H = cv.height;
  ctx.clearRect(0,0,W,H);

  // grid lines
  ctx.strokeStyle = 'rgba(30,37,51,0.9)';
  ctx.lineWidth   = 1;
  for(let i=1;i<4;i++){
    const y = (H/4)*i;
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
  }

  // y-axis labels
  ctx.fillStyle = '#4a5568';
  ctx.font      = (11*devicePixelRatio)+'px monospace';
  ctx.textAlign = 'left';
  for(let i=0;i<=4;i++){
    const v = wMax*(1 - i/4);
    const y = (H/4)*i + 3*devicePixelRatio;
    ctx.fillText(v.toFixed(2)+'A', 4*devicePixelRatio, y);
  }

  // waveform fill + stroke
  const step = W / (WLEN - 1);
  ctx.beginPath();
  for(let i=0;i<WLEN;i++){
    const x = i * step;
    const y = H - (buf[i] / wMax) * H * 0.92 - H*0.02;
    i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  }
  ctx.strokeStyle = '#ff9100';
  ctx.lineWidth   = 1.8 * devicePixelRatio;
  ctx.stroke();

  // fill under
  ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
  ctx.fillStyle = 'rgba(255,145,0,0.07)';
  ctx.fill();
}

function pushWave(v){
  if(v > wMax) wMax = Math.ceil(v * 1.3 * 10) / 10;
  buf.copyWithin(0, 1);
  buf[WLEN-1] = v;
  drawWave();
}

// Initial blank draw
drawWave();
setInterval(drawWave, 200);  // redraw even if no data yet

// =====================================================
//  FULL-RUN AVERAGE TREND  (auto-resolution, never scrolls off)
//  Keeps every 1-min average for the whole run, then averages
//  them down to <=240 evenly-spaced points so an 8 h+ run stays
//  a clean readable line. Resolution coarsens on its own as the
//  run grows:  <=4 h → 1 min/pt, 8 h → 2 min/pt, 50 h → 13 min/pt.
// =====================================================
const cv2  = document.getElementById('cv2');
const ctx2 = cv2.getContext('2d');
const TR_TARGET = 240;   // max points actually drawn
let   trend = [];        // raw per-minute avg current, oldest → newest
let   tMax  = 0.2;       // y auto-scale ceiling (A)

function resizeCv2(){
  cv2.width  = cv2.offsetWidth  * devicePixelRatio;
  cv2.height = cv2.offsetHeight * devicePixelRatio;
}
resizeCv2();

function fmtMin(m){
  if(m < 60) return m + 'm';
  const h = Math.floor(m/60), mm = m % 60;
  return h + 'h' + (mm ? (' ' + mm + 'm') : '');
}

// Average the raw minutes into <= TR_TARGET equal-time buckets.
function bucketize(){
  const n = trend.length;
  const per = Math.max(1, Math.ceil(n / TR_TARGET));  // minutes/point
  const pts = [];
  for(let s=0; s<n; s+=per){
    let sum = 0, c = 0;
    for(let k=s; k<s+per && k<n; k++){ sum += trend[k]; c++; }
    pts.push(sum / c);
  }
  return { pts: pts, per: per };
}

function drawTrend(){
  const W = cv2.width, H = cv2.height;
  ctx2.clearRect(0,0,W,H);

  // grid
  ctx2.strokeStyle = 'rgba(30,37,51,0.9)';
  ctx2.lineWidth   = 1;
  for(let i=1;i<4;i++){
    const y = (H/4)*i;
    ctx2.beginPath(); ctx2.moveTo(0,y); ctx2.lineTo(W,y); ctx2.stroke();
  }
  ctx2.font = (11*devicePixelRatio)+'px monospace';

  const n = trend.length;
  if(n === 0){
    ctx2.fillStyle = '#4a5568';
    ctx2.textAlign = 'center';
    ctx2.fillText('waiting for first 1-minute average…', W/2, H/2);
    return;
  }

  const b = bucketize(), pts = b.pts, m = pts.length;

  // y scale from the SMOOTHED data so the line fills the chart
  let mx = 0;
  for(let i=0;i<m;i++) if(pts[i] > mx) mx = pts[i];
  tMax = Math.max(0.2, Math.ceil(mx * 1.25 * 10) / 10);

  // y-axis labels
  ctx2.fillStyle = '#4a5568';
  ctx2.textAlign = 'left';
  for(let i=0;i<=4;i++){
    const v = tMax*(1 - i/4);
    const y = (H/4)*i + 3*devicePixelRatio;
    ctx2.fillText(v.toFixed(2)+'A', 4*devicePixelRatio, y);
  }

  // x spreads the ENTIRE run across the canvas — points compress
  // and auto-average coarser instead of scrolling away.
  const xAt = function(i){ return (m===1) ? W/2 : (i/(m-1))*W; };
  const yAt = function(v){ return H - (v/tMax)*H*0.92 - H*0.02; };

  ctx2.beginPath();
  for(let i=0;i<m;i++){
    const x = xAt(i), y = yAt(pts[i]);
    i===0 ? ctx2.moveTo(x,y) : ctx2.lineTo(x,y);
  }
  ctx2.strokeStyle = '#00e5ff';
  ctx2.lineWidth   = 1.8 * devicePixelRatio;
  ctx2.stroke();
  ctx2.lineTo(xAt(m-1), H); ctx2.lineTo(xAt(0), H); ctx2.closePath();
  ctx2.fillStyle = 'rgba(0,229,255,0.07)';
  ctx2.fill();

  // dots while sparse enough to stay readable
  if(m <= 120){
    ctx2.fillStyle = '#00e5ff';
    for(let i=0;i<m;i++){
      ctx2.beginPath();
      ctx2.arc(xAt(i), yAt(pts[i]), 2.2*devicePixelRatio, 0, 6.2832);
      ctx2.fill();
    }
  }

  // x time labels (real run time: start / mid / end)
  ctx2.fillStyle = '#4a5568';
  ctx2.textAlign = 'left';
  ctx2.fillText('0m', 4*devicePixelRatio, H - 4*devicePixelRatio);
  ctx2.textAlign = 'right';
  ctx2.fillText(fmtMin(n), W - 4*devicePixelRatio, H - 4*devicePixelRatio);
  if(n > 3){
    ctx2.textAlign = 'center';
    ctx2.fillText(fmtMin(Math.round(n/2)), W/2, H - 4*devicePixelRatio);
  }

  // header readout: current auto-resolution + run length
  document.getElementById('trRes').textContent = b.per + ' min';
  document.getElementById('trDur').textContent = fmtMin(n);
}

function setTrend(arr){      // full rebuild — used on (re)connect
  trend = arr.slice();
  drawTrend();
}
function pushTrend(v){       // one freshly completed minute
  trend.push(v);
  drawTrend();
}

drawTrend();  // initial empty state

// =====================================================
//  GAUGE HELPERS
// =====================================================
function setGauge(id, bid, val, max, decimals, color){
  document.getElementById(id).textContent = val.toFixed(decimals);
  document.getElementById(id).style.color = color;
  const pct = Math.min(100, Math.max(0, (val/max)*100)).toFixed(1);
  document.getElementById(bid).style.width      = pct + '%';
  document.getElementById(bid).style.background = color;
}

// =====================================================
//  HISTORY TABLE
// =====================================================
function addRow(d){
  const tb = document.getElementById('hb');
  const tr = document.createElement('tr');
  tr.innerHTML =
    '<td>'+d.min+'</td><td>'+d.v.toFixed(2)+'</td><td>'+d.a.toFixed(4)+'</td>'+
    '<td>'+d.p.toFixed(2)+'</td><td>'+d.ah.toFixed(4)+'</td><td>'+d.pk.toFixed(4)+'</td>';
  tb.prepend(tr);
  // keep table max 100 rows for memory
  while(tb.rows.length > 100) tb.deleteRow(tb.rows.length-1);
}

// =====================================================
//  WEBSOCKET
// =====================================================
const dot = document.getElementById('dot');
const cs  = document.getElementById('cs');
let   ws, wsOk = false, reconnTimer = null, watchdog = null;

// Pull a fresh snapshot of the table + graph so a tab that was
// disconnected (WiFi switch, sleep) catches up on what it missed
// instead of showing a stale/blank chart and a gapped log.
function resync(){
  fetch('/histjson').then(function(r){ return r.json(); })
    .then(function(arr){
      document.getElementById('hb').innerHTML = '';
      arr.forEach(addRow);
      setTrend(arr.map(function(d){ return d.a; }));
    }).catch(function(){});
  fetch('/wavejson').then(function(r){ return r.json(); })
    .then(function(arr){
      if(!arr || !arr.length) return;
      buf.fill(0);
      var n = Math.min(arr.length, WLEN);
      for(var k=0;k<n;k++){
        var val = arr[arr.length - n + k];
        buf[WLEN - n + k] = val;
        if(val > wMax) wMax = Math.ceil(val * 1.3 * 10) / 10;
      }
      drawWave();
    }).catch(function(){});
}

function scheduleReconnect(){
  clearTimeout(reconnTimer);
  reconnTimer = setTimeout(connect, 2000);
}

function connect(){
  clearTimeout(reconnTimer);
  clearTimeout(watchdog);
  // Drop any previous socket without letting its onclose queue
  // a second reconnect (avoids stacked connection attempts).
  if(ws){ try{ ws.onclose = null; ws.onerror = null; ws.close(); }catch(e){} }

  ws = new WebSocket('ws://' + location.hostname + ':81/');

  // If the handshake stalls (dead/zombie server slot), force it
  // closed and retry instead of hanging on CONNECTING forever.
  watchdog = setTimeout(function(){
    if(!wsOk){ try{ ws.close(); }catch(e){} }
  }, 5000);

  ws.onopen = function(){
    wsOk = true;
    clearTimeout(watchdog);
    dot.className = 'dot ok';
    cs.textContent = 'LIVE';
    cs.style.color = 'var(--ok)';
    resync();
  };

  ws.onclose = function(){
    wsOk = false;
    clearTimeout(watchdog);
    dot.className = 'dot';
    cs.textContent = 'Reconnecting…';
    cs.style.color = 'var(--warn)';
    scheduleReconnect();
  };

  ws.onerror = function(){ try{ ws.close(); }catch(e){} };

  ws.onmessage = function(e){
    var d;
    try{ d = JSON.parse(e.data); } catch(ex){ return; }

    if(d.t === 'live'){
      document.getElementById('rt').textContent = d.rt;
      document.getElementById('ah').textContent = d.ah.toFixed(4);

      var ic = d.i > 1.5 ? 'var(--danger)' : d.i > 0.3 ? 'var(--warn)' : 'var(--ok)';
      setGauge('gV',  'bV',  d.v,  20, 2,  'var(--accent)');
      setGauge('gI',  'bI',  d.i,   2, 4,  ic);
      setGauge('gP',  'bP',  d.p,  40, 2,  'var(--ok)');
      setGauge('gPk', 'bPk', d.pk,  2, 4,  'var(--danger)');

      pushWave(d.i);
    }

    if(d.t === 'hist'){
      addRow(d);
      pushTrend(d.a);
    }
  };
}

connect();

// Mobile browsers freeze background timers, so the auto-reconnect
// can stay stalled after you leave and return. Kick it immediately
// when the tab is shown again or the network comes back.
document.addEventListener('visibilitychange', function(){
  if(!document.hidden && !wsOk) connect();
});
window.addEventListener('online',  function(){ if(!wsOk) connect(); });
window.addEventListener('focus',   function(){ if(!wsOk) connect(); });
</script>
</body>
</html>
)=====";


// ============================================================
//  HTTP HANDLERS — what the ESP32 answers for each URL
// ============================================================

// "/" — hand the browser the dashboard page from flash.
void handleRoot()     { httpServer.send_P(200, "text/html", INDEX_HTML); }

// "/download" — stream the whole history log as a CSV file.
// We send it in chunks (CONTENT_LENGTH_UNKNOWN) so we never have to
// build the entire file in RAM at once.
void handleDownload() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  httpServer.sendHeader("Content-Disposition","attachment; filename=\"battery_log.csv\"");
  httpServer.send(200,"text/csv","");
  httpServer.sendContent("Minute,Voltage_V,Avg_Current_A,Power_W,Accumulated_Ah,Peak_Current_A\n");
  char row[128];
  for (int i=0; i<historyCount; i++) {
    snprintf(row,sizeof(row),"%d,%.2f,%.4f,%.2f,%.6f,%.4f\n",
      i+1, hVoltage[i], hCurrent[i], hPower[i], hAh[i], hPeak[i]);
    httpServer.sendContent(row);
  }
  httpServer.sendContent("");
}

// "/histjson" — same history as JSON, so a (re)connecting browser can
// rebuild its table and trend chart from scratch.
void handleHistJson() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  httpServer.send(200,"application/json","");
  httpServer.sendContent("[");
  char buf[128];
  for (int i=0; i<historyCount; i++) {
    snprintf(buf,sizeof(buf),
      "{\"min\":%d,\"v\":%.2f,\"a\":%.4f,\"p\":%.2f,\"ah\":%.6f,\"pk\":%.4f}%s",
      i+1, hVoltage[i], hCurrent[i], hPower[i], hAh[i], hPeak[i],
      (i==historyCount-1)?"":",");
    httpServer.sendContent(buf);
  }
  httpServer.sendContent("]");
}

// "/wavejson" — the last up-to-200 current samples, oldest -> newest,
// so the browser can drop it straight into its waveform buffer.
void handleWaveJson() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  httpServer.send(200,"application/json","");
  httpServer.sendContent("[");
  char b[16];
  // If the ring isn't full yet, start at 0; once full, start at the
  // oldest slot (waveHead) and walk forward, wrapping around.
  int start = (waveCount < WAVE_LEN) ? 0 : waveHead;
  for (int k=0; k<waveCount; k++) {
    int idx = (start + k) % WAVE_LEN;
    snprintf(b,sizeof(b),"%.4f%s", waveBuf[idx], (k==waveCount-1)?"":",");
    httpServer.sendContent(b);
  }
  httpServer.sendContent("]");
}


// ============================================================
//  INA226 READ — turn one sensor reading into V / I / P
// ============================================================
void readINA() {
  g_voltage = ina.getBusVoltage();
  float sv   = ina.getShuntVoltage();
  float raw  = sv / SHUNT_RESISTOR_OHMS;          // Ohm's law: I = V_shunt / R_shunt
  // NOTE: readings below 1 mA (including any negative/reverse current) are
  // forced to 0 to hide sensor noise around zero. This means current is only
  // ever shown as a positive draw — see the note in the handover summary if
  // you need to see reverse/charging direction separately.
  g_current  = (raw < 0.001f) ? 0.0f : raw;
  g_power    = g_voltage * g_current;
  if (g_current > g_peak) g_peak = g_current;     // remember the all-time peak
}


// ============================================================
//  WIFI EVENTS — keep the link alive across hotspot drops
// ============================================================
void onWifiEvent(WiFiEvent_t event) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.printf("[WiFi] Online → http://%s\n",
                    WiFi.localIP().toString().c_str());
      // (Re)publish the battery.local name each time we get an IP.
      MDNS.end();
      if (MDNS.begin(MDNS_HOST)) MDNS.addService("http", "tcp", 80);
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.println("[WiFi] Link lost — will keep retrying, monitor stays running.");
      break;
    default: break;
  }
}


// ============================================================
//  SETUP — runs once at power-on
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  // ── INA226 ──
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!ina.begin()) {
    Serial.println("[ERROR] INA226 not found!");
    while(1) delay(10);                 // halt: nothing to measure without the sensor
  }
  ina.setMaxCurrentShunt(2.0, SHUNT_RESISTOR_OHMS);   // expect up to 2 A through a 0.1 Ω shunt
  Serial.println("[OK] INA226 ready");

  // ── Join laptop hotspot at the FIXED IP (Station mode) ──
  WiFi.onEvent(onWifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);              // keep latency low for live push
  WiFi.setAutoReconnect(true);       // self-heal on short blips
  if (!WiFi.config(STA_IP, STA_GATEWAY, STA_SUBNET, STA_DNS))
    Serial.println("[WiFi] Static IP config failed!");
  WiFi.begin(STA_SSID, STA_PASSWORD);

  Serial.printf("[WiFi] Joining \"%s\" — dashboard fixed at http://%s\n",
                STA_SSID, STA_IP.toString().c_str());

  // Wait briefly for a first connect just for nicer boot logs, but
  // DON'T hang here — the monitor must run even with no WiFi, and
  // the loop watchdog reconnects on its own whenever the hotspot
  // comes back. (GOT_IP / DISCONNECTED handled by onWifiEvent.)
  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 12000) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED)
    Serial.println("[WiFi] Hotspot not up yet — monitoring runs anyway; "
                   "it will connect by itself when the hotspot appears.");

  // ── HTTP routes ──
  httpServer.on("/",         handleRoot);
  httpServer.on("/download", handleDownload);
  httpServer.on("/histjson", handleHistJson);
  httpServer.on("/wavejson", handleWaveJson);
  httpServer.begin();

  // ── WebSocket ──
  wsServer.begin();
  // Heartbeat: ping every 10 s, expect pong within 3 s, drop the
  // client after 2 misses. Without this, a browser that leaves the
  // network (sleep, WiFi switch) leaves a zombie socket occupying
  // one of the server's few client slots forever — after a few
  // drops every slot is dead and the dashboard sticks "Reconnecting…".
  wsServer.enableHeartbeat(10000, 3000, 2);
  // No message handler needed — server only broadcasts

  // Stamp all the schedulers so the first jobs fire one interval from now.
  startMs      = millis();
  lastFastMs   = startMs;
  lastHistoryMs= startMs;
  lastWsMs     = startMs;

  Serial.println("[RUN] Monitoring started.");
}


// ============================================================
//  LOOP — runs forever
// ============================================================
void loop() {
  httpServer.handleClient();
  wsServer.loop();

  unsigned long now = millis();

  // ── WIFI WATCHDOG ──
  // If the hotspot vanished (laptop asleep, out of range, toggled
  // off), keep trying to rejoin at the SAME fixed IP — every 10 s,
  // only while down, never blocking the monitor or needing a reboot.
  // Re-applying config() guarantees the static IP sticks on rejoin.
  if (WiFi.status() != WL_CONNECTED && now - lastWifiMs >= 10000) {
    lastWifiMs = now;
    Serial.println("[WiFi] Down — reconnecting…");
    WiFi.disconnect();
    WiFi.config(STA_IP, STA_GATEWAY, STA_SUBNET, STA_DNS);
    WiFi.begin(STA_SSID, STA_PASSWORD);
  }

  // ── FAST SAMPLE (every FAST_SAMPLE_MS = 100 ms) ──
  if (now - lastFastMs >= FAST_SAMPLE_MS) {
    lastFastMs = now;
    readINA();

    // Store into waveform ring buffer for reconnect backfill
    waveBuf[waveHead] = g_current;
    waveHead = (waveHead + 1) % WAVE_LEN;
    if (waveCount < WAVE_LEN) waveCount++;

    // Coulomb counting: add (current * time) so g_areaAs accumulates
    // amp-seconds; dividing by 3600 later gives amp-hours.
    g_areaAs += g_current * (FAST_SAMPLE_MS / 1000.0f);

    // Interval accumulators for this minute's average
    intSumA += g_current;
    intSamples++;
    if (g_current > intPeak) intPeak = g_current;

    // Build the HH:MM:SS run-time string from elapsed seconds
    float elapsed = (now - startMs) / 1000.0f;
    int rh = (int)(elapsed / 3600);
    int rm = (int)((elapsed - rh*3600) / 60);
    int rs = (int)elapsed % 60;
    sprintf(runTimeStr, "%02d:%02d:%02d", rh, rm, rs);
  }

  // ── WEBSOCKET PUSH (every 100 ms, aligned with the sample) ──
  if (now - lastWsMs >= FAST_SAMPLE_MS) {
    lastWsMs = now;
    char msg[200];
    snprintf(msg, sizeof(msg),
      "{\"t\":\"live\",\"v\":%.2f,\"i\":%.4f,\"p\":%.2f,\"pk\":%.4f,\"ah\":%.6f,\"rt\":\"%s\"}",
      g_voltage, g_current, g_power, g_peak, g_areaAs/3600.0f, runTimeStr);
    wsServer.broadcastTXT(msg);
  }

  // ── 1-MINUTE HISTORY SNAPSHOT ──
  if (now - lastHistoryMs >= HISTORY_SAVE_MS) {
    lastHistoryMs = now;
    float avgA = (intSamples > 0) ? (intSumA / intSamples) : 0.0f;   // mean current this minute

    if (historyCount < MAX_HISTORY) {
      hVoltage[historyCount] = g_voltage;
      hCurrent[historyCount] = avgA;
      hPower[historyCount]   = g_power;
      hAh[historyCount]      = g_areaAs / 3600.0f;
      hPeak[historyCount]    = intPeak;
      historyCount++;
    }

    // Push history row over WebSocket so open tabs update instantly
    char hmsg[200];
    snprintf(hmsg, sizeof(hmsg),
      "{\"t\":\"hist\",\"min\":%d,\"v\":%.2f,\"a\":%.4f,\"p\":%.2f,\"ah\":%.6f,\"pk\":%.4f}",
      historyCount, g_voltage, avgA, g_power, g_areaAs/3600.0f, intPeak);
    wsServer.broadcastTXT(hmsg);

    // Reset the per-minute accumulators for the next minute
    intSumA = 0.0f; intSamples = 0; intPeak = 0.0f;
  }
}
