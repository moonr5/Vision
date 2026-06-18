/*
 ============================================================================
   ____        _   _                       __  __             _ _
  | __ )  __ _| |_| |_ ___ _ __ _   _      |  \/  | ___  _ __ (_) |_ ___  _ __
  |  _ \ / _` | __| __/ _ \ '__| | | |     | |\/| |/ _ \| '_ \| | __/ _ \| '__|
  | |_) | (_| | |_| ||  __/ |  | |_| |     | |  | | (_) | | | | | || (_) | |
  |____/ \__,_|\__|\__\___|_|   \__, |     |_|  |_|\___/|_| |_|_|\__\___/|_|
                                |___/
 ============================================================================
   PROJECT   : Ahmed's Battery Percentage - ESP32 Wi-Fi Battery Monitor
   AUTHOR    : Ahmed Yousef Saeed Khalifa
   COPYRIGHT : (c) Ahmed Yousef Saeed Khalifa - All rights reserved.
 ----------------------------------------------------------------------------
   WHAT THIS DOES:
     An ESP32 reads a single-cell (1S) LiPo battery through a MAX17048 "fuel
     gauge" chip and shows the live voltage, charge %, and charge/discharge
     rate on a small web dashboard. The ESP32 hosts its OWN Wi-Fi network
     (Access Point), so no router or phone hotspot is needed - you connect
     straight to it. Readings stream live over WebSocket, and a long-term log
     is saved to on-board flash so the history survives reboots and power loss.

   HOW TO VIEW IT:
     1) On your phone/laptop, join the Wi-Fi network  "BatteryMonitor"
        (password is set in AP_PASSWORD below).
     2) Open a browser to   http://192.168.4.1     (or  http://battery.local )

   WIRING  (ESP32  <->  MAX17048 breakout):
     ESP32 3V3          ->  VCC / VDD     power the gauge from 3.3 V (NOT 5 V)
     ESP32 GND          ->  GND           common ground - required
     ESP32 GPIO21 (SDA) ->  SDA           I2C data  (see I2C_SDA below)
     ESP32 GPIO22 (SCL) ->  SCL           I2C clock (see I2C_SCL below)
     Battery +          ->  VBAT / CELL+  the cell the gauge measures
     Battery -          ->  GND           same ground as the ESP32
     (Most MAX17048 breakouts already include the I2C pull-up resistors.)

   SERIAL COMMAND  (one-time, at boot, 115200 baud):
     Within 15 s of power-up you may type   HH:MM:SS  + Enter  to SET the run
     timer. Do nothing and it RESUMES the saved timer instead. (The very first
     boot ever seeds the timer to 25:30:00 - see SEED_TIMER_SEC.)

   WEB BUTTONS  (on the dashboard):
     CSV          ->  download the full history log as a .csv file
     Fix glitches ->  median-smooth saved spikes (keeps every row)
     Clear        ->  erase the saved log from flash

   LIBRARY NEEDED:
     "WebSockets" by Markus Sattler (arduinoWebSockets).
     No MAX17048 library is required - we talk to the chip over raw I2C.

   BEHAVIOR NOTE (vs an older INA226 version): the MAX17048 is a FUEL GAUGE,
   not a power monitor. It reports battery VOLTAGE and estimates STATE OF
   CHARGE (%) plus a charge/discharge RATE (%/hr). It does NOT measure current,
   power, or amp-hours - so the gauges here are voltage / % / rate based.
 ============================================================================
*/

// ============================================================================
//  LIBRARIES / INCLUDES
//  Wire = I2C, LittleFS = flash file system (the log), Preferences = NVS
//  key/value store (the run timer), WiFi/WebServer/WebSocketsServer = the
//  dashboard + live data stream, ESPmDNS = the friendly battery.local name.
// ============================================================================
#include <Arduino.h>
#include <Wire.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WebSocketsServer.h>   // <-- arduinoWebSockets by Markus Sattler

// ============================================================================
//  CONFIG - WIFI ACCESS POINT  (the ESP hosts its OWN network)
//  No external router/hotspot needed: drop it anywhere (e.g. in a car) and it
//  logs on its own. To view: join this Wi-Fi, then open  http://192.168.4.1
// ============================================================================
const char* AP_SSID     = "BatteryMonitor";    // Wi-Fi name your phone connects to
const char* AP_PASSWORD = "12345687";          // must be >= 8 chars (use "" for an open network)
const char* MDNS_HOST   = "battery";           // also reachable as http://battery.local

// The dashboard lives at this fixed address. Keep IP and GATEWAY the same for
// a simple single-device AP; SUBNET 255.255.255.0 is the normal /24 mask.
IPAddress AP_IP     (192, 168, 4, 1);
IPAddress AP_GATEWAY(192, 168, 4, 1);
IPAddress AP_SUBNET (255, 255, 255, 0);

// ============================================================================
//  CONFIG - I2C BUS & MAX17048 FUEL GAUGE
//  These pins/addresses/registers are dictated by the wiring and the chip's
//  datasheet - change them only if your wiring or chip actually differs.
// ============================================================================
#define I2C_SDA 21                    // ESP32 GPIO used for I2C data  (SDA)
#define I2C_SCL 22                    // ESP32 GPIO used for I2C clock (SCL)
const uint8_t MAX17048_ADDR = 0x36;   // fixed 7-bit I2C address of the MAX17048
const uint8_t REG_VCELL     = 0x02;   // register holding the battery voltage
const uint8_t REG_CRATE     = 0x16;   // register holding the charge/discharge rate (%/hr)

