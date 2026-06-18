/*
 ============================================================================
    _____           _   _                   _
   |  ___|   _  ___| | | |    _____   _____| |
   | |_ | | | |/ _ \ | | |   / _ \ \ / / _ \ |
   |  _|| |_| |  __/ | | |__|  __/\ V /  __/ |
   |_|   \__,_|\___|_| |_____\___| \_/ \___|_|
 ============================================================================
   PROJECT   : Ahmed's Fuel Level - ESP32 Wi-Fi Truck Fuel Monitor
   AUTHOR    : Ahmed Yousef Saeed Khalifa
   COPYRIGHT : (c) Ahmed Yousef Saeed Khalifa - All rights reserved.
 ----------------------------------------------------------------------------
   WHAT THIS DOES:
   ---------------------------------
   The ESP32 reads a fuel-level probe many times a second, cleans up the
   noisy reading, and turns it into a steady "tank is X% full" number. It
   also watches for sudden drops (possible fuel theft) and sudden rises
   (a refuel). Finally it hosts its own Wi-Fi network and serves a live
   web dashboard so you can watch the tank, alerts, and history from a
   phone or laptop - no internet or router needed.

   NOTE: During bench testing a Funduino water-level sensor stands in for a
   real fuel sender. The whole pipeline (read -> filter -> % -> alerts) is
   identical for a real capacitive/resistive fuel probe; you only re-tune
   the CAL_TABLE calibration points below for your actual tank.
 ----------------------------------------------------------------------------
   WIRING  (ESP32 dev board)
   -------------------------
     Sensor signal (S / "A0") .......... GPIO34   (analog input, see note)
     Sensor power  (+ / VCC) ........... 3.3V
     Sensor ground (- / GND) ........... GND

     Why GPIO34? It is an "input-only" ADC pin on the ESP32, perfect for a
     sensor. The ADC is read at 12-bit resolution, so raw values run 0..4095.
 ----------------------------------------------------------------------------
   HOW TO USE  (no app to install)
   -------------------------------
     1. Power up the ESP32.
     2. On a phone/laptop, join the Wi-Fi network:
              SSID     : TruckFuel_Monitor
              Password : fuelguard123
     3. Open a browser to:  http://192.168.4.1
     4. The dashboard auto-refreshes every second. The "Clear Data" button
        wipes events/history (it POSTs to the /clear endpoint).
 ----------------------------------------------------------------------------
   HTTP ENDPOINTS the ESP32 serves
   -------------------------------
     GET  /        -> the dashboard web page (HTML below)
     GET  /data    -> live readings as JSON (the page polls this each second)
     POST /clear   -> resets counters, history and the event log
 ============================================================================
*/

#include <WiFi.h>
#include <WebServer.h>

/* ===========================================================================
 *  SECTION 1 - WI-FI ACCESS POINT
 *  The ESP32 creates its OWN Wi-Fi network (Access Point mode), so the
 *  dashboard works in the field with no router. Change these two lines to
 *  rename the network or set your own password (min 8 chars for WPA2).
 * ========================================================================= */
const char* ssid     = "TruckFuel_Monitor";
const char* password = "fuelguard123";

WebServer server(80);   // Plain HTTP on the standard port 80

/* ===========================================================================
 *  SECTION 2 - HARDWARE PINS
 * ========================================================================= */
const int sensorPin = 34;   // Analog input from the fuel/level probe (GPIO34)

/* ===========================================================================
 *  SECTION 3 - CALIBRATION  (raw ADC  ->  fuel %)
 *  ---------------------------------------------------------------------------
 *  The Funduino water sensor is strongly NON-linear: it is "squashed" near
 *  the top and hyper-sensitive near the bottom, so a single empty/full
 *  mapping won't fit. Instead we list a few measured (ADC, %) points and
 *  draw straight lines between them (a piecewise lookup table).
 *
 *  To calibrate for YOUR tank: note the raw ADC value at known fill levels
 *  (watch the Serial monitor) and edit the numbers below. The table must
 *  stay sorted by ascending adc.
 * ========================================================================= */
