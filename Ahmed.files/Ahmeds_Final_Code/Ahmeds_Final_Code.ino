/*
 ============================================================================
   _____ _           _       ____                     _
  |  ___| | ___  ___| |_    / ___|_   _  __ _ _ __ __| |
  | |_  | |/ _ \/ _ \ __|  | |  _| | | |/ _` | '__/ _` |
  |  _| | |  __/  __/ |_   | |_| | |_| | (_| | | | (_| |
  |_|   |_|\___|\___|\__|   \____|\__,_|\__,_|_|  \__,_|
 ============================================================================
   PROJECT   : SGU LOGISTICS — FleetGuard  (ESP32 + SIM7600G)
               + Local analog fuel-level probe
   AUTHOR    : Ahmed Yousef Saeed Khalifa
   COPYRIGHT : © Ahmed Yousef Saeed Khalifa — All rights reserved.
   INDUSTRY  : Logistics
 ----------------------------------------------------------------------------
 *
 *   Hey, this is my tracker code. The ESP32 sits inside the vehicle and it
 *   has 6 jobs to do:
 *
 *     1. Send everything to the cloud over 4G (SIM7600G modem + MQTT).
 *     2. Read its own GPS from the SIM7600G so I know where the truck is.
 *     3. Listen to the OTHER esp — the one plugged into the car's OBD2
 *        port. That esp reads speed, RPM, fuel level, coolant, etc. and
 *        broadcasts it to me over ESP-NOW. No WiFi router needed, the two
 *        esp's talk to each other directly.
 *     4. Read its OWN analog fuel-level probe wired to FUEL_PIN. This is a
 *        real hardware sensor on the tank, smoothed and calibrated on-device.
 *        It's the fuel reading you see in the TRACKER STATUS box and it rides
 *        every MQTT payload as the "fuel_sensor" block.
 *     5. Watch the 4 security sensors on the truck:
 *           - Device Case   (if someone opens the tracker box itself)
 *           - Fuel Cap      (anti fuel-theft)
 *           - Cargo Door 1
 *           - Cargo Door 2
 *        If any of these is opened → ALARM to the cloud, right away.
 *     6. If the internet is down for any reason, I save everything to
 *        internal flash. When the 4G comes back I upload it all
 *        automatically. Nothing is lost.
 *
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   FUEL-THEFT DETECTION (3 independent checks — all print to Serial):
 *
 *     1. Probe drop    : my own analog probe falls ≥10 % within 1 second.
 *                        Nothing legitimate burns fuel that fast → siphoning.
 *                        (Also published to the cloud as a "fuel_theft" event.)
 *     2. OBD2 drop     : the OBD2 esp's reported fuel_level falls ≥10 % within
 *                        10 minutes. Serial-only — the OBD2 esp's own
 *                        alarm_fuel_theft flag already carries this to the cloud.
 *     3. Cap, no refuel: the fuel cap is opened but the fuel level does NOT
 *                        rise within 5 minutes. A real refuel adds fuel; opening
 *                        the cap with no refuel is the classic siphon pattern.
 *                        (Also published to the cloud as a "fuel_theft" event.)
 *
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   POWER / SLEEP (this part is important):
 *
 *     The OBD2 esp sends me a heartbeat every 30 seconds. As long as I keep
 *     getting heartbeats, the engine is on so I stay fully awake.
 *
 *     If I do NOT hear from it for 5 minutes (engine off, car parked, OBD2 Disconnected), I
 *     send location to the cloud and then I go to deep sleep for
 *     1 hour. Then I wake up, check again, sleep again. This is how the
 *     battery lasts.
 *
 *     I also wake up immediately if anyone touches a sensor pin — door,
 *     fuel cap, case — even mid-sleep (on RTC-capable pins; see note below).
 *
 *     While sleeping, the modem RF is fully OFF (CFUN=4 — the green LED on
 *     the SIM7600 stops blinking) and the GPS data is preserved so wake-up
 *     is a warm start, not cold.
 *
 * ────────────────────────────────────────────────────────────────────────────
 *
 *   PIN LABELS  (same names show up in Serial AND on the dashboard):
 *
 *      S1   → Device Case   (tamper on the tracker housing itself)
 *      S2   → Fuel Cap      (fuel-theft / unauthorized refuel)
 *      MAG1 → Cargo Door 1  (magnetic reed switch)
 *      MAG2 → Cargo Door 2  (magnetic reed switch)
 *      FUEL → Analog fuel-level probe (ADC1)
 *
 *   FILES SAVED ON THE DEVICE:
 *
 *      /telemetry.log  →  events I couldn't send (network was down)
 *      /upload.log     →  same file, renamed while I'm uploading it
 *      NVS "voyager"   →  last GPS location, so on wake-up I can show
 *                         the truck's position even before a new fix
 *
 *   SERIAL COMMANDS (type in the Serial Monitor):
 *
 *      saved   →  dump everything still queued offline
 *      clear   →  wipe the offline log
 *
 * ════════════════════════════════════════════════════════════════════════════
 */

// ─── Modem driver config (must come BEFORE TinyGsmClient.h is included) ───
#define TINY_GSM_MODEM_SIM7600    // tell TinyGSM which modem we have
#define TINY_GSM_RX_BUFFER 1024   // bigger UART buffer = fewer dropped bytes

// ─── Libraries ────────────────────────────────────────────────────────────
#include <Arduino.h>
#include <WiFi.h>             // for ESP-NOW + the MAC address
#include <esp_now.h>          // peer-to-peer link to the OBD2 sender ESP
#include <esp_wifi.h>         // lock channel & disable WiFi modem-sleep
#include "esp_bt.h"           // btStop() — we don't use Bluetooth
#include <esp_sleep.h>        // sleep API
#include <driver/gpio.h>      // GPIO wake-up configuration
#include <TinyGsmClient.h>    // SIM7600 driver (cellular)
#include <PubSubClient.h>     // MQTT client (runs on top of TinyGsmClient)
#include <ArduinoJson.h>      // builds the JSON payloads we publish
#include <Preferences.h>      // NVS — stores last GPS fix across reboots
#include <LittleFS.h>         // tiny filesystem for the offline buffer
#include <math.h>             // fabs() for the fuel deadband
#include <time.h>
#include <sys/time.h>

// ─── Locks (so my tasks don't fight each other) ──────────────────────────
//   fsMutex    : I take this before reading/writing LittleFS, otherwise two
//                tasks could corrupt the offline log at the same time.
//   mqttMutex  : This guards the modem's UART — NOT just MQTT. Anyone who
//                talks to the modem (MQTT publish, GPS poll, CSQ, etc.)
//                must take this first. Without it the AT commands and the
//                TCP data scramble each other and nothing works.
SemaphoreHandle_t fsMutex   = NULL;
SemaphoreHandle_t mqttMutex = NULL;

// ─── My SIM card credentials ─────────────────────────────────────────────
//   Change apn to whatever your SIM provider needs. User/pass are usually
//   blank for most prepaid IoT SIMs.
const char* apn      = "internet";
const char* gprsUser = "";
const char* gprsPass = "";

// ─── MQTT broker I'm publishing to ───────────────────────────────────────
//   Using HiveMQ's public broker for testing. For production I'll move to
//   my own broker. The dashboard subscribes to mqtt_topic and gets every
//   event from this device.
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;
const char* mqtt_topic  = "monztrack/device01/gps";

// ─── My GPIO pin assignments (ESP32 / WROOM-32) ──────────────────────────
//   Deep-sleep GPIO wake only works on RTC-capable pins. On the classic
//   ESP32 those are 0, 2, 4, 12-15, 25-27, 32-39. S1/S2/MAG1 below are all
//   RTC-capable; MAG2 (GPIO 18) is NOT, so it works normally while we're
//   awake but cannot wake the chip from deep sleep.
#define ESP32_TX_PIN       12    // ESP TX  → SIM7600 RX  (modem UART)
                                 //   NOTE: GPIO 12 is a boot strapping pin
                                 //   (MTDI). Make sure the modem doesn't pull
                                 //   it HIGH at power-on or the ESP may not boot.
#define ESP32_RX_PIN       14    // ESP RX  ← SIM7600 TX
#define PWRKEY_PIN         27    // SIM7600 PWRKEY (toggle to power-cycle modem)
#define SLEEP_BTN_PIN      26    // push button → enter LIGHT sleep
#define DEEP_SLEEP_BTN_PIN 25    // push button → enter DEEP sleep (modem off)
#define PIN_S1             33    // Device-Case tamper switch
#define PIN_S2             32    // Fuel-Cap switch
#define PIN_MAG1           15    // Cargo Door 1 magnetic reed switch
#define PIN_MAG2           18    // Cargo Door 2 magnetic reed switch (no deep-sleep wake)

// Analog fuel-level probe. GPIO 34 is an input-only ADC1 pin (no internal
// pull-up). Use an ADC1 pin (GPIO 32-39) — ADC2 pins don't work while WiFi /
// ESP-NOW is active. If you rewire, pick another ADC1 pin and update this.
#define FUEL_PIN           34

// ─── Periodic timers (all in milliseconds unless noted) ──────────────────
const uint32_t TELEMETRY_MS      = 30000;   // how often we publish telemetry to the cloud
const uint32_t STATUS_PRINT_MS   = 30000;   // how often the LOCAL status box appears on Serial
const uint32_t GPS_POLL_MS       = 30000;   // how often we ask the modem for a fresh GPS fix
const uint32_t SYNC_INTERVAL_MS  = 5000;    // how often we try to upload the offline buffer
const uint32_t GPRS_RECONNECT_MS = 10000;   // min wait between GPRS reconnect attempts
const uint32_t MQTT_RECONNECT_MS = 5000;    // min wait between MQTT reconnect attempts

// ─── My sleep rules (heartbeat-driven) ───────────────────────────────────
//   While the OBD2 esp is sending heartbeats, I stay awake. ANY packet from
//   it counts as a heartbeat. If 5 min goes by with no packet, I publish a
//   final location to the cloud and then deep-sleep for 1 hour. After the
//   hour I wake up and check again. Change these two numbers if you want a
//   different timing.
const uint32_t HEARTBEAT_TIMEOUT_MS  = 5 * 60 * 1000UL;   // 5 min silence → sleep
const uint32_t POWER_SAVE_SLEEP_S    = 3600UL;            // sleep for 1 hour
volatile uint32_t lastHeartbeatMs    = 0;                 // 0 means "not heard yet"

// ─── Fuel-theft check #2: OBD2 fuel-stream rapid drop (Serial-only) ──────
//   Watches the fuel_level the OBD2 esp reports. If it falls by FUEL_DROP_PCT
//   or more within FUEL_WINDOW_MS, no normal driving burns fuel that fast →
//   someone is probably siphoning. The cloud side is already covered by the
//   OBD2 esp's own alarm_fuel_theft flag, so this one only prints.
const uint32_t FUEL_WINDOW_MS  = 10UL * 60UL * 1000UL;    // 10 min window
const float    FUEL_DROP_PCT   = 10.0f;                   // 10 % drop = theft
static float    fuelBaseline   = -1.0f;                   // -1 means "no reading yet"
static uint32_t fuelBaselineTs = 0;

// ─── Fuel-theft check #3: fuel cap opened, no refuel within 5 min ────────
//   When the cap opens I snapshot the fuel level and start a 5-min watch. A
//   real refuel makes the level rise by at least FUEL_REFUEL_RISE_PCT; if that
//   never happens, the cap was opened to take fuel, not add it → theft.
const uint32_t    FUEL_REFUEL_WINDOW_MS = 5UL * 60UL * 1000UL;   // 5 min watch
const float       FUEL_REFUEL_RISE_PCT  = 2.0f;                  // rise that counts as a refuel
volatile bool     fuelCapWatchActive    = false;                 // a watch is running
volatile uint32_t fuelCapWatchStartMs   = 0;                     // when the cap opened
volatile float    fuelCapBaselinePct    = 0.0f;                  // level at cap-open

// ─── Loop bookkeeping (timestamps of last events) ────────────────────────
uint32_t lastTelemetry    = 0, lastGprsAttempt = 0, lastMqttAttempt  = 0;
uint32_t lastSyncAttempt  = 0, lastStatusPrint  = 0, lastGpsPoll      = 0;
uint32_t lastModemCheckMs = 0, lastPersistMs    = 0, lastGprsCheck    = 0;

// ─── LTE signal strength ─────────────────────────────────────────────────
//   CSQ is the standard 0..31 number the modem gives me. 99 means "no signal
//   at all" — that's my fastest way to know if the antenna got unplugged or
//   I drove out of coverage. I refresh this every 1 second in maintainGPRS().
volatile uint8_t lastSignalCsq = 99;
const uint32_t   GPRS_CHECK_MS = 1000;

// ─── Core hardware/library objects ───────────────────────────────────────
HardwareSerial SerialAT(1);                  // UART1 talks to the SIM7600
TinyGsm        modem(SerialAT);              // SIM7600 driver
TinyGsmClient  gsmClient(modem);             // TCP socket over cellular
PubSubClient   client(gsmClient);            // MQTT over that TCP socket
Preferences    prefs;                        // NVS for last-known GPS fix