// Small fudge factor added to the measured voltage so the dashboard matches
// your multimeter. Tweak it to whatever makes the two agree; set 0 to disable.
const float   VOLT_CAL      = 0.060;  // volts of calibration offset (0 = no correction)

// ============================================================================
//  CONFIG - SAMPLING RATES
//  How often we read the chip vs. how often we save a long-term record.
// ============================================================================
const uint32_t FAST_SAMPLE_MS  = 500;    // read + push every 500 ms (2 Hz). Lower = smoother live graph, more I2C traffic.
const uint32_t HISTORY_SAVE_MS = 300000; // save one averaged record every 5 min. Lower = finer log but the buffer fills faster.
const int      MIN_PER_REC     = HISTORY_SAVE_MS / 60000;  // = 5; minutes covered by one logged record

// ============================================================================
//  DATA - LIVE MEASUREMENTS (the "right now" values shared with the dashboard)
//  Marked volatile because they're touched from the live read and the web
//  push paths; keeping it makes the shared-state intent explicit and is safe.
// ============================================================================
volatile float g_voltage  = 0.0f;
volatile float g_percent  = 0.0f;
volatile float g_rate     = 0.0f;     // %/hr; positive = charging, negative = discharging
volatile float g_minPct   = 100.0f;   // lowest charge % seen since this run started
volatile bool  g_sensorOk = false;    // true only when the latest read passed the sanity checks

// ============================================================================
//  DATA - HISTORY VAULT  (in-RAM ring of long-term records)
//  3000 records at one every 5 min = ~250 h (~10 days) of history in RAM.
//  Raise MAX_HISTORY for a longer window (uses more RAM: 4 floats x 3000).
// ============================================================================
const int MAX_HISTORY = 3000;
float hVoltage[MAX_HISTORY];           // averaged voltage per record
float hPercent[MAX_HISTORY];           // averaged charge % per record
float hRate[MAX_HISTORY];              // charge/discharge rate per record
float hLow[MAX_HISTORY];               // lowest % seen within that record's window
int   historyCount = 0;                // how many records are currently filled

const char* HIST_FILE = "/hist.bin";   // log file in flash (LittleFS) - survives reboots/power loss
Preferences prefs;                     // NVS key/value store used only for the run timer (handles frequent writes well)

// The very first boot ever (no saved timer yet) starts the run clock here, so
// the device looks like it has already been running 25 h 30 m. Purely cosmetic.
#define SEED_TIMER_SEC (25UL*3600UL + 30UL*60UL)

struct HistRec { float v, p, cr, lo; };   // one log record = 4 floats = 16 bytes (saved every 5 min)

// ============================================================================
//  DATA - WAVEFORM RING BUFFER  (the fast "last 200 samples" voltage trace)
//  A circular buffer: waveHead points at the next write slot and wraps around,
//  so we always keep the most recent WAVE_LEN readings without shifting memory.
// ============================================================================
const int WAVE_LEN = 200;              // 200 samples x 500 ms = ~100 s of fast trace
float waveBuf[WAVE_LEN];
int   waveCount = 0;                   // how many slots have been filled (caps at WAVE_LEN)
int   waveHead  = 0;                   // index of the next slot to overwrite

// ============================================================================
//  TIMING - "do X every N ms" bookkeeping
//  Each lastXxxMs remembers when we last did a thing; loop() compares it to
//  millis() instead of using delay(), so nothing blocks the web server.
// ============================================================================
unsigned long lastFastMs    = 0;   // last fast sample (FAST_SAMPLE_MS)
unsigned long lastHistoryMs = 0;   // last history snapshot (HISTORY_SAVE_MS)
unsigned long startMs       = 0;   // millis() captured at end of setup()
unsigned long lastWsMs      = 0;   // last WebSocket live push
unsigned long lastWifiMs    = 0;   // (reserved; unused in Access-Point mode)
unsigned long baseSec       = 0;   // run-timer starting offset in seconds (saved in NVS)
bool          clockSetByUser = false;   // true if the user typed a time at boot
unsigned long lastClockMs   = 0;        // last time the run timer was written to NVS
const uint32_t CLOCK_SAVE_MS = 10000;   // save the run timer every 10 s (rarely enough to spare flash, often enough to barely lose time on a reset)

// ============================================================================
//  DATA - INTERVAL ACCUMULATORS
//  Between history snapshots we sum up the fast samples so each saved record
//  is a smooth average rather than one noisy instant. Reset after every save.
// ============================================================================
float intSumV    = 0.0f;   // running sum of voltage this interval
float intSumP    = 0.0f;   // running sum of charge % this interval
int   intSamples = 0;      // how many fast samples went into the sums
float intMinP    = 100.0f; // lowest charge % seen this interval

// Reusable text buffer for the "HH:MM:SS" run-timer string shown on the page.
char runTimeStr[12] = "00:00:00";

// HTTP serves the dashboard on port 80; WebSocket streams live data on port 81.
WebServer        httpServer(80);
WebSocketsServer wsServer(81);