struct CalPoint { int adc; float pct; };
const CalPoint CAL_TABLE[] = {
  {    0,   0.0 },
  { 1500,  10.0 },
  { 2050,  25.0 },
  { 2150,  50.0 },
  { 2250,  75.0 },
  { 2350, 100.0 }   // tweak if your physical "full" reads higher/lower
};
const int CAL_N = sizeof(CAL_TABLE) / sizeof(CAL_TABLE[0]);  // number of points

const float TANK_CAPACITY_L = 200.0;   // Truck tank size in liters (used to turn % into liters)

/* ===========================================================================
 *  SECTION 4 - DETECTION & FILTER TUNING
 *  ---------------------------------------------------------------------------
 *  These knobs decide how twitchy the alerts are and how smooth the reading
 *  looks. The notes say what each one means and what happens if you change it.
 * ========================================================================= */
const float THEFT_DROP_PCT   = 2.0;    // Level falls this many % below baseline -> THEFT alert. Lower = more sensitive (more false alarms).
const float REFUEL_RISE_PCT  = 2.0;    // Level rises this many % above baseline -> REFUEL event.
const float BASELINE_DRIFT   = 0.002;  // How fast the "normal" baseline follows slow consumption (per sample). Bigger = forgets faster, so slow siphoning may slip under the alert.
const int   MEDIAN_N         = 5;      // Median pre-filter window. Throws away spikes/0-dropouts. Must be odd-ish; bigger = steadier but slower to react.
const int   AVG_N            = 30;     // Moving-average window. Smooths jitter. 30 samples at 20 Hz = ~1.5 s of smoothing.
const float DEADBAND_PCT     = 1.0;    // Displayed % stays put until it moves at least this much - stops the number from flickering on noise.

const unsigned long ALERT_HOLD_MS    = 10000;  // How long a THEFT/REFUEL banner stays up before auto-returning to NORMAL (10 s).
const unsigned long SAMPLE_INTERVAL  = 50;     // Read the sensor every 50 ms  (= 20 times/second).
const unsigned long SERIAL_INTERVAL  = 1000;   // Print a status line to Serial once per second.
const unsigned long HISTORY_INTERVAL = 2000;   // Save one history point every 2 s (60 points = a 2-minute trend on the chart).

/* ===========================================================================
 *  SECTION 5 - RUNTIME STATE  (the live values the program keeps in memory)
 * ========================================================================= */

// Latest raw + smoothed sensor readings
int   rawValue    = 0;   // Most recent raw ADC reading (0..4095)
float smoothedRaw = 0;   // Reading after median + moving-average filtering

// Ring buffer for the median pre-filter (kills outliers / dropouts)
int   medBuf[MEDIAN_N];
int   medIdx   = 0;
int   medCount = 0;

// Ring buffer for the moving average (smooths jitter). We keep a running
// sum so each update is O(1) instead of re-adding the whole window.
int   avgBuf[AVG_N];
int   avgIdx   = 0;
int   avgCount = 0;
long  avgSum   = 0;

// Derived fuel values + the slow "baseline" used for theft/refuel comparison
float fuelPct      = 0;       // Current tank fill, 0..100 %
float fuelLiters   = 0;       // Same thing expressed in liters
float baselinePct  = 0;       // Slow-moving reference level we compare against
bool  baselineInit = false;   // Has the baseline been seeded from the first reading yet?

// Current alert state shown on the dashboard
String currentStatus     = "NORMAL";   // NORMAL / THEFT_ALERT / REFUELING / LOW_FUEL
unsigned long statusChangedAt = 0;      // millis() when the status last changed (for the auto-clear timer)

// Running tallies shown in the Telemetry card
float totalStolenL = 0;   // Sum of all detected fuel drops, in liters
int   theftEvents  = 0;   // Count of theft alerts
int   refuelEvents = 0;   // Count of refuel events

