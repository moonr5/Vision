/*
  ============================================================
  SGU Logistics — ESP32 Dummy Telemetry Sender
  ============================================================

  Purpose  : Simulate a moving vehicle with OBD-II data so you
             can preview the full dashboard without real hardware.

  Network  : WiFi  →  HiveMQ public broker  →  dashboard
  WiFi     : SSID="test"  Password="12345678"
  Broker   : broker.hivemq.com  port 1883
  Topic    : monztrack/device01/gps   (single topic, all data)

  JSON payload structure (matches dashboard parser exactly):
  {
    "device_id" : "monztrack-01",
    "lat"       : -6.2252,
    "lng"       : 106.6552,
    "speed"     : 45.2,
    "loc"       : 1,          <- 1 = GPS fix, 0 = no fix
    "sats"      : 8,
    "obd" : {
      "speed"       : 45.2,
      "rpm"         : 1850,
      "throttle"    : 38,
      "coolant_temp": 87,
      "engine_load" : 42
    },
    "s1"   : 1,   <- limit switch 1  (1=closed, 0=open/alert)
    "s2"   : 1,   <- limit switch 2
    "mag1" : 1,   <- magnetic sensor 1
    "mag2" : 1,   <- magnetic sensor 2
    "fuel" : { "theft_detected": false }
  }

  Simulation highlights:
    - Vehicle starts at SGU campus Jakarta, loops a 5-km route
    - Speed varies 0–130 km/h with realistic ramp-up / braking
    - Periodically triggers: Speeding, Harsh Braking, Aggressive
      Launch, Cold Engine, Engine Lugging, Excessive Idling
    - Sensor events (S1 open, MAG1 trigger) fire occasionally
    - Safety score accumulates exactly as the C++ module would
    - Publishes every 3 seconds

  Libraries needed (install via Arduino Library Manager):
    - PubSubClient   by Nick O'Leary
    - ArduinoJson    by Benoit Blanchon

  Board: "ESP32 Dev Module"
  ============================================================
*/

// ── Includes ────────────────────────────────────────────────
#include <WiFi.h>
#include <WiFiClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <math.h>

// ── WiFi credentials ────────────────────────────────────────
const char* WIFI_SSID     = "test";
const char* WIFI_PASSWORD = "12345678";

// ── MQTT broker ─────────────────────────────────────────────
const char* MQTT_HOST     = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;
const char* MQTT_TOPIC    = "monztrack/device01/gps";
// Unique client ID — change the suffix if you run multiple devices
const char* MQTT_CLIENT   = "sgu-esp32-sim-01";

// ── Publish interval ────────────────────────────────────────
const unsigned long PUBLISH_MS    = 3000;   // publish every 3 s
const unsigned long WIFI_CHECK_MS = 5000;   // WiFi watchdog every 5 s
const unsigned long MQTT_RETRY_MS = 3000;   // MQTT reconnect interval

// ── Route: 10 waypoints looping around Jakarta ──────────────
// Starts near SGU campus, follows a realistic city loop
struct Waypoint { float lat; float lng; };
const int ROUTE_SIZE = 10;
const Waypoint ROUTE[ROUTE_SIZE] = {
    { -6.2252f,  106.6552f },  // 0  SGU campus
    { -6.2185f,  106.6820f },  // 1  Jl. Raya Daan Mogot
    { -6.2063f,  106.7110f },  // 2  Cengkareng
    { -6.1944f,  106.7580f },  // 3  Kalideres
    { -6.1856f,  106.7920f },  // 4  Penjaringan
    { -6.1990f,  106.8230f },  // 5  Pluit
    { -6.2215f,  106.8450f },  // 6  Ancol
    { -6.2380f,  106.8320f },  // 7  Tanjung Priok
    { -6.2470f,  106.7810f },  // 8  Sunter
    { -6.2360f,  106.7100f },  // 9  Cempaka Putih → back to 0
};

// ── Simulation scenario steps ────────────────────────────────
// Each step defines target speed, RPM, throttle, and how long to hold it
struct ScenarioStep {
    float targetSpeed;    // km/h
    int   targetRpm;
    float targetThrottle; // %
    float targetLoad;     // %
    float coolantTemp;    // °C
    int   holdSeconds;    // how long this step lasts
    bool  s1Open;         // trigger S1 sensor alert
    bool  mag1Open;       // trigger MAG1 sensor alert
    bool  fuelTheft;      // trigger fuel theft
};