// ============================================================================
//  WEB DASHBOARD - the entire page (HTML + CSS + JavaScript) as one string
//  ----------------------------------------------------------------------------
//  Stored in PROGMEM (flash) so it doesn't eat RAM, and 100% self-contained:
//  no fonts, libraries, or CDN calls, so it loads even with no internet.
//  The browser-side JavaScript here opens the WebSocket (port 81), draws the
//  gauges/graphs, and fetches the history. This block is served as-is to the
//  browser, so it is intentionally left untouched - edit with care, since any
//  change here changes exactly what the dashboard renders.
// ============================================================================
const char INDEX_HTML[] PROGMEM = R"=====(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Battery Voltage Monitor</title>
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

  .sb{display:flex;justify-content:space-between;align-items:center;
      width:100%;max-width:920px;background:var(--panel);
      border:1px solid var(--border);border-radius:8px;
      padding:8px 16px;margin-bottom:14px;font-size:.82rem;flex-wrap:wrap;gap:6px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--danger);
       display:inline-block;margin-right:6px;vertical-align:middle}
  .dot.ok{background:var(--ok);box-shadow:0 0 7px var(--ok)}
  .hi{color:var(--accent)}

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

  .cc{width:100%;max-width:920px;background:var(--panel);
      border:1px solid var(--border);border-radius:10px;
      padding:14px 16px;margin-bottom:14px}
  .ct{font-size:.6rem;letter-spacing:.18em;text-transform:uppercase;
      color:var(--dim);margin-bottom:8px}
  canvas{width:100%!important;height:170px!important;display:block;cursor:crosshair}

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

<h1>&#9889; Battery Voltage Monitor</h1>

<div class="sb">
  <span><span class="dot" id="dot"></span><span id="cs" style="color:var(--dim)">Connecting&#8230;</span></span>
  <span>Time:&nbsp;<span class="hi" id="rt">00:00:00</span></span>
  <span>Status:&nbsp;<span class="hi" id="st">&mdash;</span></span>
</div>

<div class="gauges">
  <div class="g">
    <div class="gl">Battery Voltage</div>
    <div class="gv" id="gV" style="color:var(--accent)">---</div>
    <div class="gu">Volts</div>
    <div class="gbar"><div class="gfill" id="bV" style="width:0%;background:var(--accent)"></div></div>
  </div>
  <div class="g">
    <div class="gl">State of Charge</div>
    <div class="gv" id="gP" style="color:var(--ok)">---</div>
    <div class="gu">Percent</div>
    <div class="gbar"><div class="gfill" id="bP" style="width:0%;background:var(--ok)"></div></div>
  </div>
  <div class="g">
    <div class="gl">Charge Rate</div>
    <div class="gv" id="gCr" style="color:var(--warn)">---</div>
    <div class="gu">% / hour</div>
    <div class="gbar"><div class="gfill" id="bCr" style="width:0%;background:var(--warn)"></div></div>
  </div>
  <div class="g">
    <div class="gl">Lowest Charge</div>
    <div class="gv" id="gLo" style="color:var(--danger)">---</div>
    <div class="gu">Percent</div>
    <div class="gbar"><div class="gfill" id="bLo" style="width:0%;background:var(--danger)"></div></div>
  </div>
</div>

<div class="cc">
  <div class="ct">Battery voltage &mdash; whole run (24 h) &middot; <span id="trRes">1 min</span>/pt &middot; <span id="trDur">0m</span></div>
  <canvas id="cv2"></canvas>
</div>

<div class="cc">
  <div class="ct">Battery voltage &mdash; last 200 samples &middot; 500 ms/pt</div>
  <canvas id="cv"></canvas>
</div>

<div class="hc">
  <div class="ht">
    <span>1-Minute History Log</span>
    <span>
      <a class="dl" href="/download">&#128229; CSV</a>
      <a class="dl" id="fix" href="#" style="color:var(--accent);border-color:var(--accent)">Fix glitches</a>
      <a class="dl" id="clr" href="#" style="color:var(--danger);border-color:var(--danger)">Clear</a>
    </span>
  </div>
  <table>
    <thead><tr>
      <th>Min</th><th>Voltage V</th><th>Charge %</th><th>Rate %/h</th><th>Low %</th>
    </tr></thead>
    <tbody id="hb"></tbody>
  </table>
</div>

<script>
// =====================================================
//  VOLTAGE WAVEFORM (fixed 3.0–4.3 V window)
// =====================================================
const WLEN  = 200;
const buf   = new Float32Array(WLEN).fill(0);
const VWMIN = 3.0, VWMAX = 4.3;

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

  ctx.strokeStyle = 'rgba(30,37,51,0.9)';
  ctx.lineWidth   = 1;
  for(let i=1;i<4;i++){
    const y = (H/4)*i;
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
  }

  ctx.fillStyle = '#4a5568';
  ctx.font      = (11*devicePixelRatio)+'px monospace';
  ctx.textAlign = 'left';
  for(let i=0;i<=4;i++){
    const v = VWMAX - (VWMAX-VWMIN)*(i/4);
    const y = (H/4)*i + 3*devicePixelRatio;
    ctx.fillText(v.toFixed(2)+'V', 4*devicePixelRatio, y);
  }

  const step = W / (WLEN - 1);
  ctx.beginPath();
  for(let i=0;i<WLEN;i++){
    const x = i * step;
    let f = (buf[i] - VWMIN) / (VWMAX - VWMIN);
    f = Math.max(0, Math.min(1, f));
    const y = H - f * H * 0.92 - H*0.02;
    i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  }
  ctx.strokeStyle = '#00e5ff';
  ctx.lineWidth   = 1.8 * devicePixelRatio;
  ctx.stroke();

  ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
  ctx.fillStyle = 'rgba(0,229,255,0.07)';
  ctx.fill();
}

function pushWave(v){
  buf.copyWithin(0, 1);
  buf[WLEN-1] = v;
  drawWave();
}

drawWave();
setInterval(drawWave, 500);

// =====================================================
//  STATE-OF-CHARGE TREND (fixed 0–100 %, auto-resolution)
// =====================================================
const cv2  = document.getElementById('cv2');
const ctx2 = cv2.getContext('2d');
const TR_TARGET = 240;
const MPR = 5;           // minutes per history record (match HISTORY_SAVE_MS on the ESP)
const TMAX = 100;        // % is already 0..100
let   trend = [];

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