// Event log - a small circular buffer; newest entry is at (eventHead - 1)
struct Event {
  unsigned long t;   // millis() timestamp when it happened
  String type;       // THEFT / REFUEL / LOW_FUEL / INFO
  String msg;        // human-readable description
};
const int MAX_EVENTS = 10;       // Keep only the last 10 events
Event events[MAX_EVENTS];
int eventHead  = 0;
int eventCount = 0;

// Fuel-level history that feeds the trend chart
const int HIST_SIZE = 60;        // 60 points * HISTORY_INTERVAL(2 s) = 2 minutes of trend
float history[HIST_SIZE];
int   histIdx   = 0;
int   histCount = 0;

// "Last time we did X" stamps for the cooperative scheduler in loop()
unsigned long lastSampleAt  = 0;
unsigned long lastSerialAt  = 0;
unsigned long lastHistoryAt = 0;

/* ===========================================================================
 *  SECTION 6 - DASHBOARD WEB PAGE
 *  ---------------------------------------------------------------------------
 *  The entire dashboard (HTML + CSS + JavaScript) lives in this one string
 *  and is stored in flash (PROGMEM) to save RAM. The browser runs the JS,
 *  which fetches /data once a second and redraws the gauge, chart and log.
 *  (Left exactly as served - editing it changes what the browser receives.)
 * ========================================================================= */