// ─── Shared state across tasks (use the muxes above to access safely) ────
static portMUX_TYPE gpsMux = portMUX_INITIALIZER_UNLOCKED;  // guards lastLat/lastLng/etc.
static portMUX_TYPE seqMux = portMUX_INITIALIZER_UNLOCKED;  // guards the seq counter

volatile bool modemOnline    = false;    // true once initModem() succeeded
volatile bool gprsLinkUp     = false;    // true while we're attached + have IP
uint32_t      systemStartMs  = 0;        // millis() right after setup() finishes
bool          hasOfflineData = false;    // true while /telemetry.log has content

// ─── GPS state (always read/written under gpsMux) ────────────────────────
//   gpsFixNow      : true if the LAST CGPSINFO poll returned a real fix
//   gpsFixEver     : true if we've gotten ANY fix this session
//   gpsHasStoredFix: true if we restored lat/lng from NVS at boot
//                    (lets us send something useful even with no live fix)
//   lastFixMs      : millis() of the last live fix — used to compute age
//   lastLat/Lng/Speed/Alt : the most recent (possibly cached) values
bool     gpsFixNow = false, gpsFixEver = false, gpsHasStoredFix = false;
uint32_t lastFixMs = 0;
float    lastLat = 0.0f, lastLng = 0.0f, lastSpeed = 0.0f, lastAlt = 0.0f;

// Set to true once we extract UTC time from the GPS NMEA — we only do this
// once per boot to avoid jumping the system clock around.
volatile bool sysTimeSet = false;

// ════════════════════════════════════════════════════════════════════════════
//   LOCAL FUEL-LEVEL PROBE  (median + moving-average + calibration)
// ════════════════════════════════════════════════════════════════════════════
//   A resistive/capacitive fuel-level probe is wired to FUEL_PIN. fuelSensorTask
//   samples it at 20 Hz and runs this pipeline:
//
//        analogRead(FUEL_PIN)
//             │
//             ▼  median(5)          ← rejects single-sample outliers (0-dropouts)
//             ▼  movingAverage(30)  ← smooths noise; ~1.5 s window at 20 Hz
//             ▼  adcToPercent()     ← piecewise-linear table (the probe is very
//             │                        non-linear, so one empty/full pair won't do)
//             ▼  deadband (≥1 %)    ← reported value only "ticks" when the real
//             │                        movement beats the deadband, killing the
//             ▼                        60→90→70 bounce a raw probe shows at rest
//        fuelLevelPct  (under fuelMux → read by the status box and JSON builders)
//
const int      FUEL_MEDIAN_N     = 5;
const int      FUEL_AVG_N        = 30;
const float    FUEL_DEADBAND_PCT = 1.0f;
const uint32_t FUEL_SAMPLE_MS    = 50;    // 20 Hz

// ── Fuel-theft check #1: local probe rapid drop ──
//   If the probe falls ≥ FUEL_THEFT_DROP_PCT within FUEL_THEFT_WINDOW_MS we
//   compare "fuel now" against "fuel ~1 s ago", each averaged over a few
//   samples so noise can't trip a false alarm.
const float    FUEL_THEFT_DROP_PCT    = 10.0f;  // % drop that counts as theft
const uint32_t FUEL_THEFT_WINDOW_MS   = 1000;   // ...measured over this window (1 s)
const uint32_t FUEL_THEFT_COOLDOWN_MS = 5000;   // don't re-alert for 5 s after one fires
const int      FUEL_THEFT_BUF         = FUEL_THEFT_WINDOW_MS / FUEL_SAMPLE_MS;  // = 20

// Piecewise calibration table — adjust to match YOUR probe and tank.
// Each row = "when the smoothed ADC equals `adc`, fuel is `pct` percent full".
// Values between rows are linearly interpolated.
struct FuelCalPoint { int adc; float pct; };
const FuelCalPoint FUEL_CAL_TABLE[] = {
    {    0,   0.0f },
    { 1500,  10.0f },
    { 2050,  25.0f },
    { 2150,  50.0f },
    { 2250,  75.0f },
    { 2350, 100.0f }   // tweak if your physical "full" reads higher/lower
};
const int   FUEL_CAL_N           = sizeof(FUEL_CAL_TABLE) / sizeof(FUEL_CAL_TABLE[0]);
const float FUEL_TANK_CAPACITY_L = 200.0f;   // tank size in liters (for the L display)

// Shared fuel state (written by fuelSensorTask, read by the status box + JSON).
static portMUX_TYPE fuelMux = portMUX_INITIALIZER_UNLOCKED;
volatile int   fuelRawAdc      = 0;
volatile float fuelSmoothedAdc = 0.0f;
volatile float fuelLevelPct    = 0.0f;
volatile bool  fuelValid       = false;     // false until the first sample
// Details of the last detected theft — read by buildFuelTheftJson() for the cloud.
volatile float fuelTheftDropPct = 0.0f;     // size of the drop (%)
volatile float fuelTheftFromPct = 0.0f;     // level before the drop (%)
volatile float fuelTheftToPct   = 0.0f;     // level after the drop (%)
const char*    fuelTheftReason  = "sensor_drop";   // "sensor_drop" | "cap_no_refuel"

// Filter ring buffers — only touched by fuelSensorTask, no mutex needed.
static int  fuelMedBuf[FUEL_MEDIAN_N];
static int  fuelMedIdx = 0, fuelMedCount = 0;
static int  fuelAvgBuf[FUEL_AVG_N];
static int  fuelAvgIdx = 0, fuelAvgCount = 0;
static long fuelAvgSum = 0;

// Returns the middle value of the last FUEL_MEDIAN_N samples — kills outliers.
static int fuelMedianFilter(int v) {
    fuelMedBuf[fuelMedIdx] = v;
    fuelMedIdx = (fuelMedIdx + 1) % FUEL_MEDIAN_N;
    if (fuelMedCount < FUEL_MEDIAN_N) fuelMedCount++;
    int tmp[FUEL_MEDIAN_N];
    for (int i = 0; i < fuelMedCount; i++) tmp[i] = fuelMedBuf[i];
    for (int i = 1; i < fuelMedCount; i++) {       // insertion sort
        int x = tmp[i], j = i - 1;
        while (j >= 0 && tmp[j] > x) { tmp[j + 1] = tmp[j]; j--; }
        tmp[j + 1] = x;
    }
    return tmp[fuelMedCount / 2];
}

// O(1) sliding-window mean.
static float fuelMovingAverage(int v) {
    if (fuelAvgCount < FUEL_AVG_N) {
        fuelAvgBuf[fuelAvgIdx] = v;
        fuelAvgSum += v;
        fuelAvgCount++;
    } else {
        fuelAvgSum -= fuelAvgBuf[fuelAvgIdx];
        fuelAvgBuf[fuelAvgIdx] = v;
        fuelAvgSum += v;
    }
    fuelAvgIdx = (fuelAvgIdx + 1) % FUEL_AVG_N;
    return (float)fuelAvgSum / fuelAvgCount;
}

// Piecewise-linear lookup: smoothed ADC → fuel %.
static float fuelAdcToPercent(float adc) {
    if (adc <= FUEL_CAL_TABLE[0].adc)              return FUEL_CAL_TABLE[0].pct;
    if (adc >= FUEL_CAL_TABLE[FUEL_CAL_N - 1].adc) return FUEL_CAL_TABLE[FUEL_CAL_N - 1].pct;
    for (int i = 0; i < FUEL_CAL_N - 1; i++) {
        int aLo = FUEL_CAL_TABLE[i].adc;
        int aHi = FUEL_CAL_TABLE[i + 1].adc;
        if (adc >= aLo && adc <= aHi) {
            float pLo = FUEL_CAL_TABLE[i].pct;
            float pHi = FUEL_CAL_TABLE[i + 1].pct;
            float r   = (adc - aLo) / (float)(aHi - aLo);
            return pLo + r * (pHi - pLo);
        }
    }
    return 0.0f;
}

// ════════════════════════════════════════════════════════════════════════════
//   ESP-NOW PACKET FORMAT  (MUST match exactly on the OTHER esp)
// ════════════════════════════════════════════════════════════════════════════
//   The OBD2 esp fills this struct with what it reads from the car —
//   speed, RPM, fuel, etc. — plus the alarm flags it computes itself
//   (alarm_speed = speed > 100, alarm_rpm = rpm > 3500, and so on).
//   Then it broadcasts the whole struct over ESP-NOW. Here I just memcpy
//   the bytes into a local copy.
//
//   IMPORTANT: the byte layout has to match EXACTLY on both sides. Same
//   field order, same types, same __attribute__((packed)) so the compiler
//   doesn't insert any padding. If anyone changes this struct, change it
//   on BOTH esp's at the same time and re-flash both — otherwise the
//   receiver sees the wrong size and rejects every packet. There's a
//   diagnostic print in OnDataRecv that tells you when this happens.
typedef struct __attribute__((packed)) obd_data {
    float speed;               // km/h
    int   rpm;
    int   throttle;            // 0..100 %
    int   coolant_temp;        // °C
    int   engine_load;         // 0..100 %
    float fuel_level;          // 0.0..100.0 %
    bool  fuel_theft;          // sender-side anomaly detector
    bool  alarm_speed;         // speed > threshold
    bool  alarm_rpm;           // rpm > threshold
    bool  alarm_coolant;       // coolant > threshold
    bool  alarm_engine_load;
    bool  alarm_throttle;
    bool  alarm_fuel_theft;
} obd_data;

// ─── Internal event used by FreeRTOS queues ──────────────────────────────
//   We re-use a single SensorEvent struct for everything the publish tasks
//   might send. The remote_pin field is a tag that tells the JSON builder
//   what KIND of event this is:
//
//       -1  Local sensor CHANGE (real edge, interrupt-driven) → "sensor_alert"
//                                                                if any pin = 0
//       -2  OBD2 normal telemetry frame                        → "obd2"
//       -3  OBD2 alarm frame                                   → "alarm"
//       -5  Periodic snapshot (every TELEMETRY_MS)             → "telemetry"
//                                                                (pins NEVER
//                                                                 raise alarms here)
//       -7  Local fuel-theft event (probe drop or cap-no-refuel) → "fuel_theft"
typedef struct {
    uint32_t ts_ms;            // millis() when the event was generated
    uint8_t  s1, s2, mag1, mag2;
    int      remote_pin;       // see tag table above
} SensorEvent;

QueueHandle_t sensorQueue = NULL;   // normal events, drained by dataProcessingTask
QueueHandle_t alarmQueue  = NULL;   // urgent alarms / fuel-theft, drained by alarmTask (priority 6)

TaskHandle_t sensorTaskHandle = nullptr;   // used by ISR to wake the sensor task

const uint32_t DEBOUNCE_MS = 25;            // sensor-change debounce window
// Last-known pin states — what we compare new readings against in sensorTask
// to detect an actual edge (vs. just a noisy interrupt). NOTE: mag1/mag2 are
// already inverted by readMag(), so 1 = magnet present, 0 = magnet absent.
uint8_t last_s1 = 0, last_s2 = 0, last_mag1 = 0, last_mag2 = 0;

// ─── Forward declarations (functions used before they're defined) ────────
void  checkFuelTheft(float currentFuel);
float currentFuelPct();
void  checkFuelCapRefuel();
void  buildAlarmJson(const SensorEvent& ev, char* out, size_t maxLen);
void  buildFuelTheftJson(const SensorEvent& ev, char* out, size_t maxLen);
void  generateEventJson(const SensorEvent& ev, char* out, size_t maxLen);

// ─── Latest OBD2 reading from the sender (protected by obdMux) ───────────
// Updated by OnDataRecv() on every incoming ESP-NOW packet. Read by the JSON
// builders. Wiped to defaults in enterPowerSaveSleep() so stale alarm flags
// can't leak into the post-wake state.
static portMUX_TYPE obdMux        = portMUX_INITIALIZER_UNLOCKED;
volatile bool  obdDataReceived    = false;     // false until first valid packet
volatile float rcv_speed          = 0.0f;
volatile int   rcv_rpm            = 0;
volatile int   rcv_throttle       = 0;
volatile int   rcv_coolant        = 65;
volatile int   rcv_engine_load    = 0;
volatile float rcv_fuel_level     = 0.0f;
volatile bool  rcv_fuel_theft     = false;
volatile bool  rcv_alarm_speed    = false;
volatile bool  rcv_alarm_rpm      = false;
volatile bool  rcv_alarm_coolant  = false;
volatile bool  rcv_alarm_eng_load = false;
volatile bool  rcv_alarm_throttle = false;
volatile bool  rcv_alarm_ftf      = false;