function bucketize(){
  const n = trend.length;
  const per = Math.max(1, Math.ceil(n / TR_TARGET));
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
    ctx2.fillText('waiting for first 1-minute snapshot…', W/2, H/2);
    return;
  }

  const b = bucketize(), pts = b.pts, m = pts.length;

  // auto-scale the voltage axis to the data (+padding) so the swings show
  let lo = Infinity, hi = -Infinity;
  for(let i=0;i<m;i++){ if(pts[i]<lo) lo=pts[i]; if(pts[i]>hi) hi=pts[i]; }
  let span = hi - lo; if(span < 0.05) span = 0.05;
  const vLo = lo - span*0.15, vHi = hi + span*0.15;

  ctx2.fillStyle = '#4a5568';
  ctx2.textAlign = 'left';
  for(let i=0;i<=4;i++){
    const v = vHi - (vHi-vLo)*(i/4);
    const y = (H/4)*i + 3*devicePixelRatio;
    ctx2.fillText(v.toFixed(3)+'V', 4*devicePixelRatio, y);
  }

  const xAt = function(i){ return (m===1) ? W/2 : (i/(m-1))*W; };
  const yAt = function(v){ return H - ((v-vLo)/(vHi-vLo))*H*0.92 - H*0.02; };

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

  if(m <= 120){
    ctx2.fillStyle = '#00e5ff';
    for(let i=0;i<m;i++){
      ctx2.beginPath();
      ctx2.arc(xAt(i), yAt(pts[i]), 2.2*devicePixelRatio, 0, 6.2832);
      ctx2.fill();
    }
  }

  ctx2.fillStyle = '#4a5568';
  ctx2.textAlign = 'left';
  ctx2.fillText('0m', 4*devicePixelRatio, H - 4*devicePixelRatio);
  ctx2.textAlign = 'right';
  ctx2.fillText(fmtMin(n*MPR), W - 4*devicePixelRatio, H - 4*devicePixelRatio);
  if(n > 3){
    ctx2.textAlign = 'center';
    ctx2.fillText(fmtMin(Math.round(n/2)*MPR), W/2, H - 4*devicePixelRatio);
  }

  document.getElementById('trRes').textContent = (b.per*MPR) + ' min';
  document.getElementById('trDur').textContent = fmtMin(n*MPR);
}

function setTrend(arr){ trend = arr.slice(); drawTrend(); }
function pushTrend(v){  trend.push(v);       drawTrend(); }

drawTrend();

// =====================================================
//  GAUGE HELPER (bar uses |value| so negative rate still shows)
// =====================================================
function setGauge(id, bid, val, max, decimals, color){
  document.getElementById(id).textContent  = val.toFixed(decimals);
  document.getElementById(id).style.color  = color;
  const pct = Math.min(100, Math.max(0, (Math.abs(val)/max)*100)).toFixed(1);
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
    '<td>'+d.min+'</td><td>'+d.v.toFixed(3)+'</td><td>'+d.p.toFixed(1)+'</td>'+
    '<td>'+d.cr.toFixed(2)+'</td><td>'+d.lo.toFixed(1)+'</td>';
  tb.prepend(tr);
  while(tb.rows.length > 100) tb.deleteRow(tb.rows.length-1);
}

// =====================================================
//  WEBSOCKET
// =====================================================
const dot = document.getElementById('dot');
const cs  = document.getElementById('cs');
let   ws, wsOk = false, reconnTimer = null, watchdog = null;

function resync(){
  fetch('/histjson').then(function(r){ return r.json(); })
    .then(function(arr){
      document.getElementById('hb').innerHTML = '';
      arr.slice(-100).forEach(addRow);            // only render the latest 100 rows
      setTrend(arr.map(function(d){ return d.v; }));
    }).catch(function(){});
  fetch('/wavejson').then(function(r){ return r.json(); })
    .then(function(arr){
      if(!arr || !arr.length) return;
      buf.fill(0);
      var n = Math.min(arr.length, WLEN);
      for(var k=0;k<n;k++){ buf[WLEN - n + k] = arr[arr.length - n + k]; }
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
  if(ws){ try{ ws.onclose = null; ws.onerror = null; ws.close(); }catch(e){} }

  ws = new WebSocket('ws://' + location.hostname + ':81/');

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
      var se = document.getElementById('st');

      if(d.ok === 0){
        se.textContent = 'Sensor offline'; se.style.color = 'var(--danger)';
      } else {
        var st  = d.cr > 0.5 ? 'Charging' : d.cr < -0.5 ? 'Discharging' : 'Idle';
        var stc = d.cr > 0.5 ? 'var(--ok)' : d.cr < -0.5 ? 'var(--warn)' : 'var(--dim)';
        se.textContent = st; se.style.color = stc;

        var pc = d.p > 50 ? 'var(--ok)' : d.p > 20 ? 'var(--warn)' : 'var(--danger)';
        setGauge('gV',  'bV',  d.v,  4.3, 3, 'var(--accent)');
        setGauge('gP',  'bP',  d.p,  100, 1, pc);
        setGauge('gCr', 'bCr', d.cr,  10, 2, stc);
        setGauge('gLo', 'bLo', d.lo, 100, 1, 'var(--danger)');

        pushWave(d.v);
      }
    }

    if(d.t === 'hist'){
      addRow(d);
      pushTrend(d.v);
    }
  };
}

connect();

document.addEventListener('visibilitychange', function(){
  if(!document.hidden && !wsOk) connect();
});
window.addEventListener('online', function(){ if(!wsOk) connect(); });
window.addEventListener('focus',  function(){ if(!wsOk) connect(); });