const int SCENARIO_SIZE = 12;
const ScenarioStep SCENARIO[SCENARIO_SIZE] = {
    //  spd   rpm    thr   load  cool  hold  s1     mag1   theft
    {  0.0f,  800,  5.0f, 10.0f, 60.0f,  8, false, false, false },  // 0  Cold idle
    { 30.0f, 1500, 92.0f, 55.0f, 65.0f,  5, false, false, false },  // 1  Aggressive launch
    { 60.0f, 2200, 40.0f, 45.0f, 82.0f,  6, false, false, false },  // 2  Normal cruise
    {120.0f, 3800, 75.0f, 70.0f, 90.0f,  5, false, false, false },  // 3  Speeding!
    { 10.0f,  900, 10.0f, 20.0f, 90.0f,  4, false, false, false },  // 4  Harsh braking
    {  0.0f,  700,  3.0f,  8.0f, 88.0f, 12, false, false, false },  // 5  Excessive idling
    { 45.0f, 1800, 38.0f, 42.0f, 87.0f,  7, true,  false, false },  // 6  S1 opens (fuel cap)
    { 50.0f, 1950, 42.0f, 44.0f, 88.0f,  6, false, true,  false },  // 7  MAG1 opens (compartment)
    { 40.0f, 1200, 35.0f, 88.0f, 85.0f,  5, false, false, false },  // 8  Engine lugging
    { 55.0f, 3500, 60.0f, 55.0f, 62.0f,  4, false, false, false },  // 9  Cold engine abuse
    { 30.0f, 1400, 28.0f, 32.0f, 87.0f,  5, false, false, true  },  // 10 Fuel theft!
    { 55.0f, 2000, 40.0f, 43.0f, 88.0f,  8, false, false, false },  // 11 Normal driving
};

// ── Live simulated values (interpolated each loop) ───────────
float simLat        = ROUTE[0].lat;
float simLng        = ROUTE[0].lng;
float simSpeed      = 0.0f;
int   simRpm        = 800;
float simThrottle   = 5.0f;
float simLoad       = 10.0f;
float simCoolant    = 60.0f;
bool  simS1         = true;   // true = closed/normal
bool  simS2         = true;
bool  simMag1       = true;
bool  simMag2       = true;
bool  simFuelTheft  = false;
int   simSats       = 8;
bool  simGpsFix     = true;

int   scenarioIdx   = 0;        // current scenario step
int   stepSecElapsed = 0;       // seconds spent in this step
int   routeIdx      = 0;        // current route waypoint
float routeProgress = 0.0f;     // 0.0 → 1.0 between waypoints

// ── Connectivity objects ─────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ── Timing ──────────────────────────────────────────────────
unsigned long lastPublish   = 0;
unsigned long lastWifiCheck = 0;
unsigned long lastMqttRetry = 0;

// ── Forward declarations ─────────────────────────────────────
void connectWiFi();
void connectMQTT();
void maintainConnections();
void stepSimulation();
void interpolatePosition();
String buildPayload();
void publishPayload();
float lerp(float a, float b, float t);
void printStatus();

// ============================================================
// SETUP
// ============================================================
void setup() {
    Serial.begin(115200);
    delay(500);

    Serial.println(F("\n╔══════════════════════════════════════╗"));
    Serial.println(F("║  SGU Dummy Telemetry — ESP32 WiFi    ║"));
    Serial.println(F("╚══════════════════════════════════════╝\n"));

    connectWiFi();

    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setKeepAlive(60);
    mqtt.setBufferSize(512);   // 512 bytes is plenty for our payload (~280 bytes)
    connectMQTT();

    Serial.println(F("\n[SIM] Starting simulation...\n"));
}

// ============================================================
// MAIN LOOP
// ============================================================
void loop() {
    unsigned long now = millis();

    // ── Connection watchdogs ─────────────────────────────────
    maintainConnections();

    // ── Publish on interval ──────────────────────────────────
    if (now - lastPublish >= PUBLISH_MS) {
        lastPublish = now;

        stepSimulation();       // advance dummy data
        interpolatePosition();  // move along route
        publishPayload();       // send to broker
        printStatus();          // serial debug
    }

    // ── MQTT loop (processes ACKs, keeps connection alive) ───
    mqtt.loop();
}

// ============================================================
// WIFI — connect + auto-reconnect
// ============================================================
void connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.print(F("[WiFi] Connecting to '"));
    Serial.print(WIFI_SSID);
    Serial.print(F("'"));

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print('.');
        attempts++;
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.print(F("[WiFi] Connected — IP: "));
        Serial.println(WiFi.localIP());
        Serial.print(F("[WiFi] RSSI: "));
        Serial.print(WiFi.RSSI());
        Serial.println(F(" dBm"));
    } else {
        Serial.println(F("[WiFi] FAILED — will retry in loop"));
    }
}