// ─── This runs every time the other esp sends me a packet ───────────────
// I have to keep this fast — it runs in callback context, like an interrupt.
// All I do is: check the size, copy into RAM under the mutex, print the
// telemetry box, queue an event, and exit. The actual publishing to the
// cloud happens later in alarmTask / dataProcessingTask.
void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    // First — does the packet size match my struct?
    // If not, the other esp has a different obd_data layout. Better to drop
    // it than blindly memcpy garbage over my alarm flags. If you EVER see
    // this print → the two esp's have different versions of the struct.
    // Re-flash both with the same version.
    if (len != sizeof(obd_data)) {
        Serial.printf("[ESP-NOW] Bad packet: got %d bytes, expected %u — ignored\n",
                      len, (unsigned)sizeof(obd_data));
        return;
    }

    // Any well-formed packet from the other esp = a heartbeat. This is what
    // the 5-minute sleep watchdog over in loop() looks at to decide whether
    // the engine is still on.
    lastHeartbeatMs = millis();

    obd_data msg;
    memcpy(&msg, data, sizeof(msg));

    portENTER_CRITICAL(&obdMux);
    obdDataReceived    = true;
    rcv_speed          = msg.speed;
    rcv_rpm            = msg.rpm;
    rcv_throttle       = msg.throttle;
    rcv_coolant        = msg.coolant_temp;
    rcv_engine_load    = msg.engine_load;
    rcv_fuel_level     = msg.fuel_level;
    rcv_fuel_theft     = msg.fuel_theft;
    rcv_alarm_speed    = msg.alarm_speed;
    rcv_alarm_rpm      = msg.alarm_rpm;
    rcv_alarm_coolant  = msg.alarm_coolant;
    rcv_alarm_eng_load = msg.alarm_engine_load;
    rcv_alarm_throttle = msg.alarm_throttle;
    rcv_alarm_ftf      = msg.alarm_fuel_theft;
    portEXIT_CRITICAL(&obdMux);

    // Fuel-theft check #2 — watch the OBD2 fuel stream for a rapid drop.
    // (Serial-only; the OBD2 esp's alarm_fuel_theft flag carries this to the cloud.)
    checkFuelTheft(msg.fuel_level);

    Serial.println("╔════════════════  VEHICLE TELEMETRY  ════════════════╗");
    Serial.printf( "║  Speed         : %.1f km/h%s\n",  msg.speed,        msg.alarm_speed       ? "   [!] OVERSPEED"   : "");
    Serial.printf( "║  Engine RPM    : %d%s\n",         msg.rpm,          msg.alarm_rpm         ? "   [!] HIGH RPM"    : "");
    Serial.printf( "║  Throttle      : %d %%%s\n",      msg.throttle,     msg.alarm_throttle    ? "   [!] HIGH"        : "");
    Serial.printf( "║  Coolant Temp  : %d C%s\n",       msg.coolant_temp, msg.alarm_coolant     ? "   [!] OVERHEAT"    : "");
    Serial.printf( "║  Engine Load   : %d %%%s\n",      msg.engine_load,  msg.alarm_engine_load ? "   [!] HIGH"        : "");
    Serial.printf( "║  Fuel Level    : %.1f %%\n",      msg.fuel_level);
    Serial.printf( "║  Fuel Theft    : %s\n",           msg.fuel_theft ? "DETECTED  [!]" : "No");
    Serial.println("╚═════════════════════════════════════════════════════╝");

    // If ANY alarm flag is set in this frame, route the event to the
    // high-priority alarmQueue so it gets published ahead of regular telemetry.
    bool anyAlarm = msg.alarm_speed    || msg.alarm_rpm      || msg.alarm_coolant ||
                    msg.alarm_engine_load || msg.alarm_throttle || msg.alarm_fuel_theft;

    SensorEvent ev = {millis(), last_s1, last_s2, last_mag1, last_mag2,
                      anyAlarm ? -3 : -2};   // -3 alarm frame, -2 normal frame

    if (anyAlarm && alarmQueue != NULL) {
        xQueueSendFromISR(alarmQueue, &ev, NULL);    // urgent path
    } else if (sensorQueue != NULL) {
        xQueueSendFromISR(sensorQueue, &ev, NULL);   // normal telemetry path
    }
}

// ─── Sequence counter ─────────────────────────────────────────────────────
// Every JSON we publish carries an incrementing seq so the cloud can detect
// gaps / duplicates / re-ordering. The counter is shared between tasks,
// hence the seqMux critical section.
uint32_t seq = 0;
uint32_t nextSeq() {
    uint32_t s;
    portENTER_CRITICAL(&seqMux);
    s = ++seq;
    portEXIT_CRITICAL(&seqMux);
    return s;
}

// File on LittleFS where unsent events are queued while the network is down.
// Renamed to /upload.log while we're flushing it (see syncOfflineData()).
const char* LOG_FILE = "/telemetry.log";

// ════════════════════════════════════════════════════════════════════════════
//   PIN-CHANGE INTERRUPT (ISR)
// ════════════════════════════════════════════════════════════════════════════
// Fires on ANY edge (rising or falling) of S1 / S2 / MAG1 / MAG2 while the
// device is awake. Just kicks the sensorTask awake; all debouncing and
// decision-making happens there (interrupt context is too restrictive).
// IRAM_ATTR places this function in instruction RAM so it can run even when
// the SPI flash is being read.
void IRAM_ATTR handleSensorInterrupt() {
    BaseType_t hpw = pdFALSE;
    if (sensorTaskHandle) vTaskNotifyGiveFromISR(sensorTaskHandle, &hpw);
    if (hpw) portYIELD_FROM_ISR();
}

// ════════════════════════════════════════════════════════════════════════════
//   readMag()  —  read a magnetic sensor with the NC inversion I want
// ════════════════════════════════════════════════════════════════════════════
//   My cargo-door reed switches are wired as normally-open. When the magnet
//   is present (door closed), the switch closes to GND and the pin reads
//   LOW. When the magnet leaves (door opened), the pin floats HIGH.
//
//   But on the dashboard I want it the other way around — "1 = OK,
//   0 = alarm". So I flip the value here in software:
//
//      door closed (magnet present)  →  pin LOW   →  I return 1  (OK)
//      door open   (magnet gone)     →  pin HIGH  →  I return 0  (ALARM)
//
//   I did NOT change the pull-up resistor or any hardware — just the
//   value. The switches S1 (Device Case) and S2 (Fuel Cap) use the raw
//   digitalRead() because they're already wired so pressed = LOW = alarm.
static inline uint8_t readMag(uint8_t pin) {
    return digitalRead(pin) == LOW ? 1 : 0;
}

// ════════════════════════════════════════════════════════════════════════════
//   DISPLAY HELPERS — turn raw values into human-friendly strings
// ════════════════════════════════════════════════════════════════════════════

// Maps the CSQ index (0-31, or 99 = unknown) into a one-word label that's
// safe to show on a dashboard / serial monitor.
static const char* signalQualityName(uint8_t csq) {
    if (csq == 99) return "No signal";
    if (csq >= 25) return "Excellent";
    if (csq >= 20) return "Very good";
    if (csq >= 15) return "Good";
    if (csq >= 10) return "Fair";
    if (csq >= 5)  return "Poor";
    return "Very poor";
}

// Convert CSQ index (0..31, 99=unknown) into dBm.  Returns 0 for unknown.
static int csqToDbm(uint8_t csq) {
    if (csq == 99) return 0;
    return -113 + 2 * (int)csq;
}

// Format seconds into a friendly string like "4 min 32 s" or "1 h 5 min".
// Drops the trailing "0 s" / "0 min" so it reads as "4 min" instead of
// "4 min 0 s" when the value is an exact minute / hour.
static void formatDuration(uint32_t s, char* out, size_t maxLen) {
    if (s < 60) {
        snprintf(out, maxLen, "%u s", (unsigned)s);
    } else if (s < 3600) {
        uint32_t m = s / 60, sec = s % 60;
        if (sec == 0) snprintf(out, maxLen, "%u min", (unsigned)m);
        else          snprintf(out, maxLen, "%u min %u s", (unsigned)m, (unsigned)sec);
    } else {
        uint32_t h = s / 3600, m = (s % 3600) / 60;
        if (m == 0) snprintf(out, maxLen, "%u h", (unsigned)h);
        else        snprintf(out, maxLen, "%u h %u min", (unsigned)h, (unsigned)m);
    }
}