document.getElementById('clr').addEventListener('click', function(e){
  e.preventDefault();
  if(!confirm('Erase the saved log from flash?')) return;
  fetch('/clear').then(function(){
    document.getElementById('hb').innerHTML = '';
    trend = []; drawTrend();
  }).catch(function(){});
});

document.getElementById('fix').addEventListener('click', function(e){
  e.preventDefault();
  if(!confirm('De-spike the saved graph? Keeps every row, just smooths the glitch dips.')) return;
  var b = document.getElementById('fix'); var t = b.textContent; b.textContent = 'Fixing...';
  fetch('/cleanup').then(function(){ b.textContent = t; resync(); })
                   .catch(function(){ b.textContent = t; });
});
</script>
</body>
</html>
)=====";

// ============================================================================
//  HTTP HANDLERS - one function per URL the dashboard can request
//  These are wired to their URLs in setup() (httpServer.on(...)).
// ============================================================================

// GET /  ->  serve the dashboard page straight from flash (PROGMEM).
void handleRoot() { httpServer.send_P(200, "text/html", INDEX_HTML); }

// GET /download  ->  stream the whole history log as a downloadable CSV file.
// We build the CSV into a 1 KB buffer and flush it in chunks. Packing many
// rows per packet (instead of one send per row) makes the download much faster.
void handleDownload() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);   // length unknown up front -> chunked transfer
  httpServer.sendHeader("Content-Disposition","attachment; filename=\"battery_log.csv\"");
  httpServer.send(200,"text/csv","");
  char chunk[1024];                       // outgoing packet buffer (bigger = fewer, larger packets)
  int len = snprintf(chunk, sizeof(chunk), "Minute,Voltage_V,Charge_pct,Rate_pct_per_hr,Low_pct\n");
  char row[64];                           // one formatted CSV line at a time
  for (int i=0; i<historyCount; i++) {
    int n = snprintf(row,sizeof(row),"%d,%.3f,%.1f,%.2f,%.1f\n",
      (i+1)*MIN_PER_REC, hVoltage[i], hPercent[i], hRate[i], hLow[i]);
    // If this row won't fit, flush what we have and start the buffer fresh.
    if (len + n >= (int)sizeof(chunk)) { chunk[len]=0; httpServer.sendContent(chunk); len=0; }
    memcpy(chunk + len, row, n); len += n;
  }
  if (len > 0) { chunk[len]=0; httpServer.sendContent(chunk); }  // flush the final partial buffer
  httpServer.sendContent("");                                    // empty chunk = "download finished"
}

// GET /histjson  ->  the whole history as a JSON array, used on (re)connect to
// repopulate the table and trend graph in one go. Same chunked, batched-row
// trick as the CSV handler. The "-2" margin leaves room for the closing "]".
void handleHistJson() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  httpServer.send(200,"application/json","");
  char chunk[1024]; int len = 0;        // batch many rows per packet (much faster)
  chunk[len++] = '[';
  char row[96];
  for (int i=0; i<historyCount; i++) {
    int n = snprintf(row,sizeof(row),
      "{\"min\":%d,\"v\":%.3f,\"p\":%.1f,\"cr\":%.2f,\"lo\":%.1f}%s",
      (i+1)*MIN_PER_REC, hVoltage[i], hPercent[i], hRate[i], hLow[i],
      (i==historyCount-1)?"":",");                 // comma between items, none after the last
    if (len + n >= (int)sizeof(chunk) - 2) { chunk[len]=0; httpServer.sendContent(chunk); len=0; }
    memcpy(chunk + len, row, n); len += n;
  }
  chunk[len++] = ']'; chunk[len] = 0;
  httpServer.sendContent(chunk);
}

// GET /wavejson  ->  the fast voltage trace (ring buffer) as a JSON array, in
// oldest-to-newest order, so a reconnecting browser can backfill the live graph.
void handleWaveJson() {
  httpServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
  httpServer.send(200,"application/json","");
  httpServer.sendContent("[");
  char b[16];
  // If the ring hasn't wrapped yet, start at 0; once full, the oldest sample is
  // the one we're about to overwrite (waveHead). Walk forward with modulo wrap.
  int start = (waveCount < WAVE_LEN) ? 0 : waveHead;
  for (int k=0; k<waveCount; k++) {
    int idx = (start + k) % WAVE_LEN;
    snprintf(b,sizeof(b),"%.3f%s", waveBuf[idx], (k==waveCount-1)?"":",");
    httpServer.sendContent(b);
  }
  httpServer.sendContent("]");
}

// GET /clear  ->  wipe the saved log from flash and reset the in-RAM state.
// The dashboard asks for confirmation first; this just does the deletion.
void handleClear() {
  LittleFS.remove(HIST_FILE);
  historyCount = 0;
  g_minPct     = 100.0f;     // forget the previous "lowest %" too
  httpServer.send(200, "text/plain", "cleared");
}

// 7-point median de-spike of one history column (used by /cleanup below).
// For each point we take the 3 neighbours on each side, sort that little
// window, and keep the MIDDLE value. The median ignores lone spikes/dips
// (glitch reads) while leaving real trends in place. `tmp` is scratch space
// holding the original column so neighbours aren't re-smoothed mid-pass.
void despikeCol(float* col, float* tmp, int n) {
  for (int i=0;i<n;i++) tmp[i] = col[i];          // snapshot the original column first
  for (int i=0;i<n;i++) {
    int lo = i-3; if (lo < 0)   lo = 0;            // clamp the +/-3 window at the array edges
    int hi = i+3; if (hi > n-1) hi = n-1;
    float w[7]; int c = 0;
    for (int k=lo;k<=hi;k++) w[c++] = tmp[k];      // gather the window (up to 7 values)
    // Insertion sort the small window (fast for <= 7 items, no extra memory)...
    for (int a=1;a<c;a++){ float v=w[a]; int b=a-1; while(b>=0 && w[b]>v){w[b+1]=w[b];b--;} w[b+1]=v; }
    col[i] = w[c/2];                               // ...then keep the median (middle element)
  }
}