const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE HTML><html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Truck Fuel Monitor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Tahoma,sans-serif;background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;min-height:100vh;padding:20px}
.header{text-align:center;margin-bottom:24px;padding:24px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px}
.header h1{font-size:1.8rem;margin-bottom:6px;letter-spacing:.5px}
.header p{color:#94a3b8;font-size:.9rem}
.live-dot{display:inline-block;width:8px;height:8px;background:#22c55e;border-radius:50%;margin-right:6px;animation:blink 1.5s infinite}
@keyframes blink{50%{opacity:.3}}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;max-width:1200px;margin:0 auto}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:22px}
.card-title{font-size:.78rem;text-transform:uppercase;letter-spacing:1.2px;color:#94a3b8;margin-bottom:18px;font-weight:600}
.title-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.title-row .card-title{margin-bottom:0}
.clear-btn{background:#7f1d1d;color:#fecaca;border:1px solid #ef4444;padding:7px 14px;border-radius:8px;font-weight:600;font-size:.75rem;letter-spacing:.8px;text-transform:uppercase;cursor:pointer;transition:all .2s}
.clear-btn:hover{background:#991b1b;color:#fff}
.clear-btn:active{transform:scale(.97)}
.gauge-wrap{display:flex;justify-content:center;align-items:center}
.gauge{position:relative;width:240px;height:240px}
.gauge svg{transform:rotate(-90deg)}
.gauge-bg{stroke:rgba(255,255,255,.08)}
.gauge-fg{stroke:#22c55e;transition:stroke-dashoffset .6s ease,stroke .4s ease;filter:drop-shadow(0 0 8px currentColor)}
.gauge-text{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;width:100%}
.gauge-percent{font-size:3.2rem;font-weight:700;line-height:1}
.gauge-liters{color:#94a3b8;font-size:1rem;margin-top:6px}
.status-wrap{text-align:center;margin-top:18px}
.status{display:inline-block;padding:9px 20px;border-radius:24px;font-weight:700;font-size:.85rem;letter-spacing:1px;text-transform:uppercase}
.status-NORMAL{background:#14532d;color:#86efac;border:1px solid #22c55e}
.status-THEFT_ALERT{background:#7f1d1d;color:#fecaca;border:1px solid #ef4444;animation:pulse 1s infinite}
.status-REFUELING{background:#1e3a8a;color:#bfdbfe;border:1px solid #3b82f6}
.status-LOW_FUEL{background:#713f12;color:#fde68a;border:1px solid #f59e0b}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.7)}50%{box-shadow:0 0 0 12px rgba(239,68,68,0)}}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.stat{background:rgba(255,255,255,.03);padding:14px;border-radius:10px;border:1px solid rgba(255,255,255,.05)}
.stat-label{color:#94a3b8;font-size:.7rem;text-transform:uppercase;letter-spacing:.8px;font-weight:600}
.stat-value{font-size:1.5rem;font-weight:700;margin-top:6px}
.stat-value.danger{color:#f87171}
.stat-value.info{color:#60a5fa}
.events{max-height:280px;overflow-y:auto}
.events::-webkit-scrollbar{width:6px}
.events::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:3px}
.event{padding:11px 13px;border-left:3px solid #22c55e;background:rgba(255,255,255,.03);border-radius:6px;margin-bottom:8px;font-size:.85rem}
.event.THEFT{border-color:#ef4444;background:rgba(239,68,68,.08)}
.event.REFUEL{border-color:#3b82f6;background:rgba(59,130,246,.08)}
.event.LOW_FUEL{border-color:#f59e0b;background:rgba(245,158,11,.08)}
.event-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.event-type{font-weight:700;letter-spacing:.5px;font-size:.75rem}
.event-time{color:#94a3b8;font-size:.72rem}
.event-msg{color:#cbd5e1}
.empty{color:#64748b;text-align:center;padding:30px;font-size:.85rem}
canvas{width:100%;height:180px;background:rgba(0,0,0,.25);border-radius:8px;border:1px solid rgba(255,255,255,.05)}
.chart-card{grid-column:span 2}
@media(max-width:768px){.grid{grid-template-columns:1fr}.chart-card{grid-column:span 1}}
</style>
</head>
<body>
<div class="header">
<h1>Truck Fuel Monitor</h1>
<p><span class="live-dot"></span>Real-time fuel level &amp; theft detection</p>
</div>
<div class="grid">

<div class="card">
<div class="card-title">Fuel Level</div>
<div class="gauge-wrap">
<div class="gauge">
<svg width="240" height="240">
<circle class="gauge-bg" cx="120" cy="120" r="100" stroke-width="16" fill="none"/>
<circle id="gFg" class="gauge-fg" cx="120" cy="120" r="100" stroke-width="16" fill="none" stroke-linecap="round"/>
</svg>
<div class="gauge-text">
<div id="pct" class="gauge-percent">--</div>
<div id="liters" class="gauge-liters">-- L</div>
</div>
</div>
</div>
<div class="status-wrap"><span id="status" class="status status-NORMAL">NORMAL</span></div>
</div>

<div class="card">
<div class="card-title">Telemetry</div>
<div class="stats">
<div class="stat"><div class="stat-label">Raw ADC</div><div id="raw" class="stat-value">--</div></div>
<div class="stat"><div class="stat-label">Tank Capacity</div><div class="stat-value">200 L</div></div>
<div class="stat"><div class="stat-label">Theft Events</div><div id="theftCount" class="stat-value danger">0</div></div>
<div class="stat"><div class="stat-label">Total Stolen</div><div id="stolen" class="stat-value danger">0.0 L</div></div>
<div class="stat"><div class="stat-label">Refuel Events</div><div id="refuelCount" class="stat-value info">0</div></div>
<div class="stat"><div class="stat-label">Uptime</div><div id="uptime" class="stat-value">--</div></div>
</div>
</div>

<div class="card chart-card">
<div class="card-title">Fuel Level Trend (last 2 minutes)</div>
<canvas id="chart"></canvas>
</div>

<div class="card chart-card">
<div class="title-row">
<div class="card-title">Event Log</div>
<button id="clearBtn" class="clear-btn">Clear Data</button>
</div>
<div id="events" class="events"><div class="empty">No events yet</div></div>
</div>

</div>
<script>
const gFg=document.getElementById('gFg');
const R=100,CIRC=2*Math.PI*R;
gFg.setAttribute('stroke-dasharray',CIRC);
gFg.setAttribute('stroke-dashoffset',CIRC);

let chartData=[];
const cv=document.getElementById('chart');
const ctx=cv.getContext('2d');

function drawChart(){
  const w=cv.width=cv.offsetWidth*window.devicePixelRatio;
  const h=cv.height=cv.offsetHeight*window.devicePixelRatio;
  ctx.scale(1,1);
  cv.style.width=cv.offsetWidth+'px';
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='rgba(255,255,255,.05)';
  ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
    const y=(h/4)*i;
    ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();
  }
  if(chartData.length<2)return;
  ctx.beginPath();
  ctx.moveTo(0,h);
  chartData.forEach((v,i)=>{
    const x=chartData.length===1?w/2:(i/(chartData.length-1))*w;
    const y=h-(v/100)*h;
    ctx.lineTo(x,y);
  });
  ctx.lineTo(w,h);ctx.closePath();
  const grad=ctx.createLinearGradient(0,0,0,h);
  grad.addColorStop(0,'rgba(34,197,94,.35)');
  grad.addColorStop(1,'rgba(34,197,94,0)');
  ctx.fillStyle=grad;ctx.fill();
  ctx.beginPath();
  chartData.forEach((v,i)=>{
    const x=chartData.length===1?w/2:(i/(chartData.length-1))*w;
    const y=h-(v/100)*h;
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  });
  ctx.strokeStyle='#22c55e';ctx.lineWidth=2.5;ctx.stroke();
}

function fmtTime(s){
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
}
function ago(s){
  if(s<60)return s+'s ago';
  if(s<3600)return Math.floor(s/60)+'m '+(s%60)+'s ago';
  return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m ago';
}

function update(){
  fetch('/data').then(r=>r.json()).then(d=>{
    document.getElementById('pct').textContent=d.pct.toFixed(1)+'%';
    document.getElementById('liters').textContent=d.liters.toFixed(1)+' L';
    document.getElementById('raw').textContent=d.raw;
    document.getElementById('theftCount').textContent=d.theftCount;
    document.getElementById('stolen').textContent=d.stolenL.toFixed(1)+' L';
    document.getElementById('refuelCount').textContent=d.refuelCount;
    document.getElementById('uptime').textContent=fmtTime(d.uptime);
    const offset=CIRC-(Math.max(0,Math.min(100,d.pct))/100)*CIRC;
    gFg.setAttribute('stroke-dashoffset',offset);
    let color='#22c55e';
    if(d.pct<15)color='#ef4444';
    else if(d.pct<30)color='#f59e0b';
    gFg.setAttribute('stroke',color);
    const st=document.getElementById('status');
    st.className='status status-'+d.status;
    st.textContent=d.status.replace('_',' ');
    chartData=d.history;
    drawChart();
    const ev=document.getElementById('events');
    if(d.events.length===0){
      ev.innerHTML='<div class="empty">No events yet</div>';
    }else{
      ev.innerHTML=d.events.map(e=>`
        <div class="event ${e.type}">
          <div class="event-head"><span class="event-type">${e.type.replace('_',' ')}</span><span class="event-time">${ago(e.t)}</span></div>
          <div class="event-msg">${e.msg}</div>
        </div>`).join('');
    }
  }).catch(()=>{});
}
setInterval(update,1000);update();
window.addEventListener('resize',drawChart);

document.getElementById('clearBtn').addEventListener('click',()=>{
  if(!confirm('Clear all theft events, refuel events, history, and stolen total?'))return;
  fetch('/clear',{method:'POST'}).then(()=>update());
});
</script>
</body></html>
)rawliteral";

/* ===========================================================================
 *  SECTION 7 - SIGNAL-PROCESSING HELPERS
 *  ---------------------------------------------------------------------------
 *  Small, pure-ish functions that turn the raw, noisy ADC reading into a
 *  stable fuel percentage. Order of use: medianFilter -> movingAverage ->
 *  adcToPercent.
 * ========================================================================= */

// Median filter: returns the MIDDLE value of the last MEDIAN_N samples.
// A median ignores the odd wild reading (e.g. a momentary 0 dropout) because
// one outlier can't move the middle of the pack - unlike an average, which it
// would drag. We copy the ring buffer, sort the copy, and pick the center.
int medianFilter(int v) {
  medBuf[medIdx] = v;
  medIdx = (medIdx + 1) % MEDIAN_N;
  if (medCount < MEDIAN_N) medCount++;

  int tmp[MEDIAN_N];
  for (int i = 0; i < medCount; i++) tmp[i] = medBuf[i];

  // Insertion sort - tiny window, so this is plenty fast and simple.
  for (int i = 1; i < medCount; i++) {
    int x = tmp[i], j = i - 1;
    while (j >= 0 && tmp[j] > x) { tmp[j + 1] = tmp[j]; j--; }
    tmp[j + 1] = x;
  }
  return tmp[medCount / 2];   // the middle element = the median
}

// Moving average: the mean of the last AVG_N samples. We keep a running sum
// (add the new sample, subtract the one leaving the window) so each call is
// constant-time no matter how big AVG_N is.
float movingAverage(int v) {
  if (avgCount < AVG_N) {
    // Window not full yet - just grow it.
    avgBuf[avgIdx] = v;
    avgSum += v;
    avgCount++;
  } else {
    // Window full - evict the oldest sample, then add the new one.
    avgSum -= avgBuf[avgIdx];
    avgBuf[avgIdx] = v;
    avgSum += v;
  }
  avgIdx = (avgIdx + 1) % AVG_N;
  return (float)avgSum / avgCount;
}

// Convert a (smoothed) ADC value into a fuel percentage using the piecewise
// CAL_TABLE. Below the first point we clamp to its %, above the last we clamp
// to its %, and in between we linearly interpolate along the matching segment.
float adcToPercent(float adc) {
  if (adc <= CAL_TABLE[0].adc)         return CAL_TABLE[0].pct;          // at/under "empty"
  if (adc >= CAL_TABLE[CAL_N - 1].adc) return CAL_TABLE[CAL_N - 1].pct;  // at/over "full"

  for (int i = 0; i < CAL_N - 1; i++) {
    int aLo = CAL_TABLE[i].adc;
    int aHi = CAL_TABLE[i + 1].adc;
    if (adc >= aLo && adc <= aHi) {
      float pLo = CAL_TABLE[i].pct;
      float pHi = CAL_TABLE[i + 1].pct;
      float r   = (adc - aLo) / (float)(aHi - aLo);   // 0..1 position within this segment
      return pLo + r * (pHi - pLo);                   // straight-line blend between the two %s
    }
  }
  return 0;   // unreachable in practice (table is sorted and covers the range)
}

/* ===========================================================================
 *  SECTION 8 - EVENT LOG & STATUS HELPERS
 * ========================================================================= */

// Push a new entry onto the circular event log, overwriting the oldest once
// the buffer (MAX_EVENTS) is full.
void addEvent(const String& type, const String& msg) {
  events[eventHead] = { millis(), type, msg };
  eventHead = (eventHead + 1) % MAX_EVENTS;
  if (eventCount < MAX_EVENTS) eventCount++;
}

// Change the dashboard status and remember WHEN it changed, so the alert
// auto-clear timer (ALERT_HOLD_MS) can measure how long it has been showing.
void setStatus(const String& s) {
  currentStatus = s;
  statusChangedAt = millis();
}

/* ===========================================================================
 *  SECTION 9 - MAIN MEASUREMENT LOGIC
 *  ---------------------------------------------------------------------------
 *  Called 20x per second. Reads the sensor, filters it, updates the fuel %,
 *  and decides whether a theft / refuel / low-fuel event just happened.
 * ========================================================================= */
void sampleSensor() {
  rawValue = analogRead(sensorPin);

  // Two-stage clean-up: median removes outliers, moving average smooths jitter.
  int med = medianFilter(rawValue);
  smoothedRaw = movingAverage(med);

  // Map the smoothed ADC to a %, then apply a deadband: we only accept the new
  // % if it moved at least DEADBAND_PCT, otherwise the displayed number would
  // jitter forever on tiny noise. (First reading always passes, to seed it.)
  float newPct = adcToPercent(smoothedRaw);
  if (!baselineInit || fabs(newPct - fuelPct) >= DEADBAND_PCT) {
    fuelPct = newPct;
  }
  fuelLiters = (fuelPct / 100.0) * TANK_CAPACITY_L;

  // On the very first valid sample, seed the baseline and bail out - there is
  // nothing to compare against yet.
  if (!baselineInit) {
    baselinePct = fuelPct;
    baselineInit = true;
    addEvent("INFO", "System initialized. Baseline set at " + String(fuelPct, 1) + "%");
    return;
  }

  // Compare the current level against the slow baseline.
  //   big DROP  -> someone may be stealing fuel
  //   big RISE  -> tank was refilled
  //   small move-> let the baseline gently drift to follow normal burn
  float delta = fuelPct - baselinePct;

  if (delta <= -THEFT_DROP_PCT) {
    float stolenL = (-delta / 100.0) * TANK_CAPACITY_L;
    totalStolenL += stolenL;
    theftEvents++;
    addEvent("THEFT", "Fuel drop of " + String(-delta, 1) + "% (" + String(stolenL, 1) + " L)");
    setStatus("THEFT_ALERT");
    baselinePct = fuelPct;            // re-anchor so we don't re-fire on the same drop
  } else if (delta >= REFUEL_RISE_PCT) {
    float addedL = (delta / 100.0) * TANK_CAPACITY_L;
    refuelEvents++;
    addEvent("REFUEL", "Tank refilled by " + String(delta, 1) + "% (" + String(addedL, 1) + " L)");
    setStatus("REFUELING");
    baselinePct = fuelPct;            // re-anchor at the new full level
  } else {
    // Normal slow consumption: nudge the baseline a hair toward the reading so
    // gradual burn never looks like theft. (This is an exponential moving avg.)
    baselinePct = baselinePct * (1 - BASELINE_DRIFT) + fuelPct * BASELINE_DRIFT;
  }

  // A THEFT/REFUEL banner is temporary - drop back to NORMAL after the hold time.
  if ((currentStatus == "THEFT_ALERT" || currentStatus == "REFUELING")
      && millis() - statusChangedAt > ALERT_HOLD_MS) {
    currentStatus = "NORMAL";
  }

  // Low-fuel warning. It only takes over when nothing more urgent is showing,
  // so an active theft/refuel alert is never hidden by it.
  if (currentStatus == "NORMAL" && fuelPct < 15.0) {
    if (currentStatus != "LOW_FUEL") {
      addEvent("LOW_FUEL", "Fuel level below 15% (" + String(fuelPct, 1) + "%)");
    }
    currentStatus = "LOW_FUEL";
  } else if (currentStatus == "LOW_FUEL" && fuelPct >= 15.0) {
    currentStatus = "NORMAL";
  }
}

// Save one fuel-level data point for the trend chart (circular buffer).
void recordHistory() {
  history[histIdx] = fuelPct;
  histIdx = (histIdx + 1) % HIST_SIZE;
  if (histCount < HIST_SIZE) histCount++;
}

// One-line heartbeat to the Serial monitor so you can watch raw + % live.
void printSerial() {
  Serial.print("ADC: ");
  Serial.print(rawValue);
  Serial.print("  |  Fuel: ");
  Serial.print(fuelPct, 1);
  Serial.println(" %");
}

/* ===========================================================================
 *  SECTION 10 - JSON FOR THE DASHBOARD  (response to GET /data)
 *  ---------------------------------------------------------------------------
 *  Hand-built JSON string. The field names and shape here must match what the
 *  JavaScript in the web page expects, so don't rename keys without updating
 *  the page too.
 * ========================================================================= */
String buildJson() {
  String j = "{";
  j += "\"raw\":"         + String(rawValue) + ",";
  j += "\"pct\":"         + String(fuelPct, 2) + ",";
  j += "\"liters\":"      + String(fuelLiters, 2) + ",";
  j += "\"status\":\""    + currentStatus + "\",";
  j += "\"theftCount\":"  + String(theftEvents) + ",";
  j += "\"stolenL\":"     + String(totalStolenL, 2) + ",";
  j += "\"refuelCount\":" + String(refuelEvents) + ",";
  j += "\"uptime\":"      + String(millis() / 1000) + ",";

  // history[] in chronological order (oldest -> newest) for the trend line.
  j += "\"history\":[";
  int start = (histCount < HIST_SIZE) ? 0 : histIdx;   // where the oldest sample lives
  for (int i = 0; i < histCount; i++) {
    int idx = (start + i) % HIST_SIZE;
    if (i > 0) j += ",";
    j += String(history[idx], 1);
  }
  j += "],";

  // events[] newest-first; "t" is "seconds ago" so the page can show "5s ago".
  j += "\"events\":[";
  unsigned long now = millis();
  for (int i = 0; i < eventCount; i++) {
    int idx = (eventHead - 1 - i + MAX_EVENTS) % MAX_EVENTS;   // walk backwards from newest
    if (i > 0) j += ",";
    j += "{\"type\":\"" + events[idx].type + "\",";
    j += "\"msg\":\""   + events[idx].msg  + "\",";
    j += "\"t\":"       + String((now - events[idx].t) / 1000) + "}";
  }
  j += "]}";
  return j;
}

// Reset everything the user can see - counters, stolen total, history and the
// event log - and force the baseline to re-seed on the next reading.
void clearAll() {
  theftEvents   = 0;
  refuelEvents  = 0;
  totalStolenL  = 0;
  eventHead     = 0;
  eventCount    = 0;
  histIdx       = 0;
  histCount     = 0;
  for (int i = 0; i < HIST_SIZE; i++) history[i] = 0;
  baselineInit  = false;   // next sample becomes the new baseline
  baselinePct   = 0;
  currentStatus = "NORMAL";
  medCount = 0; medIdx = 0;            // also flush the filters so old readings don't linger
  avgCount = 0; avgIdx = 0; avgSum = 0;
  smoothedRaw   = 0;
  addEvent("INFO", "Data cleared by user");
}

/* ===========================================================================
 *  SECTION 11 - SETUP  (runs once at power-on)
 * ========================================================================= */
void setup() {
  Serial.begin(115200);
  delay(200);                  // brief pause so the Serial monitor catches the first lines
  Serial.println();
  Serial.println("Booting Truck Fuel Monitor...");

  analogReadResolution(12);    // ESP32 ADC -> 12-bit, so analogRead returns 0..4095

  // Bring up the ESP32's own Wi-Fi network (no router required).
  WiFi.softAP(ssid, password);
  IPAddress ip = WiFi.softAPIP();
  Serial.print("AP SSID    : "); Serial.println(ssid);
  Serial.print("AP Password: "); Serial.println(password);
  Serial.print("Dashboard  : http://"); Serial.println(ip);

  // Wire up the three HTTP endpoints (see header for the full list).
  server.on("/", []() {
    server.send_P(200, "text/html", index_html);     // the dashboard page
  });
  server.on("/data", []() {
    server.send(200, "application/json", buildJson()); // live readings
  });
  server.on("/clear", HTTP_POST, []() {
    clearAll();
    server.send(200, "text/plain", "Cleared");         // reset button
  });
  server.begin();
  Serial.println("HTTP server started.");
}

/* ===========================================================================
 *  SECTION 12 - LOOP  (runs forever)
 *  ---------------------------------------------------------------------------
 *  A simple cooperative scheduler: instead of using delay() (which would
 *  freeze the web server), we check millis() and run each job only when its
 *  interval has elapsed. This keeps the dashboard responsive at all times.
 * ========================================================================= */
void loop() {
  server.handleClient();       // service any pending web requests every pass

  unsigned long now = millis();

  if (now - lastSampleAt >= SAMPLE_INTERVAL) {    // read + process the sensor (20 Hz)
    lastSampleAt = now;
    sampleSensor();
  }
  if (now - lastHistoryAt >= HISTORY_INTERVAL) {  // log a trend point (every 2 s)
    lastHistoryAt = now;
    recordHistory();
  }
  if (now - lastSerialAt >= SERIAL_INTERVAL) {    // print heartbeat (every 1 s)
    lastSerialAt = now;
    printSerial();
  }
}