// ============================================================
// MQTT — connect + subscribe
// ============================================================
void connectMQTT() {
    if (!WiFi.isConnected()) return;
    if (mqtt.connected()) return;

    Serial.print(F("[MQTT] Connecting to "));
    Serial.print(MQTT_HOST);
    Serial.print(F("..."));

    // Use a unique client ID (append last 3 bytes of MAC)
    char clientId[32];
    uint8_t mac[6];
    WiFi.macAddress(mac);
    snprintf(clientId, sizeof(clientId), "%s-%02X%02X%02X",
             MQTT_CLIENT, mac[3], mac[4], mac[5]);

    if (mqtt.connect(clientId)) {
        Serial.println(F(" connected"));
    } else {
        Serial.print(F(" FAILED, state="));
        Serial.println(mqtt.state());
        // state codes:
        // -4 = MQTT_CONNECTION_TIMEOUT
        // -3 = MQTT_CONNECTION_LOST
        // -2 = MQTT_CONNECT_FAILED
        // -1 = MQTT_DISCONNECTED
        //  1 = MQTT_CONNECT_BAD_PROTOCOL
        //  2 = MQTT_CONNECT_BAD_CLIENT_ID
        //  5 = MQTT_CONNECT_UNAUTHORIZED
    }
}

// ============================================================
// MAINTAIN CONNECTIONS — called every loop()
// ============================================================
void maintainConnections() {
    unsigned long now = millis();

    // WiFi watchdog
    if (now - lastWifiCheck >= WIFI_CHECK_MS) {
        lastWifiCheck = now;
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println(F("[WiFi] Disconnected — reconnecting..."));
            connectWiFi();
        }
    }

    // MQTT watchdog
    if (!mqtt.connected()) {
        if (now - lastMqttRetry >= MQTT_RETRY_MS) {
            lastMqttRetry = now;
            Serial.println(F("[MQTT] Disconnected — reconnecting..."));
            connectMQTT();
        }
    }
}

// ============================================================
// STEP SIMULATION
// Advances the scenario step every N seconds, interpolates
// all OBD values toward the step's targets smoothly.
// ============================================================
void stepSimulation() {
    const ScenarioStep& step = SCENARIO[scenarioIdx];
    float alpha = 0.25f;  // smoothing factor (0=no change, 1=instant)

    // Smooth interpolation toward targets
    simSpeed    = lerp(simSpeed,    step.targetSpeed,    alpha);
    simRpm      = (int)lerp((float)simRpm, (float)step.targetRpm, alpha);
    simThrottle = lerp(simThrottle, step.targetThrottle, alpha);
    simLoad     = lerp(simLoad,     step.targetLoad,     alpha);
    simCoolant  = lerp(simCoolant,  step.coolantTemp,    0.05f); // coolant warms slowly

    // Sensor states come directly from the step
    simS1        = !step.s1Open;    // true=closed(normal), false=open(alert)
    simS2        = true;            // S2 always normal in simulation
    simMag1      = !step.mag1Open;
    simMag2      = true;
    simFuelTheft = step.fuelTheft;

    // Advance step timer
    stepSecElapsed++;
    if (stepSecElapsed >= step.holdSeconds) {
        stepSecElapsed = 0;
        scenarioIdx    = (scenarioIdx + 1) % SCENARIO_SIZE;
        Serial.print(F("[SIM] → Scenario step "));
        Serial.println(scenarioIdx);
    }

    // Satellite count: flicker occasionally
    simSats    = 7 + (int)(sin(millis() / 15000.0f) * 2.0f);
    simGpsFix  = (simSats >= 4);
}

// ============================================================
// INTERPOLATE POSITION
// Moves the GPS coordinate along the route at current speed.
// ============================================================
void interpolatePosition() {
    if (!simGpsFix || simSpeed < 1.0f) return;

    // Distance per publish interval in degrees (very rough)
    // 1 degree lat ≈ 111 km → per 3 s at speed km/h:
    float distDeg = (simSpeed / 3600.0f) * (PUBLISH_MS / 1000.0f) / 111.0f;
    routeProgress += distDeg * 25.0f; // scale to waypoint units

    while (routeProgress >= 1.0f) {
        routeProgress -= 1.0f;
        routeIdx = (routeIdx + 1) % ROUTE_SIZE;
    }

    int nextIdx = (routeIdx + 1) % ROUTE_SIZE;
    simLat = lerp(ROUTE[routeIdx].lat, ROUTE[nextIdx].lat, routeProgress);
    simLng = lerp(ROUTE[routeIdx].lng, ROUTE[nextIdx].lng, routeProgress);
}