// GET /cleanup  ->  de-spike the ALREADY-LOGGED history in place and re-save it.
// Every row is kept (same timeline), only obvious glitch spikes are smoothed.
void handleCleanup() {
  if (historyCount > 0) {
    // One scratch column shared by all three passes (allocated once, freed once).
    float* tmp = (float*) malloc(sizeof(float) * historyCount);
    if (!tmp) { httpServer.send(500, "text/plain", "out of memory"); return; }
    despikeCol(hVoltage, tmp, historyCount);
    despikeCol(hPercent, tmp, historyCount);
    despikeCol(hLow,     tmp, historyCount);
    free(tmp);

    // Rate isn't smoothed directly - it's recomputed from the now-clean % column.
    // (percent change between records) x (records per hour) = %/hr. Also refresh
    // the all-time lowest % from the cleaned data.
    g_minPct = 100.0f;
    for (int i=0;i<historyCount;i++) {
      hRate[i] = (i==0) ? 0.0f : (hPercent[i]-hPercent[i-1]) * (60.0f / MIN_PER_REC);
      if (hLow[i] < g_minPct) g_minPct = hLow[i];
    }

    File f = LittleFS.open(HIST_FILE, "w");          // "w" overwrites the file with the cleaned data
    if (f) {
      for (int i=0;i<historyCount;i++) {
        HistRec r = { hVoltage[i], hPercent[i], hRate[i], hLow[i] };
        f.write((const uint8_t*)&r, sizeof(r));
      }
      f.close();
    }
  }
  httpServer.send(200, "text/plain", "cleaned");
}

// ============================================================================
//  SENSOR - MAX17048 fuel gauge access (raw I2C, no library)
//  All the magic numbers below come straight from the MAX17048 datasheet.
// ============================================================================

// Read one 16-bit register: tell the chip which register, then read 2 bytes.
uint16_t readRegister16(uint8_t reg) {
  Wire.beginTransmission(MAX17048_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);                 // false = "repeated start": keep the bus so we can read back
  Wire.requestFrom(MAX17048_ADDR, (uint8_t)2);
  uint16_t value = Wire.read() << 8;           // first byte is the high byte (MSB first)
  value |= Wire.read();                        // second byte is the low byte
  return value;
}

// Quick "is the chip there?" probe: address it and see if it acknowledges.
bool deviceConnected() {
  Wire.beginTransmission(MAX17048_ADDR);
  return Wire.endTransmission() == 0;          // endTransmission() returns 0 when the chip ACKed
}

// Battery voltage in volts. The VCELL register counts in 78.125 microvolt steps
// (datasheet), so raw * 78.125e-6 = volts; then add our calibration offset.
float batteryVoltage() {
  return readRegister16(REG_VCELL) * 78.125e-6 + VOLT_CAL;
}

// Convert a 1S LiPo voltage to an approximate charge %. The MAX17048's own %
// estimate exists, but this lookup curve (voltage -> %) is used instead for a
// predictable result: 4.20 V = 100%, 3.30 V = 0%, straight-line between points.
// These pairs are a typical resting-LiPo discharge curve; tweak them if your
// cell behaves differently. We linearly interpolate between the two surrounding
// rows, and clamp anything above/below the ends to 100%/0%.
float batteryPercent(float v) {
  static const float curve[][2] = {            // { volts, percent }, high -> low
    {4.20, 100}, {4.10, 90}, {4.00, 80}, {3.92, 70}, {3.86, 60},
    {3.81,  50}, {3.78, 40}, {3.76, 30}, {3.73, 20}, {3.69, 10},
    {3.50,   5}, {3.30,  0}
  };
  const int n = sizeof(curve) / sizeof(curve[0]);     // number of rows in the curve
  if (v >= curve[0][0])   return 100.0;               // at/above the top point -> full
  if (v <= curve[n-1][0]) return 0.0;                 // at/below the bottom point -> empty
  for (int i = 0; i < n - 1; i++) {
    if (v >= curve[i + 1][0]) {                        // found the segment v sits in
      float vHi = curve[i][0],     pHi = curve[i][1];
      float vLo = curve[i + 1][0], pLo = curve[i + 1][1];
      return pLo + (v - vLo) * (pHi - pLo) / (vHi - vLo);  // linear interpolation
    }
  }
  return 0.0;
}

// Charge/discharge rate in %/hr. The CRATE register is SIGNED (positive while
// charging, negative while discharging) and counts 0.208 %/hr per step (datasheet).
float chargeRate() {
  int16_t raw = (int16_t)readRegister16(REG_CRATE);  // reinterpret the 16 bits as signed
  return raw * 0.208f;
}

// Take one full reading and publish it to the global g_* values - but only if
// it looks sane. Rejecting nonsense reads (loose wire, no battery, wrong rail)
// here keeps glitches out of the live graph and the saved log.
void readBattery() {
  if (!deviceConnected())      { g_sensorOk = false; return; }   // chip didn't answer
  float v = batteryVoltage();
  if (v < 2.0f || v > 4.35f)   { g_sensorOk = false; return; }   // outside a real 1S LiPo range -> reject
  g_sensorOk = true;
  g_voltage  = v;
  g_percent  = batteryPercent(v);
  g_rate     = chargeRate();
  if (g_percent < g_minPct) g_minPct = g_percent;                // track the lowest % of the run
}