// ─── Fuel-theft check #2 (OBD2 fuel stream — Serial-only) ────────────────
// Called from OnDataRecv every time a new OBD2 fuel reading arrives. Watches
// for a drop faster than normal driving could produce. If fuel goes UP
// (refuel), the baseline slides forward so we don't false-trigger later.
void checkFuelTheft(float currentFuel) {
    uint32_t now = millis();

    // First reading after boot → just establish the baseline.
    if (fuelBaseline < 0) {
        fuelBaseline = currentFuel;
        fuelBaselineTs = now;
        return;
    }

    uint32_t elapsed = now - fuelBaselineTs;
    float drop = fuelBaseline - currentFuel;

    // Refuel — fuel went up. Reset baseline so we don't false-trigger later.
    if (drop < -1.0f) {
        fuelBaseline = currentFuel;
        fuelBaselineTs = now;
        return;
    }

    // Rapid drop inside the window → ALARM
    if (drop >= FUEL_DROP_PCT && elapsed <= FUEL_WINDOW_MS) {
        char tBuf[32]; formatDuration(elapsed / 1000, tBuf, sizeof(tBuf));
        Serial.println();
        Serial.println("╔════════════  FUEL THEFT DETECTED  ════════════╗");
        Serial.println("║   Source : OBD2 fuel reading");
        Serial.printf( "║   Rapid drop: %.1f %% → %.1f %% in %s\n",
                       fuelBaseline, currentFuel, tBuf);
        Serial.println("║   Normal driving can't burn fuel this fast.");
        Serial.println("╚═══════════════════════════════════════════════╝");
        // Reset baseline so I don't spam the alert every packet.
        fuelBaseline   = currentFuel;
        fuelBaselineTs = now;
        return;
    }

    // Window expired — slide the baseline so we keep tracking the next 10 min.
    if (elapsed >= FUEL_WINDOW_MS) {
        fuelBaseline   = currentFuel;
        fuelBaselineTs = now;
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   FUEL TASK + CAP-REFUEL WATCH
// ════════════════════════════════════════════════════════════════════════════

// Best available fuel level right now: my own probe if it's calibrated,
// otherwise fall back to the OBD2 esp's reported fuel_level.
float currentFuelPct() {
    float pct; bool valid;
    portENTER_CRITICAL(&fuelMux);
    pct = fuelLevelPct; valid = fuelValid;
    portEXIT_CRITICAL(&fuelMux);
    if (valid) return pct;
    float obd;
    portENTER_CRITICAL(&obdMux);
    obd = rcv_fuel_level;
    portEXIT_CRITICAL(&obdMux);
    return obd;
}

// Fuel-theft check #3 — evaluated continuously from fuelSensorTask.
// A watch starts in sensorTask when the fuel cap opens. If the level rises by
// FUEL_REFUEL_RISE_PCT it was a real refuel → clear. If 5 min pass with no
// rise, the cap was opened to TAKE fuel → theft (Serial + cloud).
void checkFuelCapRefuel() {
    if (!fuelCapWatchActive) return;

    uint32_t now    = millis();
    float    nowPct = currentFuelPct();

    // Legit refuel — fuel rose enough → all good, stop watching.
    if (nowPct >= fuelCapBaselinePct + FUEL_REFUEL_RISE_PCT) {
        fuelCapWatchActive = false;
        Serial.println();
        Serial.printf("[FUEL] Refuel confirmed after cap-open (%.1f %% → %.1f %%).\n",
                      fuelCapBaselinePct, nowPct);
        return;
    }

    // 5 minutes with no refuel → theft.
    if (now - fuelCapWatchStartMs >= FUEL_REFUEL_WINDOW_MS) {
        fuelCapWatchActive = false;

        Serial.println();
        Serial.println("╔════════════  FUEL THEFT DETECTED  ════════════╗");
        Serial.println("║   Source : fuel cap opened, NO refuel in 5 min");
        Serial.printf( "║   Level : %.1f %% (was %.1f %% at cap-open)\n",
                       nowPct, fuelCapBaselinePct);
        Serial.println("║   Opening the cap without adding fuel = siphon.");
        Serial.println("╚═══════════════════════════════════════════════╝");

        // Cloud: route through the urgent path (tag -7).
        portENTER_CRITICAL(&fuelMux);
        fuelTheftReason  = "cap_no_refuel";
        fuelTheftFromPct = fuelCapBaselinePct;
        fuelTheftToPct   = nowPct;
        fuelTheftDropPct = fuelCapBaselinePct - nowPct;
        portEXIT_CRITICAL(&fuelMux);

        if (alarmQueue) {
            SensorEvent ev = {now, last_s1, last_s2, last_mag1, last_mag2, -7};
            xQueueSend(alarmQueue, &ev, 0);
        }
    }
}

// fuelSensorTask (priority 3) — samples FUEL_PIN at 20 Hz, smooths it, runs
// the local-probe theft check, and keeps the cap-refuel watch ticking. The
// reported value is published into the shared globals for the status box +
// JSON builders. It does NOT print every reading — the fuel level shows in
// the TRACKER STATUS box every 30 s; only theft fires a Serial alert here.
void fuelSensorTask(void* pv) {
    static float reportedPct  = 0.0f;
    static bool  reportedInit = false;

    static float    theftBuf[FUEL_THEFT_BUF];   // ~1 second of fuel-% history
    static int      theftCount  = 0;
    static int      theftIdx    = 0;
    static uint32_t lastTheftMs = 0;

    for (;;) {
        int   raw      = analogRead(FUEL_PIN);
        int   med      = fuelMedianFilter(raw);
        float smoothed = fuelMovingAverage(med);        // stable signal — for display
        float showPct  = fuelAdcToPercent(smoothed);    // smooth % shown to the user
        float fastPct  = fuelAdcToPercent(med);         // responsive % — for theft check

        // Deadband the displayed value so it doesn't jitter when sitting still.
        if (!reportedInit || fabs(showPct - reportedPct) >= FUEL_DEADBAND_PCT) {
            reportedPct  = showPct;
            reportedInit = true;
        }

        // Push the responsive reading into the 1-second history ring.
        theftBuf[theftIdx] = fastPct;
        theftIdx = (theftIdx + 1) % FUEL_THEFT_BUF;
        if (theftCount < FUEL_THEFT_BUF) theftCount++;

        // ── Fuel-theft check #1: fuel now vs fuel ~1 second ago ──
        bool  theftNow = false;
        float dropPct = 0, fromPct = 0, toPct = 0;
        if (theftCount >= FUEL_THEFT_BUF) {
            const int K = 5;                       // average 5 samples each end (noise-proof)
            float past = 0, recent = 0;
            for (int i = 0; i < K; i++) {
                past   += theftBuf[(theftIdx + i) % FUEL_THEFT_BUF];                       // oldest
                recent += theftBuf[(theftIdx - 1 - i + FUEL_THEFT_BUF) % FUEL_THEFT_BUF];  // newest
            }
            past /= K; recent /= K;
            dropPct = past - recent; fromPct = past; toPct = recent;

            if (dropPct >= FUEL_THEFT_DROP_PCT &&
                (millis() - lastTheftMs) >= FUEL_THEFT_COOLDOWN_MS) {
                theftNow    = true;
                lastTheftMs = millis();
            }
        }

        // Publish the latest reading (read by the status box + JSON builders).
        portENTER_CRITICAL(&fuelMux);
        fuelRawAdc      = raw;
        fuelSmoothedAdc = smoothed;
        fuelLevelPct    = reportedPct;
        fuelValid       = reportedInit;
        if (theftNow) {
            fuelTheftReason  = "sensor_drop";
            fuelTheftDropPct = dropPct;
            fuelTheftFromPct = fromPct;
            fuelTheftToPct   = toPct;
        }
        portEXIT_CRITICAL(&fuelMux);

        // ── Theft → loud Serial alert + urgent cloud message ──
        if (theftNow) {
            float stolenL = (dropPct / 100.0f) * FUEL_TANK_CAPACITY_L;
            Serial.println();
            Serial.println("╔════════════  FUEL THEFT DETECTED  ════════════╗");
            Serial.println("║   Source : on-board fuel probe");
            Serial.printf( "║   Dropped %.1f %% in 1 second\n", dropPct);
            Serial.printf( "║   Level : %.1f %% → %.1f %%   (~%.0f L removed)\n",
                           fromPct, toPct, stolenL);
            Serial.println("╚═══════════════════════════════════════════════╝");

            if (alarmQueue) {   // urgent cloud path (tag -7)
                SensorEvent ev = {millis(), last_s1, last_s2, last_mag1, last_mag2, -7};
                xQueueSend(alarmQueue, &ev, 0);
            }
        }

        // Keep the "cap opened, did a refuel follow?" watch ticking.
        checkFuelCapRefuel();

        vTaskDelay(pdMS_TO_TICKS(FUEL_SAMPLE_MS));
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   JSON BUILDERS — assemble the payloads we publish to MQTT
// ════════════════════════════════════════════════════════════════════════════

// Tack a "type":"…" field onto an already-built JSON object. Used to label
// the same payload differently depending on how it got out: "live", "alarm",
// or "buffered" (when replayed from the offline log on next wake).
static String injectType(const String& json, const char* t) {
    if (json.length() < 2 || !json.endsWith("}")) return json;
    return json.substring(0, json.length() - 1) + ",\"type\":\"" + t + "\"}";
}

// Snapshot the local fuel probe under its mutex. Used by every builder.
static void snapshotFuel(float& pct, int& adc, float& smoothed, bool& valid) {
    portENTER_CRITICAL(&fuelMux);
    pct      = fuelLevelPct;
    adc      = fuelRawAdc;
    smoothed = fuelSmoothedAdc;
    valid    = fuelValid;
    portEXIT_CRITICAL(&fuelMux);
}

// Adds the standard "fuel_sensor" block (the on-board probe) to any doc.
static void addFuelSensor(JsonObject fl, float pct, int adc, float smoothed, bool valid) {
    fl["pct"]        = pct;
    fl["liters"]     = (pct / 100.0f) * FUEL_TANK_CAPACITY_L;
    fl["adc"]        = adc;
    fl["adc_smooth"] = smoothed;
    fl["valid"]      = valid;
    fl["capacity_l"] = FUEL_TANK_CAPACITY_L;
}

// Compact alarm payload — used by alarmTask for the urgent OBD2 path. Always
// includes the OBD2 block + alarms because, by definition, we got here
// because one of the alarm flags is true.
void buildAlarmJson(const SensorEvent& ev, char* out, size_t maxLen) {
    bool  fixNow, fixEver; float lat, lng;
    portENTER_CRITICAL(&gpsMux);
    fixNow = gpsFixNow; fixEver = gpsFixEver; lat = lastLat; lng = lastLng;
    portEXIT_CRITICAL(&gpsMux);

    float obd_speed, obd_fuel; int obd_rpm, obd_thr, obd_cool, obd_load;
    bool  obd_ftf, a_spd, a_rpm, a_cool, a_load, a_thr, a_ftf;
    portENTER_CRITICAL(&obdMux);
    obd_speed = rcv_speed;  obd_rpm  = rcv_rpm;   obd_thr  = rcv_throttle;
    obd_cool  = rcv_coolant; obd_load = rcv_engine_load;
    obd_fuel  = rcv_fuel_level; obd_ftf = rcv_fuel_theft;
    a_spd = rcv_alarm_speed; a_rpm  = rcv_alarm_rpm;  a_cool = rcv_alarm_coolant;
    a_load= rcv_alarm_eng_load; a_thr = rcv_alarm_throttle; a_ftf = rcv_alarm_ftf;
    portEXIT_CRITICAL(&obdMux);

    float fl_pct, fl_smoothed; int fl_adc; bool fl_valid;
    snapshotFuel(fl_pct, fl_adc, fl_smoothed, fl_valid);

    StaticJsonDocument<512> doc;
    doc["seq"]       = nextSeq();
    doc["ts_ms"]     = ev.ts_ms;
    doc["device_id"] = "monztrack-01";
    doc["event"]     = "alarm";
    doc["loc"]       = fixNow ? 1 : 0;
    doc["lat"]       = lat;
    doc["lng"]       = lng;
    doc["s1"]        = ev.s1;
    doc["s2"]        = ev.s2;
    doc["mag1"]      = ev.mag1;
    doc["mag2"]      = ev.mag2;

    addFuelSensor(doc.createNestedObject("fuel_sensor"), fl_pct, fl_adc, fl_smoothed, fl_valid);

    JsonObject obd = doc.createNestedObject("obd");
    obd["speed"]       = obd_speed; obd["rpm"]          = obd_rpm;
    obd["throttle"]    = obd_thr;   obd["coolant_temp"] = obd_cool;
    obd["engine_load"] = obd_load;  obd["fuel_level"]   = obd_fuel;
    obd["fuel_theft"]  = obd_ftf;

    JsonObject al = doc.createNestedObject("alarms");
    al["speed"] = a_spd; al["rpm"]         = a_rpm;  al["coolant"]     = a_cool;
    al["engine_load"] = a_load; al["throttle"] = a_thr; al["fuel_theft"] = a_ftf;

    serializeJson(doc, out, maxLen);
}

// Urgent fuel-theft payload — built when the probe sees a rapid drop OR the
// fuel cap is opened with no refuel. Includes GPS so the cloud knows WHERE.
void buildFuelTheftJson(const SensorEvent& ev, char* out, size_t maxLen) {
    bool fixNow, fixEver; float lat, lng;
    portENTER_CRITICAL(&gpsMux);
    fixNow = gpsFixNow; fixEver = gpsFixEver; lat = lastLat; lng = lastLng;
    portEXIT_CRITICAL(&gpsMux);

    float dropPct, fromPct, toPct; int adc; const char* reason;
    portENTER_CRITICAL(&fuelMux);
    adc     = fuelRawAdc;
    dropPct = fuelTheftDropPct; fromPct = fuelTheftFromPct; toPct = fuelTheftToPct;
    reason  = fuelTheftReason;
    portEXIT_CRITICAL(&fuelMux);

    StaticJsonDocument<384> doc;
    doc["seq"]       = nextSeq();
    doc["ts_ms"]     = ev.ts_ms;
    doc["device_id"] = "monztrack-01";
    doc["event"]     = "fuel_theft";
    doc["reason"]    = reason;                                 // "sensor_drop" | "cap_no_refuel"
    doc["loc"]       = fixNow ? 1 : 0;
    doc["lat"]       = lat;
    doc["lng"]       = lng;

    JsonObject fl = doc.createNestedObject("fuel_sensor");
    fl["pct"]       = toPct;                                   // level after the drop
    fl["from_pct"]  = fromPct;                                 // level before the drop
    fl["drop_pct"]  = dropPct;                                 // size of the drop
    fl["liters"]    = (toPct   / 100.0f) * FUEL_TANK_CAPACITY_L;
    fl["stolen_l"]  = (dropPct / 100.0f) * FUEL_TANK_CAPACITY_L;
    fl["adc"]       = adc;

    serializeJson(doc, out, maxLen);
}

// Full telemetry / sensor_alert payload — the everyday format. Includes GPS
// fix + last-known fallback, pin states, the on-board fuel probe, and an OBD
// block when fresh data is available. Used by dataProcessingTask for sensor
// changes AND for periodic snapshots.
void generateEventJson(const SensorEvent& ev, char* out, size_t maxLen) {
    bool     fixNow, fixEver, hasStored; float lat, lng, spd, alt; uint32_t fixMs;
    portENTER_CRITICAL(&gpsMux);
    fixNow    = gpsFixNow;  fixEver   = gpsFixEver;  hasStored = gpsHasStoredFix;
    lat       = lastLat;    lng       = lastLng;
    spd       = lastSpeed;  alt       = lastAlt;      fixMs     = lastFixMs;
    portEXIT_CRITICAL(&gpsMux);

    bool  hasObd, obd_ftf, a_spd, a_rpm, a_cool, a_load, a_thr, a_ftf;
    float obd_speed, obd_fuel; int obd_rpm, obd_thr, obd_cool, obd_load;
    portENTER_CRITICAL(&obdMux);
    hasObd    = obdDataReceived; obd_speed = rcv_speed;  obd_rpm  = rcv_rpm;
    obd_thr   = rcv_throttle;   obd_cool  = rcv_coolant; obd_load = rcv_engine_load;
    obd_fuel  = rcv_fuel_level; obd_ftf   = rcv_fuel_theft;
    a_spd     = rcv_alarm_speed; a_rpm = rcv_alarm_rpm;
    a_cool    = rcv_alarm_coolant; a_load  = rcv_alarm_eng_load;
    a_thr     = rcv_alarm_throttle; a_ftf  = rcv_alarm_ftf;
    portEXIT_CRITICAL(&obdMux);

    float fl_pct, fl_smoothed; int fl_adc; bool fl_valid;
    snapshotFuel(fl_pct, fl_adc, fl_smoothed, fl_valid);

    // ── Freshness gate ──
    // If we haven't heard from the sender in 60 s, the OBD2/alarm flags in RAM
    // are stale. Excluding the block from telemetry prevents the dashboard from
    // re-lighting alarms that were last true minutes ago, especially during the
    // 5-min silence countdown before sleep.
    const uint32_t OBD_FRESH_MS = 60000;
    bool obdFresh = (lastHeartbeatMs > 0) &&
                    (millis() - lastHeartbeatMs < OBD_FRESH_MS);
    if (!obdFresh) hasObd = false;

    uint32_t gps_age = fixEver ? (uint32_t)(millis() - fixMs) : 0;

    // Classify event:
    //   OBD2 frames are always "obd2".
    //   Periodic snapshots (-5) are always plain "telemetry" so stuck pin
    //   states never trigger alarms on the dashboard.
    //   Only an actual edge from sensorTask (-1) is allowed to fire
    //   sensor_alert — and only if it lands in an alarm state.
    const char* etype;
    if      (ev.remote_pin == -2)                                            etype = "obd2";
    else if (ev.remote_pin == -5)                                            etype = "telemetry";
    else if (ev.remote_pin == -1 &&
             (ev.s1==0 || ev.s2==0 || ev.mag1==0 || ev.mag2==0))             etype = "sensor_alert";
    else                                                                      etype = "telemetry";

    StaticJsonDocument<768> doc;
    doc["seq"]            = nextSeq();
    doc["ts_ms"]          = ev.ts_ms;
    doc["device_id"]      = "monztrack-01";
    doc["event"]          = etype;
    doc["gps_fix"]        = fixNow;
    doc["gps_has_fix"]    = fixEver;
    doc["gps_has_stored"] = hasStored;
    doc["gps_age_ms"]     = gps_age;
    doc["loc"]            = fixNow ? 1 : 0;
    doc["lat"]            = lat;
    doc["lng"]            = lng;
    doc["gps_speed"]      = spd;
    doc["alt"]            = alt;
    doc["s1"]             = ev.s1;
    doc["s2"]             = ev.s2;
    doc["mag1"]           = ev.mag1;
    doc["mag2"]           = ev.mag2;
    doc["theft_detected"] = (ev.s1 == 0) || obd_ftf;

    addFuelSensor(doc.createNestedObject("fuel_sensor"), fl_pct, fl_adc, fl_smoothed, fl_valid);

    if (hasObd) {
        JsonObject obd = doc.createNestedObject("obd");
        obd["speed"]       = obd_speed; obd["rpm"]          = obd_rpm;
        obd["throttle"]    = obd_thr;   obd["coolant_temp"] = obd_cool;
        obd["engine_load"] = obd_load;  obd["fuel_level"]   = obd_fuel;
        obd["fuel_theft"]  = obd_ftf;

        JsonObject al = doc.createNestedObject("alarms");
        al["speed"] = a_spd; al["rpm"] = a_rpm;     al["coolant"]     = a_cool;
        al["engine_load"] = a_load; al["throttle"] = a_thr; al["fuel_theft"] = a_ftf;
    }
    serializeJson(doc, out, maxLen);
}

// ════════════════════════════════════════════════════════════════════════════
//   OFFLINE BUFFER — saves a payload to flash so it can be uploaded later
// ════════════════════════════════════════════════════════════════════════════
// Called by the publish tasks whenever the network is down. Each call appends
// one JSON line to /telemetry.log. syncOfflineData() picks it up later.
void saveToFile(const char* json) {
    if (xSemaphoreTake(fsMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
        File f = LittleFS.open(LOG_FILE, FILE_APPEND);
        if (f) { f.println(json); f.close(); hasOfflineData = true; }
        xSemaphoreGive(fsMutex);
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   FREERTOS TASKS  (the parts that run in parallel)
// ════════════════════════════════════════════════════════════════════════════
//   I have 5 tasks running. Higher priority preempts lower priority — that's
//   how I make alarms jump the queue ahead of regular telemetry:
//
//      priority 6   alarmTask           — OBD2 alarms + fuel theft (URGENT)
//      priority 4   sensorTask          — debounces my switch interrupts
//      priority 3   dataProcessingTask  — regular telemetry + sensor_alert events
//      priority 3   fuelSensorTask      — samples the analog fuel probe at 20 Hz
//      priority 2   serialCommandTask   — handles "saved" / "clear" commands
//
//   All publishing tasks share mqttMutex so they take turns on the modem.

// ─── alarmTask  (priority 6 — highest) ────────────────────────────────────
//   Fast path for urgent events. Tag -7 = local fuel theft; anything else =
//   OBD2 alarm frame. Tries 3 quick retries, then saves to flash if it can't
//   get through. Short mutex window so other publishes don't get blocked too
//   long behind an alarm.
void alarmTask(void* pv) {
    SensorEvent ev;
    char buffer[640];
    for (;;) {
        if (xQueueReceive(alarmQueue, &ev, portMAX_DELAY) != pdTRUE) continue;

        if (ev.remote_pin == -7) buildFuelTheftJson(ev, buffer, sizeof(buffer));
        else                     buildAlarmJson(ev, buffer, sizeof(buffer));

        bool sent = false;
        for (int retry = 0; retry < 3 && !sent; retry++) {
            if (modemOnline && gprsLinkUp) {
                if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(500)) == pdTRUE) {
                    if (client.connected()) {
                        String p = injectType(String(buffer), "alarm");
                        sent = client.publish(mqtt_topic, p.c_str());
                    }
                    xSemaphoreGive(mqttMutex);
                }
            }
            if (!sent && retry < 2) vTaskDelay(pdMS_TO_TICKS(150));
        }

        if (!sent) saveToFile(buffer);
    }
}

// ─── dataProcessingTask  (priority 3) ─────────────────────────────────────
//   This handles everything that's NOT an urgent alarm:
//     - sensor changes (door opened, fuel cap, case tamper)
//     - periodic telemetry every 30 s
//     - OBD2 normal telemetry frames
//
//   Important rule about offline storage:
//     I only save to flash when the network is GENUINELY down (modem off
//     or 4G dropped). Slow internet or a brief MQTT hiccup does NOT cause
//     offline saves — I keep retrying. This is what makes the device feel
//     reliable even on a weak cellular signal.
void dataProcessingTask(void* pv) {
    SensorEvent ev;
    char buffer[1024];
    for (;;) {
        if (xQueueReceive(sensorQueue, &ev, portMAX_DELAY) != pdTRUE) continue;

        generateEventJson(ev, buffer, sizeof(buffer));

        // Modem or 4G genuinely down → offline immediately, no point retrying
        if (!modemOnline || !gprsLinkUp) {
            saveToFile(buffer);
            continue;
        }

        // Network layer is up. Keep trying as long as it stays up.
        // Budget bounds memory pressure on the queue if something is truly stuck.
        const uint32_t PUBLISH_BUDGET_MS = 20000;
        uint32_t startMs = millis();
        bool sent = false;

        while (!sent) {
            // If modem/4G drop mid-retry → real "no internet" → save offline
            if (!modemOnline || !gprsLinkUp) break;

            // Hard ceiling so the queue can't back up forever on a wedged connection
            if (millis() - startMs >= PUBLISH_BUDGET_MS) break;

            if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
                client.loop();   // flush stale socket state from prior publishes
                if (client.connected()) {
                    sent = client.publish(mqtt_topic,
                                          injectType(String(buffer), "live").c_str());
                }
                xSemaphoreGive(mqttMutex);
            }

            // Yield between attempts so maintainMQTT() can reconnect if MQTT dropped
            // (its cooldown is 5s, so 500ms × ~10 retries spans multiple reconnects).
            if (!sent) vTaskDelay(pdMS_TO_TICKS(500));
        }

        // Save offline only when network actually dropped, or budget exhausted.
        // Slow publishes that eventually succeed never reach this branch.
        if (!sent) saveToFile(buffer);
    }
}

// Helper used by the "saved" serial command — dumps the offline buffer
// to the Serial monitor, grouped by event type. Not normally called.
void printFileContents(const char* filename, bool printAlarm) {
    File file = LittleFS.open(filename, FILE_READ);
    if (!file) return;
    while (file.available()) {
        String line = file.readStringUntil('\n'); line.trim();
        if (line.length() == 0) continue;
        bool isAlarm = (line.indexOf("\"event\":\"alarm\"") != -1);
        if  (printAlarm && isAlarm)  { Serial.print("[ALARM]  "); Serial.println(line); }
        if (!printAlarm && !isAlarm) { Serial.print("[DATA]   "); Serial.println(line); }
    }
    file.close();
}

// ─── serialCommandTask (priority 2) ──────────────────────────────────────
// Reads commands typed in the Serial Monitor:
//   "saved"  → dump all currently-buffered offline events
//   "clear"  → wipe the offline log entirely (useful after firmware changes
//              that altered event semantics)
void serialCommandTask(void* pv) {
    String buf = "";
    for (;;) {
        while (Serial.available()) {
            char c = Serial.read();
            if (c == '\n' || c == '\r') {
                if (buf.length() > 0) {
                    buf.trim(); buf.toLowerCase();
                    if (buf == "saved") {
                        if (xSemaphoreTake(fsMutex, pdMS_TO_TICKS(1000)) == pdTRUE) {
                            bool has = LittleFS.exists("/upload.log") || LittleFS.exists(LOG_FILE);
                            Serial.println("\n=== OFFLINE DATA LOG ===");
                            if (!has) {
                                Serial.println("No saved data.");
                            } else {
                                Serial.println("[Alarm Events]");
                                if (LittleFS.exists("/upload.log")) printFileContents("/upload.log", true);
                                if (LittleFS.exists(LOG_FILE))      printFileContents(LOG_FILE,      true);
                                Serial.println("[Telemetry / Sensor Events]");
                                if (LittleFS.exists("/upload.log")) printFileContents("/upload.log", false);
                                if (LittleFS.exists(LOG_FILE))      printFileContents(LOG_FILE,      false);
                            }
                            Serial.println("========================\n");
                            xSemaphoreGive(fsMutex);
                        }
                    } else if (buf == "clear") {
                        if (xSemaphoreTake(fsMutex, pdMS_TO_TICKS(1000)) == pdTRUE) {
                            LittleFS.remove(LOG_FILE); LittleFS.remove("/upload.log");
                            hasOfflineData = false;
                            Serial.println("[SYSTEM] Log cleared.");
                            xSemaphoreGive(fsMutex);
                        }
                    }
                    buf = "";
                }
            } else { buf += c; }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   MODEM & GPS — bring-up, signal check, GPRS, network maintenance
// ════════════════════════════════════════════════════════════════════════════

// Hardware power-on for the SIM7600. The PWRKEY pin needs a specific pulse
// pattern (a short low followed by ~1.5s low) to toggle the modem's power.
// Then we wait ~15s for it to finish booting before issuing AT commands.
void powerOnModem() {
    pinMode(PWRKEY_PIN, OUTPUT);
    digitalWrite(PWRKEY_PIN, HIGH); delay(100);
    digitalWrite(PWRKEY_PIN, LOW);  delay(1500);
    digitalWrite(PWRKEY_PIN, HIGH); delay(15000);
}

// Configure one pin as a deep-sleep wake source.
//   - Wake polarity is set to the OPPOSITE of the current pin level so the
//     chip doesn't wake the instant it goes to sleep (level-triggered wake).
//   - Uses the ESP32 Arduino Core v3.x (ESP-IDF v5) gpio_wakeup API. The
//     `pin > 5` guard mirrors the original build; pins above it are skipped
//     and logged rather than risking an "invalid wakeup IO" error.
static void setupSensorWake(uint8_t pin, const char* name) {
    if (pin > 5) {
        Serial.printf("[POWER] %s on GPIO %u — wake skipped for this pin\n", name, pin);
        return;
    }
    uint8_t now = digitalRead(pin);
    gpio_int_type_t mode = (now == LOW) ? GPIO_INTR_HIGH_LEVEL : GPIO_INTR_LOW_LEVEL;
    gpio_wakeup_enable((gpio_num_t)pin, mode);
    esp_sleep_enable_gpio_wakeup();
    Serial.printf("[POWER] %s on GPIO %u (now %s) → wake on %s\n",
                  name, pin, now ? "HIGH" : "LOW",
                  mode == GPIO_INTR_HIGH_LEVEL ? "HIGH" : "LOW");
}

void enableSensorWakeup() {
    setupSensorWake(PIN_S1,   "S1");
    setupSensorWake(PIN_S2,   "S2");
    setupSensorWake(PIN_MAG1, "MAG1");
    setupSensorWake(PIN_MAG2, "MAG2");
}

// ─── initModem() ─────────────────────────────────────────────────────────
// Brings the SIM7600 up from any state (cold-off, CSCLK=2 sleep, or already
// running). On wake-from-sleep this runs again — +CFUN=1 here re-enables
// the RF that we turned off in enterPowerSaveSleep().
bool initModem() {
    SerialAT.begin(115200, SERIAL_8N1, ESP32_TX_PIN, ESP32_RX_PIN);
    delay(1000);

    // Send AT up to 20 times. If the modem is in CSCLK=2 sleep, a few AT
    // commands wake it up. If it's already running, the first one ACKs.
    // If nothing answers, we power-cycle the modem via PWRKEY.
    bool awake = false;
    for (int i = 0; i < 20; i++) {
        SerialAT.println("AT"); delay(100);
        if (modem.testAT(500)) { awake = true; break; }
        delay(400);
    }
    if (!awake) powerOnModem();

    // Drain any boot chatter, but bound the loop so a noisy / floating RX pin
    // can't freeze us here forever.
    uint32_t flushT = millis();
    while (SerialAT.available() && (millis() - flushT < 1000)) {
        SerialAT.read();
        delay(1);
    }

    SerialAT.println("ATE0"); delay(150);
    if (!modem.init()) return false;
    // Disable serial-sleep so the modem stays fully awake while we're using it
    // (it may have been left in CSCLK=2 from the previous sleep cycle).
    modem.sendAT("+CSCLK=0");    modem.waitResponse(500L);
    // Restore full RF functionality — previous sleep may have left it in
    // CFUN=4 (airplane mode). Without this the radio stays off after wake.
    modem.sendAT("+CFUN=1");     modem.waitResponse(10000L);
    delay(500);   // give RF time to power up before network operations
    delay(50); modem.setNetworkMode(38); delay(50);
    // Auto-start GPS on next modem power-on so satellite reacquisition begins
    // before the ESP finishes booting after deep-sleep wake.
    modem.sendAT("+CGPSAUTO=1"); modem.waitResponse(1000L);
    modem.sendAT("+CGPS=1,1");   modem.waitResponse(2000L);
    return true;
}

// ════════════════════════════════════════════════════════════════════════════
//   NETWORK MAINTENANCE — keep GPRS + MQTT alive, react to signal loss
// ════════════════════════════════════════════════════════════════════════════

// Called every loop iteration (rate-limited internally to 1Hz). Checks the
// signal quality, network registration, GPRS attach state, and triggers
// reconnects when needed. Also flips gprsLinkUp to false the moment the
// signal drops to 99 (antenna pulled or out of coverage).
void maintainGPRS() {
    if (!modemOnline) { gprsLinkUp = false; return; }

    // Rate-limit AT polling: 1Hz is enough to flip gprsLinkUp within ~1s
    // of antenna loss, without flooding the modem.
    uint32_t now = millis();
    if (now - lastGprsCheck < GPRS_CHECK_MS) return;
    lastGprsCheck = now;

    // mqttMutex guards the entire SerialAT, not just MQTT — these AT calls
    // corrupt each other if a task publishes mid-command otherwise.
    if (!mqttMutex || xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) != pdTRUE) return;

    // ── Signal-quality canary ──
    // AT+CREG? / AT+CGATT? lag 30+ seconds when the antenna is pulled because
    // the modem keeps cached registration state. AT+CSQ reflects the actual
    // RF measurement and goes to 99 ("not detectable") within a few seconds.
    int csq = modem.getSignalQuality();
    lastSignalCsq = (csq < 0) ? 99 : (uint8_t)csq;

    if (lastSignalCsq == 99) {
        // No signal at all → no internet. Force-drop link state and tear down
        // the (now-stale) MQTT socket so publishes fail fast and route to file.
        gprsLinkUp = false;
        if (client.connected()) client.disconnect();
        xSemaphoreGive(mqttMutex);
        return;
    }

    if (!modem.isNetworkConnected()) {
        gprsLinkUp = false;
        if (millis() - lastGprsAttempt > GPRS_RECONNECT_MS) {
            lastGprsAttempt = millis(); modem.waitForNetwork(3000);
        }
    } else {
        gprsLinkUp = modem.isGprsConnected();
        if (!gprsLinkUp && millis() - lastGprsAttempt > GPRS_RECONNECT_MS) {
            lastGprsAttempt = millis(); modem.gprsConnect(apn, gprsUser, gprsPass);
        }
    }
    xSemaphoreGive(mqttMutex);
}

// Reconnects MQTT to the broker. Called every loop. Uses a random client-id
// suffix so multiple devices can use the same broker without collisions.
void maintainMQTT() {
    if (!gprsLinkUp) return;
    uint32_t now = millis();
    if (!client.connected() && now - lastMqttAttempt > MQTT_RECONNECT_MS) {
        lastMqttAttempt = now;
        if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
            client.setBufferSize(2048);
            client.connect(("Voyager-" + String(random(0xffff), HEX)).c_str());
            xSemaphoreGive(mqttMutex);
        }
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   OFFLINE SYNC — replay buffered events to the broker once we're back online
// ════════════════════════════════════════════════════════════════════════════
// 1. Rename /telemetry.log → /upload.log so new events can still be appended
//    to the original file while we drain the upload one.
// 2. Walk through /upload.log line by line, publishing each. Yield between
//    publishes so an urgent alarm can preempt.
// 3. Delete /upload.log only if EVERY line went through. Otherwise leave it
//    and we'll retry next pass.
// All uploaded events are tagged with "type":"buffered" so the dashboard can
// distinguish them from live events if it wants to.
void syncOfflineData() {
    if (!modemOnline || !gprsLinkUp || !client.connected()) return;

    if (hasOfflineData) {
        if (xSemaphoreTake(fsMutex, pdMS_TO_TICKS(1000)) == pdTRUE) {
            if (LittleFS.exists(LOG_FILE)) {
                LittleFS.rename(LOG_FILE, "/upload.log");
                hasOfflineData = false;
            }
            xSemaphoreGive(fsMutex);
        }
    }

    if (!LittleFS.exists("/upload.log")) return;

    File file = LittleFS.open("/upload.log", FILE_READ);
    if (!file) { LittleFS.remove("/upload.log"); return; }

    bool allSent = true;
    while (file.available()) {
        String payload = file.readStringUntil('\n'); payload.trim();
        if (payload.length() == 0) continue;

        bool ok = false;
        if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(200)) == pdTRUE) {
            if (client.connected()) {
                ok = client.publish(mqtt_topic, injectType(payload, "buffered").c_str());
            }
            xSemaphoreGive(mqttMutex);
        }

        vTaskDelay(pdMS_TO_TICKS(10));  // yield — alarmTask (priority 6) preempts here

        if (!ok) { allSent = false; break; }
    }
    file.close();
    if (allSent) LittleFS.remove("/upload.log");
}

// Emergency cleanup — if the offline buffer fills the flash to <50 KB free,
// we have to drop something or future writes will fail. Easier to drop the
// oldest data than to lose the device.
void checkStorage() {
    if (xSemaphoreTake(fsMutex, pdMS_TO_TICKS(500)) == pdTRUE) {
        if ((LittleFS.totalBytes() - LittleFS.usedBytes()) < 51200) {
            LittleFS.remove(LOG_FILE); LittleFS.remove("/upload.log");
            hasOfflineData = false;
        }
        xSemaphoreGive(fsMutex);
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   NVS (Non-Volatile Storage) & SYSTEM TIME
// ════════════════════════════════════════════════════════════════════════════
// NVS survives power-cycles and deep sleep. We use it to remember the last
// GPS fix so the device can report a useful location immediately on wake,
// even before a fresh fix is acquired.

// Load the saved fix back into RAM on boot. Only accepts plausible values
// (lat in [-90,90], lng in [-180,180], and not exactly 0,0).
void loadLastFix() {
    prefs.begin("voyager", true);
    float    pLat = prefs.getFloat("lat", 0.0f);
    float    pLng = prefs.getFloat("lng", 0.0f);
    float    pAlt = prefs.getFloat("alt", 0.0f);
    float    pSpd = prefs.getFloat("spd", 0.0f);
    uint32_t pFixT= prefs.getUInt ("fix_t", 0);
    prefs.end();
    if (pLat>=-90&&pLat<=90&&pLng>=-180&&pLng<=180&&!(pLat==0&&pLng==0)) {
        portENTER_CRITICAL(&gpsMux);
        lastLat = pLat; lastLng = pLng;
        lastAlt = pAlt; lastSpeed = pSpd;
        gpsHasStoredFix = true;
        portEXIT_CRITICAL(&gpsMux);
        Serial.printf("[GPS] Restored last fix: %.6f, %.6f (alt %.1fm, t=%u)\n",
                      pLat, pLng, pAlt, pFixT);
    }
}

// Save the current GPS fix to NVS RIGHT NOW (no 30 s throttle).
// Called from every sleep path so the next wake has the freshest possible
// position to report immediately, even before a fresh fix is acquired.
void persistGpsStateNow() {
    float pLat, pLng, pAlt, pSpd; bool everFixed;
    portENTER_CRITICAL(&gpsMux);
    pLat = lastLat; pLng = lastLng; pAlt = lastAlt; pSpd = lastSpeed;
    everFixed = gpsFixEver;
    portEXIT_CRITICAL(&gpsMux);
    if (!everFixed || (pLat == 0.0f && pLng == 0.0f)) return;

    uint32_t fixUnix = (uint32_t)time(nullptr);  // 0 if system time not yet set

    prefs.begin("voyager", false);
    prefs.putFloat("lat",   pLat);
    prefs.putFloat("lng",   pLng);
    prefs.putFloat("alt",   pAlt);
    prefs.putFloat("spd",   pSpd);
    prefs.putUInt ("fix_t", fixUnix);
    prefs.end();
}

// Throttled wrapper around persistGpsStateNow() — only saves to NVS every
// 30 s to limit flash wear. Called after every successful GPS poll.
void persistLastFixIfDue() {
    if (millis() - lastPersistMs < 30000) return;
    lastPersistMs = millis();
    persistGpsStateNow();
}

// Convert NMEA-style coordinate (e.g. "0613.4775,S") into a decimal degree.
// The NMEA format is DDMM.mmmm, so DD = int(val/100) and MM.mmmm goes into
// the fractional minutes converted to degrees. South/West flip sign.
float convertNmeaToDecimal(const char* coord, const char* dir) {
    if (!coord || !dir || !strlen(coord)) return 0.0f;
    float val = atof(coord); int deg = (int)(val / 100);
    float dec = deg + (val - deg * 100.0f) / 60.0f;
    if (dir[0]=='S' || dir[0]=='W') dec = -dec;
    return dec;
}

// Howard Hinnant's "days from civil" algorithm — converts a (year, month,
// day) tuple into days since the Unix epoch. Pure integer math, no time
// zones, no DST. Reference: https://howardhinnant.github.io/date_algorithms.html
static int64_t days_from_civil(int y, unsigned m, unsigned d) {
    y -= (m <= 2);
    int era = (y>=0?y:y-399)/400; unsigned yoe=(unsigned)(y-era*400);
    unsigned doy=(153*(m+(m>2?-3:9))+2)/5+d-1;
    unsigned doe=yoe*365+yoe/4-yoe/100+doy;
    return (int64_t)era*146097+(int64_t)doe-719468;
}

// Sets the ESP's internal clock from a UTC date/time. Called once when the
// first GPS fix arrives. After this, time(nullptr) returns proper unix epoch.
static void setSystemTimeUTC(int Y, int Mo, int D, int H, int Mi, int S) {
    int64_t epoch = days_from_civil(Y,(unsigned)Mo,(unsigned)D)*86400LL
                    +(int64_t)H*3600LL+(int64_t)Mi*60LL+S;
    struct timeval tv = {(time_t)epoch, 0}; settimeofday(&tv, nullptr);
}

// ════════════════════════════════════════════════════════════════════════════
//   GPS POLLING (SIM7600 built-in GNSS)
// ════════════════════════════════════════════════════════════════════════════
// We use AT+CGPSINFO which returns a one-liner with lat, lng, date, UTC time,
// altitude, speed, etc. CALLER MUST HOLD mqttMutex while calling this — the
// modem UART is shared with MQTT and we'd otherwise scramble each other.
static bool pollCGPSINFO(uint32_t tms = 800) {
    while (modem.stream.available()) modem.stream.read();
    modem.sendAT("+CGPSINFO"); delay(20);
    if (modem.waitResponse(tms, "+CGPSINFO:") != 1) { modem.waitResponse(50); return false; }
    char buf[128]; size_t len = modem.stream.readBytesUntil('\n', buf, sizeof(buf)-1);
    modem.waitResponse(50); buf[len] = '\0';
    if (len > 0 && buf[len-1] == '\r') buf[len-1] = '\0';
    char* f[10]; int cnt = 0; char* p = buf; if (*p==' ') p++;
    while (p && cnt < 10) { f[cnt++]=p; p=strchr(p,','); if (p){*p='\0'; p++;} }
    if (cnt < 8 || !strlen(f[0])) {
        portENTER_CRITICAL(&gpsMux); gpsFixNow = false; portEXIT_CRITICAL(&gpsMux);
        return true;
    }
    float lat=convertNmeaToDecimal(f[0],f[1]), lng=convertNmeaToDecimal(f[2],f[3]);
    float alt=atof(f[6]), spd=atof(f[7])*1.852f; bool fixed=(lat!=0||lng!=0);
    portENTER_CRITICAL(&gpsMux);
    gpsFixNow = fixed;
    if (fixed) {
        gpsFixEver=true; lastFixMs=millis();
        lastLat=lat; lastLng=lng; lastAlt=alt; lastSpeed=spd;
    }
    portEXIT_CRITICAL(&gpsMux);
    if (!sysTimeSet && f[4] && f[5] && strlen(f[4])==6 && strlen(f[5])>=6) {
        setSystemTimeUTC(
            2000+(f[4][4]-'0')*10+(f[4][5]-'0'),
            (f[4][2]-'0')*10+(f[4][3]-'0'),
            (f[4][0]-'0')*10+(f[4][1]-'0'),
            (f[5][0]-'0')*10+(f[5][1]-'0'),
            (f[5][2]-'0')*10+(f[5][3]-'0'),
            (f[5][4]-'0')*10+(f[5][5]-'0'));
        sysTimeSet = true;
    }
    return true;
}

// Re-enable GPS in case the modem mysteriously turned it off (some firmware
// versions disable GPS after an AT error). Called as a fallback when
// pollCGPSINFO() returns false.
void ensureGnssOn() {
    modem.sendAT("+CGPS?"); delay(20);
    if (modem.waitResponse(1500L, "+CGPS:") == 1) {
        int mode = modem.stream.parseInt(); modem.stream.parseInt();
        if (mode != 1) { modem.sendAT("+CGPS=1,1"); modem.waitResponse(2000L); }
    }
}

// Top-level modem maintenance, called every loop iteration. Brings the modem
// up if it's not online, periodically verifies it's still alive (heartbeat
// AT every 60 s), and polls GPS at GPS_POLL_MS cadence.
void maintainModemAndGPS() {
    if (!modemOnline) {
        if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(5000)) == pdTRUE) {
            modemOnline = initModem();
            xSemaphoreGive(mqttMutex);
        }
        if (modemOnline) lastGpsPoll = millis() - GPS_POLL_MS;
        return;
    }
    uint32_t now = millis();
    if (now - lastModemCheckMs >= 60000) {
        lastModemCheckMs = now;
        if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
            bool alive = modem.testAT(1000);
            if (!alive) { gprsLinkUp = false; powerOnModem(); modemOnline = initModem(); }
            xSemaphoreGive(mqttMutex);
            if (!alive) return;
        }
    }
    if (now - lastGpsPoll >= GPS_POLL_MS) {
        // Hold the UART for the GPS round-trip — otherwise a concurrent
        // client.publish() from a task scrambles the +CGPSINFO response.
        if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
            lastGpsPoll = now;
            bool got = pollCGPSINFO(800);
            if (!got) ensureGnssOn();
            xSemaphoreGive(mqttMutex);
            if (got) persistLastFixIfDue();
        }
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   SENSOR TASK (priority 4) — debounces and dispatches switch events
// ════════════════════════════════════════════════════════════════════════════
// Sleeps on a task-notification. The pin-change ISR wakes it. After waking
// it debounces (DEBOUNCE_MS), reads the new pin values, and if they actually
// differ from the previous state it queues a SensorEvent and prints the
// friendly "SENSOR ALERT" box on Serial.
//
// remote_pin = -1 means "real edge from the ISR", which is what triggers
// the dashboard alarm if the new state is OPEN (pin == 0).
void sensorTask(void* pv) {
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);     // wait for any pin change
        vTaskDelay(pdMS_TO_TICKS(DEBOUNCE_MS));      // debounce window
        while (ulTaskNotifyTake(pdTRUE, 0) > 0) vTaskDelay(pdMS_TO_TICKS(5));  // coalesce

        uint8_t s1   = digitalRead(PIN_S1),   s2   = digitalRead(PIN_S2);
        uint8_t mag1 = readMag(PIN_MAG1),     mag2 = readMag(PIN_MAG2);  // NC-inverted
        if (s1==last_s1 && s2==last_s2 && mag1==last_mag1 && mag2==last_mag2) continue;

        // Catch the fuel-cap transition BEFORE I overwrite last_s2:
        // 1 → 0 means it just opened.
        bool fuelCapJustOpened = (last_s2 == 1) && (s2 == 0);

        last_s1=s1; last_s2=s2; last_mag1=mag1; last_mag2=mag2;

        SensorEvent ev = {millis(), s1, s2, mag1, mag2, -1};
        xQueueSend(sensorQueue, &ev, 0);

        Serial.println("┌──────────────────  SENSOR ALERT  ──────────────────┐");
        Serial.println("│  A sensor just changed state:");
        Serial.printf( "│    Device Case  : %s\n", s1   ? "Sealed"  : "TAMPERED  [!]");
        Serial.printf( "│    Fuel Cap     : %s\n", s2   ? "Closed"  : "OPEN  [!]");
        Serial.printf( "│    Cargo Door 1 : %s\n", mag1 ? "Closed"  : "OPEN  [!]");
        Serial.printf( "│    Cargo Door 2 : %s\n", mag2 ? "Closed"  : "OPEN  [!]");
        Serial.println("└────────────────────────────────────────────────────┘");

        // ── Fuel-theft check #3: arm the "did a refuel follow?" watch ──
        // Opening the cap is normal IF fuel goes up soon after. Snapshot the
        // level now; fuelSensorTask (checkFuelCapRefuel) decides in 5 min.
        if (fuelCapJustOpened) {
            fuelCapBaselinePct  = currentFuelPct();
            fuelCapWatchStartMs = millis();
            fuelCapWatchActive  = true;
            Serial.printf("   Fuel cap opened at %.1f %% — watching 5 min for a refuel.\n",
                          fuelCapBaselinePct);
        }
    }
}

// ════════════════════════════════════════════════════════════════════════════
//   SLEEP HANDLERS — three different ways the device can go to sleep
// ════════════════════════════════════════════════════════════════════════════

// Tear down the live network stack: close MQTT cleanly, drop GPRS. Called
// before all three sleep paths.
void shutdownNetworkMqtt() {
    if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(1000)) == pdTRUE) {
        client.disconnect(); xSemaphoreGive(mqttMutex);
    } else { client.disconnect(); }
    modem.gprsDisconnect(); gprsLinkUp = false;
}

// ── Manual SLEEP button (SLEEP_BTN_PIN) ──
// Modem goes into UART sleep mode (CSCLK=2) but keeps its GPS context alive,
// so wake-up gives a hot GPS start within 5-15 s. Slightly higher power than
// the deep-sleep button but much faster recovery.
void enterSleepMode() {
    persistGpsStateNow();   // save freshest fix for warm restart on wake
    modem.sendAT("+CSCLK=2"); modem.waitResponse();
    shutdownNetworkMqtt(); enableSensorWakeup(); esp_deep_sleep_start();
}

// ── Manual DEEP-SLEEP button (DEEP_SLEEP_BTN_PIN) ──
// Full modem power-off (lowest power). On wake the modem cold-boots — slower
// (15+ s) but draws almost nothing while sleeping.
void enterDeepSleepMode() {
    persistGpsStateNow();   // ESP-side cache restored even if modem cold-starts
    shutdownNetworkMqtt(); modem.poweroff();
    enableSensorWakeup(); esp_deep_sleep_start();
}

// ════════════════════════════════════════════════════════════════════════════
//   POWER-SAVE SLEEP  (this is the big one — the heartbeat-driven 1-hour cycle)
// ════════════════════════════════════════════════════════════════════════════
//   This gets called automatically by the watchdog in loop() when the OBD2
//   esp has been silent for HEARTBEAT_TIMEOUT_MS (5 min by default — engine
//   is presumably off).
//
//   The order of these steps is important — I learned that the hard way:
//
//     1. Detach pin interrupts so a noisy GPIO during shutdown doesn't fire
//        a fake sensor event.
//     2. Empty both queues so nothing in-flight gets published after we
//        already cleared the alarm state.
//     3. Wipe all rcv_alarm_* flags + the fuel-cap watch in RAM. Belt + braces.
//     4. Save GPS fix to NVS so the next wake-up can immediately show the
//        truck's last known position.
//     5. Build a full "going to sleep, everything is fine" telemetry frame.
//        I include ALL the pin fields here — if I don't, the dashboard
//        default-fills missing pins as "all triggered" → false alarms. Then
//        I publish it (5 retries).
//     6. Tell the chip what wakes it up: any sensor pin OR the 1-hour timer.
//     7. Disconnect MQTT, switch modem to airplane mode (CFUN=4 — radio
//        completely off, green LED stops blinking) and UART sleep (CSCLK=2).
//     8. esp_deep_sleep_start() — done. Wakes on pin or timer.
//        On wake, setup() runs from scratch. RAM is gone but NVS survives.
void enterPowerSaveSleep() {
    Serial.println("┌─────────── ENTERING POWER-SAVE ───────────┐");
    Serial.println("│ No heartbeat for 5 min — sleeping 1 hour");

    // ── 1. Stop new sensor events from being queued during shutdown ──
    detachInterrupt(digitalPinToInterrupt(PIN_S1));
    detachInterrupt(digitalPinToInterrupt(PIN_S2));
    detachInterrupt(digitalPinToInterrupt(PIN_MAG1));
    detachInterrupt(digitalPinToInterrupt(PIN_MAG2));

    // ── 2. Drop any queued events the tasks haven't published yet ──
    if (sensorQueue) xQueueReset(sensorQueue);
    if (alarmQueue)  xQueueReset(alarmQueue);

    // ── 3. Wipe OBD2 alarm state + fuel-cap watch so nothing carries over ──
    portENTER_CRITICAL(&obdMux);
    obdDataReceived    = false;
    rcv_fuel_level     = 0.0f;
    rcv_alarm_speed    = false;
    rcv_alarm_rpm      = false;
    rcv_alarm_coolant  = false;
    rcv_alarm_eng_load = false;
    rcv_alarm_throttle = false;
    rcv_alarm_ftf      = false;
    portEXIT_CRITICAL(&obdMux);
    fuelCapWatchActive = false;

    persistGpsStateNow();

    // ── 4. Build a "going dark, alarms cleared" snapshot ──
    bool fixNow, fixEver, hasStored; float lat, lng, spd, alt; uint32_t fixMs;
    portENTER_CRITICAL(&gpsMux);
    fixNow = gpsFixNow; fixEver = gpsFixEver; hasStored = gpsHasStoredFix;
    lat = lastLat; lng = lastLng; spd = lastSpeed; alt = lastAlt; fixMs = lastFixMs;
    portEXIT_CRITICAL(&gpsMux);

    // Read pin states NOW so the message is structurally identical to a normal
    // telemetry frame. Without s1/s2/mag1/mag2 the dashboard default-fills them
    // as "all triggered" — that's the false-alarm-on-sleep bug.
    uint8_t s1 = (uint8_t)digitalRead(PIN_S1);
    uint8_t s2 = (uint8_t)digitalRead(PIN_S2);
    uint8_t m1 = readMag(PIN_MAG1);
    uint8_t m2 = readMag(PIN_MAG2);

    float fl_pct, fl_smoothed; int fl_adc; bool fl_valid;
    snapshotFuel(fl_pct, fl_adc, fl_smoothed, fl_valid);

    StaticJsonDocument<768> doc;
    doc["seq"]            = nextSeq();
    doc["ts_ms"]          = millis();
    doc["device_id"]      = "monztrack-01";
    doc["event"]          = "power_save_enter";
    doc["uptime_s"]       = (millis() - systemStartMs) / 1000;
    doc["csq"]            = lastSignalCsq;
    doc["gps_fix"]        = fixNow;
    doc["gps_has_fix"]    = fixEver;
    doc["gps_has_stored"] = hasStored;
    doc["gps_age_ms"]     = fixEver ? (uint32_t)(millis() - fixMs) : 0;
    doc["loc"]            = fixNow ? 1 : 0;
    doc["lat"]            = lat;
    doc["lng"]            = lng;
    doc["gps_speed"]      = spd;
    doc["alt"]            = alt;
    doc["s1"]             = s1;
    doc["s2"]             = s2;
    doc["mag1"]           = m1;
    doc["mag2"]           = m2;
    doc["theft_detected"] = false;          // going to sleep cleanly
    doc["alarms_cleared"] = true;

    addFuelSensor(doc.createNestedObject("fuel_sensor"), fl_pct, fl_adc, fl_smoothed, fl_valid);

    JsonObject al = doc.createNestedObject("alarms");
    al["speed"] = false; al["rpm"]         = false; al["coolant"]     = false;
    al["engine_load"] = false; al["throttle"] = false; al["fuel_theft"] = false;

    char payload[768];
    serializeJson(doc, payload, sizeof(payload));

    bool sent = false;
    for (int retry = 0; retry < 5 && !sent; retry++) {
        if (modemOnline && gprsLinkUp && mqttMutex &&
            xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(2000)) == pdTRUE) {
            if (client.connected()) sent = client.publish(mqtt_topic, payload);
            xSemaphoreGive(mqttMutex);
        }
        if (!sent) delay(500);
    }
    if (!sent) saveToFile(payload);
    Serial.printf( "│ Location: %s\n", sent ? "published" : "saved offline");
    Serial.println("└────────────────────────────────────────────┘");

    enableSensorWakeup();
    esp_sleep_enable_timer_wakeup((uint64_t)POWER_SAVE_SLEEP_S * 1000000ULL);

    shutdownNetworkMqtt();

    // ── Real radio shutdown (not just UART sleep) ──
    // CFUN=4 disables cellular TX/RX → green LED stops blinking, big power drop.
    // GPS chain stays on its own RF, keeping ephemeris → warm-start on wake.
    // CSCLK=2 then puts the UART/MCU side into low-power too.
    Serial.println("[POWER] Modem RF off (CFUN=4) + UART sleep (CSCLK=2)");
    modem.sendAT("+CFUN=4"); modem.waitResponse(5000L);
    delay(300);   // let the RF stage actually power down
    modem.sendAT("+CSCLK=2"); modem.waitResponse();

    Serial.flush();
    esp_deep_sleep_start();
}