// ============================================================
// BUILD JSON PAYLOAD
// Matches the exact structure parsed by onMessageArrived()
// in index.html.
// Target size: ~280 bytes well within 512-byte buffer.
// ============================================================
String buildPayload() {
    // StaticJsonDocument sized to fit the payload comfortably
    // Use https://arduinojson.org/v6/assistant/ to tune if needed
    StaticJsonDocument<320> doc;

    doc["device_id"] = "monztrack-01";
    doc["lat"]       = serialized(String(simLat, 6));
    doc["lng"]       = serialized(String(simLng, 6));
    doc["speed"]     = serialized(String(simSpeed, 1));
    doc["loc"]       = simGpsFix ? 1 : 0;
    doc["sats"]      = simSats;

    // Nested OBD object — matches payload.obd.* in dashboard
    JsonObject obd = doc.createNestedObject("obd");
    obd["speed"]        = serialized(String(simSpeed, 1));
    obd["rpm"]          = simRpm;
    obd["throttle"]     = (int)simThrottle;
    obd["coolant_temp"] = (int)simCoolant;
    obd["engine_load"]  = (int)simLoad;

    // Flat sensor fields — matches payload.s1, .s2, .mag1, .mag2
    doc["s1"]   = simS1   ? 1 : 0;
    doc["s2"]   = simS2   ? 1 : 0;
    doc["mag1"] = simMag1 ? 1 : 0;
    doc["mag2"] = simMag2 ? 1 : 0;

    // Nested fuel object — matches payload.fuel.theft_detected
    JsonObject fuel = doc.createNestedObject("fuel");
    fuel["theft_detected"] = simFuelTheft;

    String output;
    output.reserve(320);
    serializeJson(doc, output);
    return output;
}

// ============================================================
// PUBLISH
// ============================================================
void publishPayload() {
    if (!mqtt.connected()) return;

    String payload = buildPayload();

    bool ok = mqtt.publish(MQTT_TOPIC, payload.c_str(), false);  // false = not retained

    if (ok) {
        Serial.print(F("[MQTT] ✓ Published ("));
        Serial.print(payload.length());
        Serial.println(F(" bytes)"));
    } else {
        Serial.println(F("[MQTT] ✗ Publish failed — buffer overflow or disconnected"));
        Serial.print(F("[MQTT] Payload size: "));
        Serial.println(payload.length());
    }
}

// ============================================================
// HELPERS
// ============================================================
float lerp(float a, float b, float t) {
    return a + t * (b - a);
}

void printStatus() {
    const ScenarioStep& step = SCENARIO[scenarioIdx];

    Serial.println(F("┌─────── DUMMY TELEMETRY ──────────────┐"));
    Serial.print(F("│ Scenario : step ")); Serial.print(scenarioIdx);
    Serial.print(F("  (")); Serial.print(stepSecElapsed);
    Serial.print(F("/")); Serial.print(step.holdSeconds);
    Serial.println(F("s)"));

    Serial.print(F("│ GPS      : "));
    Serial.print(simLat, 5); Serial.print(F(", "));
    Serial.print(simLng, 5);
    Serial.print(F("  fix=")); Serial.println(simGpsFix ? "YES" : "NO");

    Serial.print(F("│ Speed    : ")); Serial.print(simSpeed, 1); Serial.println(F(" km/h"));
    Serial.print(F("│ RPM      : ")); Serial.println(simRpm);
    Serial.print(F("│ Throttle : ")); Serial.print(simThrottle, 1); Serial.println(F(" %"));
    Serial.print(F("│ Coolant  : ")); Serial.print(simCoolant, 1); Serial.println(F(" °C"));
    Serial.print(F("│ Load     : ")); Serial.print(simLoad, 1); Serial.println(F(" %"));

    Serial.print(F("│ Sensors  : S1=")); Serial.print(simS1 ? "OK" : "OPEN");
    Serial.print(F(" S2=")); Serial.print(simS2 ? "OK" : "OPEN");
    Serial.print(F(" M1=")); Serial.print(simMag1 ? "OK" : "OPEN");
    Serial.print(F(" M2=")); Serial.println(simMag2 ? "OK" : "OPEN");

    if (simFuelTheft) Serial.println(F("│ ⚠  FUEL THEFT SIMULATED"));

    Serial.print(F("│ WiFi     : "));
    Serial.print(WiFi.status() == WL_CONNECTED ? "CONNECTED" : "DISCONNECTED");
    Serial.print(F("  RSSI=")); Serial.print(WiFi.RSSI()); Serial.println(F(" dBm"));

    Serial.print(F("│ MQTT     : "));
    Serial.println(mqtt.connected() ? "CONNECTED" : "DISCONNECTED");
    Serial.println(F("└──────────────────────────────────────┘"));
}