// ============================================================================
//  PERSISTENCE - keep history & run timer across reboots / power loss
//  History lives in a binary LittleFS file (fixed-size HistRec records);
//  the run timer lives in NVS (Preferences), which tolerates frequent writes.
// ============================================================================

// Called once at boot: replay the saved log file back into the RAM arrays.
// We read fixed 16-byte HistRec structs until the file ends or RAM is full.
void loadHistory() {
  if (!LittleFS.exists(HIST_FILE)) return;
  File f = LittleFS.open(HIST_FILE, "r");
  if (!f) return;
  HistRec r;
  while (historyCount < MAX_HISTORY &&
         f.read((uint8_t*)&r, sizeof(r)) == sizeof(r)) {   // stop on a short/failed read (end of file)
    hVoltage[historyCount] = r.v;
    hPercent[historyCount] = r.p;
    hRate[historyCount]    = r.cr;
    hLow[historyCount]     = r.lo;
    if (r.lo < g_minPct) g_minPct = r.lo;                  // recover the all-time lowest %
    historyCount++;
  }
  f.close();
  Serial.printf("[FS] Restored %d log rows from flash\n", historyCount);
}

// Append one new record to the end of the log file ("a" = append mode).
void appendHistory(const HistRec& r) {
  File f = LittleFS.open(HIST_FILE, "a");
  if (!f) return;
  f.write((const uint8_t*)&r, sizeof(r));
  f.close();
}

// Resume the run timer from NVS. NVS is used (not LittleFS) because it handles
// the every-10-s writes gracefully. On the very first boot the "clk" key
// doesn't exist, so we fall back to SEED_TIMER_SEC. Skipped entirely if the
// user already typed a time at the boot prompt.
void loadClock() {
  if (clockSetByUser) return;
  prefs.begin("battmon", true);                              // open the "battmon" namespace read-only
  unsigned long saved = prefs.getULong("clk", 0xFFFFFFFFUL); // 0xFFFFFFFF is our "no value stored yet" sentinel
  prefs.end();
  baseSec = (saved == 0xFFFFFFFFUL) ? SEED_TIMER_SEC : saved;
  Serial.printf("[CLOCK] Resumed at %02lu:%02lu:%02lu\n",
                baseSec / 3600, (baseSec % 3600) / 60, baseSec % 60);
}

// Save the current run-timer total (seconds) to NVS so a reset barely loses time.
void saveClock(unsigned long totalSec) {
  prefs.begin("battmon", false);                            // open read-write
  prefs.putULong("clk", totalSec);
  prefs.end();
}

// (No Wi-Fi event handler is needed in Access-Point mode - our own AP is always up.)

// ============================================================================
//  SETUP - runs once at power-up: ask for the time, then bring everything up
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  // -- Optional boot prompt: type HH:MM:SS to SET the run timer, or wait 15 s --
  // Within the countdown you can seed the run clock; do nothing and we resume
  // the saved timer in loadClock() below. The window is 15000 ms - change that
  // number to give more/less time to type.
  delay(300);
  while (Serial.available()) Serial.read();   // throw away boot/upload junk so it
                                              // isn't mistaken for your typed input
  Serial.println("\nType HH:MM:SS to SET the run timer, or wait to RESUME the saved one.");
  Serial.println("Counting down 15 s:");
  String tin = "";                            // characters typed so far
  bool   gotLine = false;                     // set true once a full line is entered
  int    lastSec = -1;                        // so we only print each countdown number once
  unsigned long tStart = millis();
  while (millis() - tStart < 15000 && !gotLine) {
    int remain = 15 - (int)((millis() - tStart) / 1000);
    if (remain != lastSec) { Serial.printf("  %d\n", remain); lastSec = remain; }
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n' || c == '\r') { if (tin.length()) { gotLine = true; break; } }  // Enter ends input (if anything was typed)
      else if (c >= ' ')          { tin += c; }   // keep printable chars, ignore control chars
    }
    delay(20);                                // small pause so we don't spin the CPU
  }
  int hh = 0, mm = 0, ss = 0;
  // Accept the time if at least HH:MM parsed (seconds optional, hence ">= 2").
  if (tin.length() && sscanf(tin.c_str(), "%d:%d:%d", &hh, &mm, &ss) >= 2) {
    baseSec = (unsigned long)hh * 3600UL + mm * 60UL + ss;   // total seconds; counts up forever (no 24 h wrap)
    clockSetByUser = true;                                   // so loadClock() leaves this alone
    Serial.printf("[CLOCK] Timer set to %02d:%02d:%02d\n", hh, mm, ss);
  } else {
    Serial.println("[CLOCK] No input - resuming the saved timer.");
  }

  // -- MAX17048 fuel gauge on the I2C bus --
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!deviceConnected()) {
    Serial.println("[ERROR] MAX17048 not found! Check wiring "
                   "(VBAT->battery+, GND common, SDA=21, SCL=22).");
    // Not fatal on purpose: we keep going so the dashboard still loads and can
    // show "Sensor offline" instead of refusing to boot.
  } else {
    Serial.println("[OK] MAX17048 ready");
  }

  // -- Flash storage: mount the file system and restore any log from before a reboot --
  if (!LittleFS.begin(true)) {                 // true = format if the FS is missing/corrupt
    Serial.println("[FS] LittleFS mount failed - history won't persist.");
  } else {
    loadHistory();
    loadClock();     // resume the run timer where it left off (unless set above)
  }

  // -- Start our OWN Wi-Fi network (Access Point) so no router is needed --
  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);
  bool apOk = WiFi.softAP(AP_SSID, AP_PASSWORD);
  WiFi.setSleep(false);                         // disable Wi-Fi power saving so the dashboard stays snappy
  Serial.printf("[AP] %s - connect to WiFi \"%s\" (pass: %s)\n",
                apOk ? "Network up" : "FAILED to start", AP_SSID, AP_PASSWORD);
  Serial.printf("[AP] Then open the dashboard at http://%s\n",
                WiFi.softAPIP().toString().c_str());
  if (MDNS.begin(MDNS_HOST)) MDNS.addService("http", "tcp", 80);  // enables http://battery.local

  // -- HTTP routes: map each URL to its handler defined above --
  httpServer.on("/",         handleRoot);
  httpServer.on("/download", handleDownload);
  httpServer.on("/histjson", handleHistJson);
  httpServer.on("/wavejson", handleWaveJson);
  httpServer.on("/clear",    handleClear);
  httpServer.on("/cleanup",  handleCleanup);
  httpServer.begin();

  // -- WebSocket server for the live data stream --
  // enableHeartbeat(pingEvery, pongTimeout, dropAfter): ping every 10 s, expect
  // a reply within 3 s, and drop a client after 2 missed pings (frees dead sockets).
  wsServer.begin();
  wsServer.enableHeartbeat(10000, 3000, 2);

  // Capture the start time and align all the "do every N ms" timers to it.
  startMs       = millis();
  lastFastMs    = startMs;
  lastHistoryMs = startMs;
  lastWsMs      = startMs;

  Serial.println("[RUN] Battery monitor started.");
}