// ════════════════════════════════════════════════════════════════════════════
//   SETUP — runs once after every boot (including wake from deep sleep)
// ════════════════════════════════════════════════════════════════════════════
// Order matters here:
//   1. Mutexes & queues (FreeRTOS primitives) before anything that uses them.
//   2. ESP-NOW BEFORE the modem so we can start receiving heartbeats as soon
//      as the sender comes online.
//   3. ADC + LittleFS, then tasks — tasks may want the filesystem / ADC.
//   4. Wake-cause logging so the Serial output tells you why we just booted.
//   5. Pin setup + interrupts AFTER an 8-s settling delay (gives the modem
//      power rail time to stabilise so we don't get phantom interrupts).
//   6. Modem init last because it's the slowest step (~15 s on cold boot).
void setup() {
    Serial.begin(115200);

    fsMutex   = xSemaphoreCreateMutex();
    mqttMutex = xSemaphoreCreateMutex();

    btStop();
    setCpuFrequencyMhz(80);

    sensorQueue = xQueueCreate(15, sizeof(SensorEvent));
    alarmQueue  = xQueueCreate(5,  sizeof(SensorEvent));

    WiFi.mode(WIFI_STA);
    // ── Disable WiFi modem-sleep ──
    // Without this the radio sleeps between beacons (DTIM-driven) and ESP-NOW
    // packets arriving during sleep get silently dropped. Costs a few mA but
    // is the difference between heartbeats arriving and the receiver thinking
    // the sender is dead.
    WiFi.setSleep(false);

    if (esp_now_init() == ESP_OK) {
        esp_now_register_recv_cb(OnDataRecv);

        // ── Lock to channel 1 — must match the sender's peerInfo.channel ──
        // In STA mode without an AP, the channel is otherwise undefined and
        // the two devices can end up listening on different channels.
        esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
        uint8_t pri; wifi_second_chan_t sec;
        esp_wifi_get_channel(&pri, &sec);

        Serial.printf("[ESP-NOW] Receiver ready on channel %u. Expected packet size: %u bytes\n",
                      pri, (unsigned)sizeof(obd_data));
        Serial.printf("[ESP-NOW] My MAC (add this as peer on the sender): %s\n",
                      WiFi.macAddress().c_str());
    } else {
        Serial.println("[ESP-NOW] Init failed");
    }

    // ── ADC setup for the local fuel probe ──
    // 12-bit (0..4095) matches the calibration table; 11 dB attenuation gives
    // the full ~0..3.3 V input range the probe needs.
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    if (LittleFS.begin(true)) {
        if (xSemaphoreTake(fsMutex, portMAX_DELAY) == pdTRUE) {
            File f = LittleFS.open(LOG_FILE, FILE_READ);
            if (f && f.size() > 0) hasOfflineData = true;
            if (f) f.close();
            xSemaphoreGive(fsMutex);
        }
    }

    // ── Start the FreeRTOS tasks. Priorities are intentional — higher number
    //    preempts lower. AlarmTask must beat everything else so alarms are
    //    delivered before regular telemetry.
    xTaskCreate(alarmTask,          "AlarmTask",     4096, nullptr, 6, nullptr);
    xTaskCreate(sensorTask,         "SensorTask",    4096, nullptr, 4, &sensorTaskHandle);
    xTaskCreate(dataProcessingTask, "DataProcTask",  8192, nullptr, 3, nullptr);
    xTaskCreate(fuelSensorTask,     "FuelTask",      4096, nullptr, 3, nullptr);
    xTaskCreate(serialCommandTask,  "SerialCmdTask", 4096, nullptr, 2, nullptr);

    // 8-second settling delay before we attach interrupts and start the modem.
    // Lets the SIM7600's power rail stabilise so we don't get spurious pin
    // interrupts on boot.
    delay(8000);
    systemStartMs = millis();
    // lastHeartbeatMs stays 0 until a heartbeat actually arrives — used as a
    // cleaner "did the sender ever speak this session?" signal.

    // Log the wake reason; behavior is identical for every wake — run the
    // normal loop. The 5-min heartbeat watchdog inside loop() decides when
    // to sleep again.
    switch (esp_sleep_get_wakeup_cause()) {
        case ESP_SLEEP_WAKEUP_TIMER:
            Serial.println("[POWER] Wake from 1h timer — 5 min to detect sender");
            break;
        case ESP_SLEEP_WAKEUP_GPIO:
        case ESP_SLEEP_WAKEUP_EXT0:
        case ESP_SLEEP_WAKEUP_EXT1:
            Serial.println("[POWER] Wake from sensor pin");
            break;
        default:
            Serial.println("[POWER] Cold boot");
            break;
    }

    pinMode(SLEEP_BTN_PIN,      INPUT_PULLUP);
    pinMode(DEEP_SLEEP_BTN_PIN, INPUT_PULLUP);
    pinMode(PIN_S1,             INPUT_PULLUP);
    pinMode(PIN_S2,             INPUT_PULLUP);
    pinMode(PIN_MAG1,           INPUT_PULLUP);
    pinMode(PIN_MAG2,           INPUT_PULLUP);

    last_s1   = digitalRead(PIN_S1);
    last_s2   = digitalRead(PIN_S2);
    last_mag1 = readMag(PIN_MAG1);   // NC-inverted: 1 = magnet present, 0 = absent
    last_mag2 = readMag(PIN_MAG2);

    attachInterrupt(digitalPinToInterrupt(PIN_S1),   handleSensorInterrupt, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_S2),   handleSensorInterrupt, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_MAG1), handleSensorInterrupt, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_MAG2), handleSensorInterrupt, CHANGE);

    loadLastFix();
    modemOnline      = initModem();
    lastGprsAttempt  = millis();
    lastMqttAttempt  = millis();
    lastModemCheckMs = millis();

    client.setServer(mqtt_server, mqtt_port);
    client.setBufferSize(2048);

    Serial.println("[SYSTEM] Ready. Commands: saved | clear");
}

// ════════════════════════════════════════════════════════════════════════════
//   MAIN LOOP — runs forever after setup()
// ════════════════════════════════════════════════════════════════════════════
// Most of the heavy work happens in the FreeRTOS tasks; loop() just glues
// the periodic tasks together: button checks, heartbeat watchdog, modem +
// network maintenance, MQTT loop, offline sync, and periodic telemetry.
void loop() {
    uint32_t now = millis();

    // ── Manual sleep buttons (debounced) ──
    if (digitalRead(SLEEP_BTN_PIN) == LOW) {
        delay(50); if (digitalRead(SLEEP_BTN_PIN) == LOW) enterSleepMode();
    }
    if (digitalRead(DEEP_SLEEP_BTN_PIN) == LOW) {
        delay(50); if (digitalRead(DEEP_SLEEP_BTN_PIN) == LOW) enterDeepSleepMode();
    }

    // Heartbeat watchdog. Reference is the last heartbeat — or systemStartMs
    // if the sender has never spoken this session (gives a fresh boot the
    // full HEARTBEAT_TIMEOUT_MS grace period before sleeping).
    uint32_t hbRef = (lastHeartbeatMs > 0) ? lastHeartbeatMs : systemStartMs;
    if (now - hbRef > HEARTBEAT_TIMEOUT_MS) {
        Serial.printf("\n*** HEARTBEAT WATCHDOG FIRED at %us uptime — sleeping ***\n",
                      (now - systemStartMs) / 1000);
        enterPowerSaveSleep();   // does not return
    }

    maintainModemAndGPS();
    // ── Keep the radio + GPRS up; reconnect MQTT if it dropped ──
    maintainGPRS();
    maintainMQTT();

    // Pump the MQTT client to process incoming/outgoing packets. Brief mutex
    // window so the publish tasks can take the line right after.
    if (mqttMutex && xSemaphoreTake(mqttMutex, pdMS_TO_TICKS(50)) == pdTRUE) {
        client.loop(); xSemaphoreGive(mqttMutex);
    }

    // Drain the offline buffer when the link comes back, OR check every
    // SYNC_INTERVAL_MS just in case (covers the "what if it filled up while
    // we were online but nothing triggered a sync" edge case).
    if (hasOfflineData || (now - lastSyncAttempt >= SYNC_INTERVAL_MS)) {
        lastSyncAttempt = now; syncOfflineData();
    }

    // ── Periodic telemetry — pure snapshot, NEVER fires sensor_alert ──
    // remote_pin = -5 forces the JSON builder to classify this as "telemetry"
    // even when pins are stuck in a 0 state (unconnected test pins, etc.).
    if (now - lastTelemetry >= TELEMETRY_MS) {
        lastTelemetry = now;
        SensorEvent t = {now,
            (uint8_t)digitalRead(PIN_S1),  (uint8_t)digitalRead(PIN_S2),
            readMag(PIN_MAG1), readMag(PIN_MAG2),
            -5};
        xQueueSend(sensorQueue, &t, 0);
    }

    if (now - lastStatusPrint >= STATUS_PRINT_MS) {
        lastStatusPrint = now;

        bool fixNow, fixEver; float lat, lng;
        portENTER_CRITICAL(&gpsMux);
        fixNow = gpsFixNow; fixEver = gpsFixEver; lat = lastLat; lng = lastLng;
        portEXIT_CRITICAL(&gpsMux);

        // On-board fuel probe reading for the status box.
        float flPct, flSmoothed; int flAdc; bool flValid;
        snapshotFuel(flPct, flAdc, flSmoothed, flValid);

        // ── Round every time value here to the same 30 s grid ──
        // The loop can fire a few seconds late, which would show "4 min 24 s"
        // instead of "4 min 30 s". I round uptime, heartbeat-age, AND the
        // sleep-in countdown to the same 30 s grid so:
        //    Running for + Sleep in  always = HEARTBEAT_TIMEOUT_MS (= 5 min)
        // and every value in the box ticks by exactly 30 s each print.
        uint32_t uptimeSec = (now - systemStartMs) / 1000;
        uptimeSec = ((uptimeSec + 15) / 30) * 30;
        char uptimeStr[32]; formatDuration(uptimeSec, uptimeStr, sizeof(uptimeStr));

        uint32_t hbRef  = (lastHeartbeatMs > 0) ? lastHeartbeatMs : systemStartMs;
        uint32_t ageMs  = now - hbRef;
        uint32_t leftMs = (ageMs >= HEARTBEAT_TIMEOUT_MS) ? 0 : (HEARTBEAT_TIMEOUT_MS - ageMs);
        uint32_t ageSec  = ((ageMs  / 1000) + 15) / 30 * 30;
        uint32_t leftSec = ((leftMs / 1000) + 15) / 30 * 30;
        char sleepStr[32]; formatDuration(leftSec, sleepStr, sizeof(sleepStr));
        char ageStr[32];   formatDuration(ageSec,  ageStr,   sizeof(ageStr));

        // ── UTC time from the GPS (set once when first fix arrives) ──
        char utcStr[32];
        if (sysTimeSet) {
            time_t t = time(nullptr);
            struct tm tm_utc;
            gmtime_r(&t, &tm_utc);
            strftime(utcStr, sizeof(utcStr), "%Y-%m-%d %H:%M:%S UTC", &tm_utc);
        } else {
            snprintf(utcStr, sizeof(utcStr), "Waiting for GPS fix...");
        }

        Serial.println("┌──────────────────  TRACKER STATUS  ──────────────────┐");
        Serial.printf( "│  Time (UTC)  : %s\n", utcStr);
        Serial.printf( "│  Running for : %s\n", uptimeStr);

        // Cellular status + LTE signal on the SAME line
        if (modemOnline && gprsLinkUp) {
            Serial.printf( "│  Cellular    : Online (4G)   |   Signal: %s (%d dBm)\n",
                           signalQualityName(lastSignalCsq), csqToDbm(lastSignalCsq));
        } else if (modemOnline) {
            Serial.printf( "│  Cellular    : Searching     |   Signal: %s\n",
                           signalQualityName(lastSignalCsq));
        } else {
            Serial.println("│  Cellular    : Modem OFF     |   Signal: --");
        }
        Serial.printf( "│  Server      : %s\n",
                       client.connected() ? "Connected" : "Disconnected");
        if      (fixNow)  Serial.printf("│  Location    : Live GPS      %.6f, %.6f\n", lat, lng);
        else if (fixEver) Serial.printf("│  Location    : Last known    %.6f, %.6f\n", lat, lng);
        else              Serial.println("│  Location    : Searching for satellites...");

        // ── On-board fuel-level reading ──
        if (flValid) {
            float liters = (flPct / 100.0f) * FUEL_TANK_CAPACITY_L;
            Serial.printf("│  Fuel Level  : %.1f %%      (%.0f / %.0f L)\n",
                          flPct, liters, FUEL_TANK_CAPACITY_L);
        } else {
            Serial.println("│  Fuel Level  : Calibrating...");
        }

        Serial.println("│");
        Serial.printf( "│  Device Case  : %s\n", last_s1   ? "Sealed"  : "TAMPERED  [!]");
        Serial.printf( "│  Fuel Cap     : %s\n", last_s2   ? "Closed"  : "OPEN  [!]");
        Serial.printf( "│  Cargo Door 1 : %s\n", last_mag1 ? "Closed"  : "OPEN  [!]");
        Serial.printf( "│  Cargo Door 2 : %s\n", last_mag2 ? "Closed"  : "OPEN  [!]");

        Serial.println("│");
        Serial.printf( "│  Pending     : %u events   |   Saved offline: %s\n",
                       (unsigned)uxQueueMessagesWaiting(sensorQueue),
                       hasOfflineData ? "yes" : "none");
        if (lastHeartbeatMs == 0) {
            Serial.printf( "│  OBD2 link   : not detected   |   Sleep in: %s\n", sleepStr);
        } else {
            Serial.printf( "│  OBD2 link   : seen %s ago   |   Sleep in: %s\n",
                           ageStr, sleepStr);
        }
        Serial.println("└──────────────────────────────────────────────────────┘");

        checkStorage();
    }
}
// © Ahmed Yousef Saeed Khalifa — All rights reserved.