// ============================================================================
//  LOOP - runs forever; everything is time-sliced with millis(), never delay()
//  so the web server and WebSocket stay responsive the whole time.
// ============================================================================
void loop() {
  httpServer.handleClient();   // service any pending web requests
  wsServer.loop();             // service the WebSocket (pings, new clients, etc.)

  unsigned long now = millis();

  // (No Wi-Fi watchdog needed in AP mode - the ESP hosts its own network.)

  // -- FAST SAMPLE: read the gauge and feed the live trace/accumulators --
  if (now - lastFastMs >= FAST_SAMPLE_MS) {
    lastFastMs = now;
    readBattery();

    if (g_sensorOk) {                  // only record real data, never rejected reads
      // Push this voltage into the ring buffer (used to backfill a reconnecting browser).
      waveBuf[waveHead] = g_voltage;
      waveHead = (waveHead + 1) % WAVE_LEN;   // advance and wrap around
      if (waveCount < WAVE_LEN) waveCount++;

      // Add to the running totals so the next history record is an average.
      intSumV += g_voltage;
      intSumP += g_percent;
      intSamples++;
      if (g_percent < intMinP) intMinP = g_percent;
    }

    // Rebuild the "HH:MM:SS" run-timer string. It counts elapsed seconds since
    // boot on top of baseSec and keeps growing past 24 h (no wrap by design).
    unsigned long clk = baseSec + (now - startMs) / 1000UL;
    int rh = clk / 3600;
    int rm = (clk % 3600) / 60;
    int rs = clk % 60;
    sprintf(runTimeStr, "%02d:%02d:%02d", rh, rm, rs);
  }

  // -- WEBSOCKET PUSH: send the latest live values to every connected browser --
  if (now - lastWsMs >= FAST_SAMPLE_MS) {
    lastWsMs = now;
    char msg[200];
    snprintf(msg, sizeof(msg),
      "{\"t\":\"live\",\"ok\":%d,\"v\":%.3f,\"p\":%.1f,\"cr\":%.2f,\"lo\":%.1f,\"rt\":\"%s\"}",
      g_sensorOk ? 1 : 0, g_voltage, g_percent, g_rate, g_minPct, runTimeStr);
    wsServer.broadcastTXT(msg);
  }

  // -- Persist the run timer to NVS every CLOCK_SAVE_MS (10 s) --
  if (now - lastClockMs >= CLOCK_SAVE_MS) {
    lastClockMs = now;
    saveClock(baseSec + (now - startMs) / 1000UL);
  }

  // -- HISTORY SNAPSHOT: every HISTORY_SAVE_MS (5 min) save one averaged record --
  if (now - lastHistoryMs >= HISTORY_SAVE_MS) {
    lastHistoryMs = now;

    // Only log if we gathered samples and there's room. intSamples==0 means the
    // sensor was offline the whole interval, so we skip that empty record.
    if (intSamples > 0 && historyCount < MAX_HISTORY) {
      HistRec r = { intSumV / intSamples, intSumP / intSamples, g_rate, intMinP };
      hVoltage[historyCount] = r.v;
      hPercent[historyCount] = r.p;
      hRate[historyCount]    = r.cr;
      hLow[historyCount]     = r.lo;
      historyCount++;
      appendHistory(r);                                   // also write it to flash

      // Tell live browsers about the brand-new record so their table/graph update.
      char hmsg[200];
      snprintf(hmsg, sizeof(hmsg),
        "{\"t\":\"hist\",\"min\":%d,\"v\":%.3f,\"p\":%.1f,\"cr\":%.2f,\"lo\":%.1f}",
        historyCount*MIN_PER_REC, r.v, r.p, r.cr, r.lo);
      wsServer.broadcastTXT(hmsg);
    }

    // Reset the accumulators for the next interval (whether or not we logged).
    intSumV = 0.0f; intSumP = 0.0f; intSamples = 0; intMinP = 100.0f;
  }
}
