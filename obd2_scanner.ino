/**
 * ============================================================
 *  MonzTrack OBD2 ESP32 Firmware — v2.0 (CORRECTED)
 *  Made by Monzer · github.com/moonr5/Vision
 * ============================================================
 *
 *  Fixes applied vs v1:
 *  [1] StaticJsonDocument increased to 768 bytes (was 512 — caused silent truncation)
 *  [2] char payload[] buffer matched to 768 bytes
 *  [3] throttle added as flat top-level field (dashboard reads data.throttle)
 *  [4] fuel_level added as flat top-level field (dashboard reads data.fuel_level)
 *  [5] speed_obd added (dashboard GPS vs OBD sync check — was stuck on WAITING)
 *  [6] mil added (MIL / check engine light indicator — was never updating)
 *  [7] fuel.level_percent added inside fuel{} object (dashboard fallback path)
 *  [8] coolant_temp added as flat top-level field (processActiveDriverBehavior reads it flat)
 *  [9] engine_load added as flat top-level field (same reason as above)
 *
 * ============================================================
 *  PUBLISHED JSON SHAPE (complete, matches dashboard exactly)
 * ============================================================
 *  {
 *    "device_id"    : "device-01",
 *    "lat"          : 0,
 *    "lng"          : 0,
 *    "speed"        : 45,
 *    "speed_obd"    : 45,        ← GPS vs OBD sync check
 *    "loc"          : 0,
 *    "sats"         : 0,
 *    "throttle"     : 18.4,      ← flat (driver behavior scoring)
 *    "fuel_level"   : 60.2,      ← flat (fuel gauge)
 *    "coolant_temp" : 88,        ← flat (driver behavior scoring)
 *    "engine_load"  : 32.5,      ← flat (driver behavior scoring)
 *    "mil"          : false,     ← check engine light
 *    "s1"           : 1,
 *    "s2"           : 1,
 *    "mag1"         : 1,
 *    "mag2"         : 1,
 *    "fuel": {
 *      "theft_detected" : false,
 *      "level_percent"  : 60.2   ← fallback path for fuel gauge
 *    },
 *    "obd": {
 *      "rpm"          : 1200,
 *      "speed"        : 45,
 *      "engine_load"  : 32.5,
 *      "coolant_temp" : 88,
 *      "intake_temp"  : 35,
 *      "throttle"     : 18.4,
 *      "fuel_level"   : 60.2,
 *      "run_time"     : 3600,
 *      "voltage"      : 14.12,
 *      "maf"          : 8.45,
 *      "fuel_press"   : 270,
 *      "map_kpa"      : 45,
 *      "timing"       : 12.5,
 *      "rail_press"   : 350.0
 *    }
 *  }
 *
 * ============================================================
 *  HARDWARE WIRING
 * ============================================================
 *  MCP2515 Pin  → ESP32 Pin
 *  VCC          → 5V
 *  GND          → GND
 *  CS           → GPIO 5
 *  MISO (SO)    → GPIO 19
 *  MOSI (SI)    → GPIO 23
 *  SCK          → GPIO 18
 *  INT          → (not used)
 *
 *  Optional Sensors  → ESP32 Pin
 *  Limit Switch S1   → GPIO 34  (INPUT_PULLUP)
 *  Limit Switch S2   → GPIO 35  (INPUT_PULLUP)
 *  Magnetic Sensor 1 → GPIO 32  (INPUT_PULLUP)
 *  Magnetic Sensor 2 → GPIO 33  (INPUT_PULLUP)
 *
 * ============================================================
 *  REQUIRED LIBRARIES (Arduino Library Manager)
 * ============================================================
 *  - mcp_can       by Cory J. Fowler
 *  - PubSubClient  by Nick O'Leary
 *  - ArduinoJson   by Benoit Blanchon  (v6)
 * ============================================================
 */

#include <SPI.h>
#include <mcp_can.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ============================================================
//  USER CONFIGURATION — edit these values
// ============================================================
const char* WIFI_SSID     = "test";
const char* WIFI_PASSWORD = "12345678";

// Public HiveMQ broker — no credentials needed
// Dashboard connects here via WSS on port 8884
// ESP32 connects here via plain TCP on port 1883
const char* MQTT_BROKER   = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;
const char* MQTT_TOPIC    = "monztrack/device01/gps";
const char* DEVICE_ID     = "device-01";

// Publish interval in milliseconds
const unsigned long PUBLISH_INTERVAL     = 3000;
const unsigned long TESTER_PRESENT_MS    = 2000;

// ============================================================
//  PIN DEFINITIONS
// ============================================================
const int CAN_CS_PIN = 5;

// Set any sensor pin to -1 if not wired — defaults to 1 (normal/green)
const int PIN_S1   = 34;
const int PIN_S2   = 35;
const int PIN_MAG1 = 32;
const int PIN_MAG2 = 33;

// ============================================================
//  OBD2 PID CONSTANTS  (SAE J1979 / ISO 15031-5)
// ============================================================
#define CAN_ID_OBD_REQUEST    0x7DF
#define OBD_SERVICE_01        0x01
#define OBD_RESPONSE_BASE     0x41   // 0x40 + service 0x01

#define PID_ENGINE_LOAD       0x04
#define PID_COOLANT_TEMP      0x05
#define PID_FUEL_PRESSURE     0x0A
#define PID_INTAKE_MAP        0x0B
#define PID_ENGINE_RPM        0x0C
#define PID_VEHICLE_SPEED     0x0D
#define PID_TIMING_ADVANCE    0x0E
#define PID_INTAKE_AIR_TEMP   0x0F
#define PID_MAF_FLOW          0x10
#define PID_THROTTLE_POS      0x11
#define PID_OBD_STANDARDS     0x1C   // used to detect MIL support
#define PID_RUN_TIME          0x1F
#define PID_FUEL_RAIL_PRESS   0x23
#define PID_FUEL_LEVEL        0x2F
#define PID_CTRL_MODULE_VOLT  0x42
#define PID_MIL_STATUS        0x01   // byte A bit7 = MIL on/off

// ============================================================
//  GLOBAL OBJECTS
// ============================================================
MCP_CAN      CAN(CAN_CS_PIN);
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

// ============================================================
//  OBD DATA STORE  (updated by processIncomingCAN)
// ============================================================
struct ObdData {
    float        rpm          = 0;
    int          speed        = 0;
    float        engineLoad   = 0;
    int          coolantTemp  = 0;
    int          intakeTemp   = 0;
    float        throttle     = 0;
    float        fuelLevel    = 0;
    unsigned int runTime      = 0;
    float        voltage      = 0;
    float        maf          = 0;
    int          fuelPressure = 0;
    int          intakeMap    = 0;
    float        timing       = 0;
    float        railPressure = 0;
    bool         mil          = false;  // Malfunction Indicator Lamp
} obd;

// ============================================================
//  TIMING TRACKERS
// ============================================================
unsigned long lastPublishMs       = 0;
unsigned long lastTesterPresentMs = 0;

// ============================================================
//  SETUP
// ============================================================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n[SYSTEM] MonzTrack OBD2 v2.0 starting...");
    Serial.println("[SYSTEM] Made by Monzer · github.com/moonr5/Vision");

    // Configure sensor input pins
    if (PIN_S1   >= 0) pinMode(PIN_S1,   INPUT_PULLUP);
    if (PIN_S2   >= 0) pinMode(PIN_S2,   INPUT_PULLUP);
    if (PIN_MAG1 >= 0) pinMode(PIN_MAG1, INPUT_PULLUP);
    if (PIN_MAG2 >= 0) pinMode(PIN_MAG2, INPUT_PULLUP);

    setupWiFi();

    mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    mqtt.setBufferSize(800);  // Headroom above our 768-byte JSON

    setupCAN();

    Serial.println("[SYSTEM] Ready. Publishing to: " + String(MQTT_TOPIC));
}

// ============================================================
//  MAIN LOOP
// ============================================================
void loop() {
    // Maintain MQTT connection
    if (WiFi.status() == WL_CONNECTED) {
        if (!mqtt.connected()) reconnectMQTT();
        mqtt.loop();
    }

    unsigned long now = millis();

    // Send Tester Present every 2 s to keep ECU in diagnostic mode
    if (now - lastTesterPresentMs >= TESTER_PRESENT_MS) {
        lastTesterPresentMs = now;
        sendTesterPresent();
    }

    // Poll all OBD PIDs then publish
    if (now - lastPublishMs >= PUBLISH_INTERVAL) {
        lastPublishMs = now;
        pollAllPIDs();
        buildAndPublish();
    }

    // Continuously drain any incoming CAN frames
    processIncomingCAN();
}

// ============================================================
//  WIFI SETUP
// ============================================================
void setupWiFi() {
    Serial.print("[WiFi] Connecting to " + String(WIFI_SSID));
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
        delay(500);
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[WiFi] Connected — IP: " + WiFi.localIP().toString());
    } else {
        Serial.println("\n[WiFi] Failed — running without network.");
    }
}

// ============================================================
//  MQTT RECONNECT
// ============================================================
void reconnectMQTT() {
    while (!mqtt.connected()) {
        if (WiFi.status() != WL_CONNECTED) return;

        // Unique client ID using chip MAC so multiple devices don't clash
        String clientId = "MonzTrack_" + String((uint32_t)ESP.getEfuseMac(), HEX);
        Serial.print("[MQTT] Connecting as " + clientId + " ... ");

        if (mqtt.connect(clientId.c_str())) {
            Serial.println("connected.");
        } else {
            Serial.println("failed (rc=" + String(mqtt.state()) + "). Retry in 3s.");
            delay(3000);
        }
    }
}

// ============================================================
//  CAN / MCP2515 SETUP
//  If no data arrives: try CAN_250KBPS, or MCP_16MHZ for 16MHz boards
// ============================================================
void setupCAN() {
    Serial.print("[CAN] Initialising MCP2515 ... ");
    while (CAN_OK != CAN.begin(MCP_ANY, CAN_500KBPS, MCP_8MHZ)) {
        Serial.println("failed, retrying...");
        delay(500);
    }
    CAN.setMode(MCP_NORMAL);
    Serial.println("ready at 500 kbps.");
}

// ============================================================
//  TESTER PRESENT  (ISO 14229 — keeps ECU awake for diagnostics)
// ============================================================
void sendTesterPresent() {
    unsigned char msg[8] = {0x02, 0x3E, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00};
    CAN.sendMsgBuf(CAN_ID_OBD_REQUEST, 0, 8, msg);
}

// ============================================================
//  SEND A SINGLE OBD2 MODE-01 PID REQUEST
// ============================================================
void sendPIDRequest(uint8_t pid) {
    unsigned char msg[8] = {0x02, OBD_SERVICE_01, pid,
                             0x55, 0x55, 0x55, 0x55, 0x55};
    CAN.sendMsgBuf(CAN_ID_OBD_REQUEST, 0, 8, msg);
}

// ============================================================
//  BLOCK & DRAIN CAN FOR ms MILLISECONDS
// ============================================================
void waitAndDrain(unsigned long ms) {
    unsigned long end = millis() + ms;
    while (millis() < end) {
        processIncomingCAN();
        yield();
    }
}

// ============================================================
//  POLL ALL SUPPORTED PIDS SEQUENTIALLY
// ============================================================
void pollAllPIDs() {
    const uint8_t pids[] = {
        PID_MIL_STATUS,       // Check engine / DTC count
        PID_ENGINE_RPM,
        PID_VEHICLE_SPEED,
        PID_ENGINE_LOAD,
        PID_COOLANT_TEMP,
        PID_INTAKE_AIR_TEMP,
        PID_THROTTLE_POS,
        PID_FUEL_LEVEL,
        PID_RUN_TIME,
        PID_CTRL_MODULE_VOLT,
        PID_MAF_FLOW,
        PID_FUEL_PRESSURE,
        PID_INTAKE_MAP,
        PID_TIMING_ADVANCE,
        PID_FUEL_RAIL_PRESS
    };

    for (uint8_t i = 0; i < sizeof(pids); i++) {
        sendPIDRequest(pids[i]);
        waitAndDrain(60);  // 60 ms per PID is sufficient for most ECUs
    }

    printOBDToSerial();
}

// ============================================================
//  PROCESS ALL AVAILABLE INCOMING CAN FRAMES
// ============================================================
void processIncomingCAN() {
    unsigned char len = 0, buf[8];
    unsigned long id  = 0;

    while (CAN_MSGAVAIL == CAN.checkReceive()) {
        CAN.readMsgBuf(&id, &len, buf);

        // OBD2 responses come from ECU IDs 0x7E8–0x7EF
        // Frame layout: [length] [0x41] [PID] [dataA] [dataB] ...
        if (len >= 3 && buf[1] == OBD_RESPONSE_BASE) {
            decodeOBDResponse(buf[2], buf);
        }

        // MIL status is in service 01 PID 01 — response byte is 0x41
        // buf[1]=0x41, buf[2]=0x01 (PID), buf[3] bit7 = MIL on/off
        if (len >= 4 && buf[1] == OBD_RESPONSE_BASE && buf[2] == 0x01) {
            obd.mil = (buf[3] & 0x80) != 0;  // bit 7 = MIL lamp
        }
    }
}

// ============================================================
//  DECODE A SINGLE OBD2 RESPONSE INTO obd STRUCT
//  All formulas from SAE J1979 / ISO 15031-5
// ============================================================
void decodeOBDResponse(uint8_t pid, unsigned char* buf) {
    switch (pid) {

        case PID_ENGINE_RPM:
            // ((A * 256) + B) / 4  → RPM
            obd.rpm = ((buf[3] * 256.0f) + buf[4]) / 4.0f;
            break;

        case PID_VEHICLE_SPEED:
            // A  → km/h
            obd.speed = buf[3];
            break;

        case PID_ENGINE_LOAD:
            // (A * 100) / 255  → %
            obd.engineLoad = (buf[3] * 100.0f) / 255.0f;
            break;

        case PID_COOLANT_TEMP:
            // A - 40  → °C
            obd.coolantTemp = buf[3] - 40;
            break;

        case PID_INTAKE_AIR_TEMP:
            // A - 40  → °C
            obd.intakeTemp = buf[3] - 40;
            break;

        case PID_THROTTLE_POS:
            // (A * 100) / 255  → %
            obd.throttle = (buf[3] * 100.0f) / 255.0f;
            break;

        case PID_FUEL_LEVEL:
            // (A * 100) / 255  → %
            obd.fuelLevel = (buf[3] * 100.0f) / 255.0f;
            break;

        case PID_RUN_TIME:
            // (A * 256) + B  → seconds
            obd.runTime = (buf[3] * 256) + buf[4];
            break;

        case PID_CTRL_MODULE_VOLT:
            // ((A * 256) + B) / 1000  → Volts
            obd.voltage = ((buf[3] * 256.0f) + buf[4]) / 1000.0f;
            break;

        case PID_MAF_FLOW:
            // ((A * 256) + B) / 100  → g/s
            obd.maf = ((buf[3] * 256.0f) + buf[4]) / 100.0f;
            break;

        case PID_FUEL_PRESSURE:
            // A * 3  → kPa gauge
            obd.fuelPressure = buf[3] * 3;
            break;

        case PID_INTAKE_MAP:
            // A  → kPa absolute
            obd.intakeMap = buf[3];
            break;

        case PID_TIMING_ADVANCE:
            // (A / 2) - 64  → degrees before TDC
            obd.timing = (buf[3] / 2.0f) - 64.0f;
            break;

        case PID_FUEL_RAIL_PRESS:
            // ((A * 256) + B) * 0.079  → kPa
            obd.railPressure = ((buf[3] * 256.0f) + buf[4]) * 0.079f;
            break;

        default:
            break;
    }
}

// ============================================================
//  READ DIGITAL SENSOR PIN
//  INPUT_PULLUP: HIGH = normal/closed (1), LOW = open/alert (0)
//  Dashboard: 1 = green (normal), 0 = red (alert)
// ============================================================
int readSensor(int pin) {
    if (pin < 0) return 1;  // Not wired → safe default
    return (digitalRead(pin) == HIGH) ? 1 : 0;
}

// ============================================================
//  BUILD COMPLETE JSON PAYLOAD AND PUBLISH VIA MQTT
// ============================================================
void buildAndPublish() {
    int s1   = readSensor(PIN_S1);
    int s2   = readSensor(PIN_S2);
    int mag1 = readSensor(PIN_MAG1);
    int mag2 = readSensor(PIN_MAG2);

    // Fuel theft heuristic: fuel cap open (S1=0) while stationary
    bool theftDetected = (s1 == 0 && obd.speed < 2);

    // --------------------------------------------------------
    //  768-byte document — sized to fit the full JSON safely
    //  (measured max JSON ~650 bytes — 768 gives safe headroom)
    // --------------------------------------------------------
    StaticJsonDocument<768> doc;

    // --- Identity ---
    doc["device_id"] = DEVICE_ID;

    // --- GPS fields (no GPS module — send 0) ---
    // Wire a UART GPS (e.g. NEO-6M) to replace these with real coords
    doc["lat"]  = 0;
    doc["lng"]  = 0;
    doc["loc"]  = 0;   // 0 = no fix, 1 = GPS locked
    doc["sats"] = 0;

    // --- Speed (top-level — dashboard primary speed display) ---
    doc["speed"]     = obd.speed;

    // --- FIX [5]: speed_obd — enables GPS vs OBD sync indicator ---
    doc["speed_obd"] = obd.speed;

    // --- FIX [3]: throttle flat — driver behavior scoring ---
    doc["throttle"]  = round(obd.throttle * 10) / 10.0f;

    // --- FIX [4]: fuel_level flat — dashboard fuel gauge ---
    doc["fuel_level"] = round(obd.fuelLevel * 10) / 10.0f;

    // --- FIX [8]: coolant_temp flat — driver behavior scoring ---
    doc["coolant_temp"] = obd.coolantTemp;

    // --- FIX [9]: engine_load flat — driver behavior scoring ---
    doc["engine_load"] = round(obd.engineLoad * 10) / 10.0f;

    // --- FIX [6]: mil — MIL / check engine light indicator ---
    doc["mil"] = obd.mil;

    // --- Security sensors ---
    doc["s1"]   = s1;
    doc["s2"]   = s2;
    doc["mag1"] = mag1;
    doc["mag2"] = mag2;

    // --- Fuel object ---
    JsonObject fuel = doc.createNestedObject("fuel");
    fuel["theft_detected"] = theftDetected;
    // FIX [7]: level_percent fallback path the dashboard also checks
    fuel["level_percent"]  = round(obd.fuelLevel * 10) / 10.0f;

    // --- Full OBD nested object (all raw sensor readings) ---
    JsonObject obdJson = doc.createNestedObject("obd");
    obdJson["rpm"]          = (int)obd.rpm;
    obdJson["speed"]        = obd.speed;
    obdJson["engine_load"]  = round(obd.engineLoad   * 10)  / 10.0f;
    obdJson["coolant_temp"] = obd.coolantTemp;
    obdJson["intake_temp"]  = obd.intakeTemp;
    obdJson["throttle"]     = round(obd.throttle     * 10)  / 10.0f;
    obdJson["fuel_level"]   = round(obd.fuelLevel    * 10)  / 10.0f;
    obdJson["run_time"]     = obd.runTime;
    obdJson["voltage"]      = round(obd.voltage      * 100) / 100.0f;
    obdJson["maf"]          = round(obd.maf          * 100) / 100.0f;
    obdJson["fuel_press"]   = obd.fuelPressure;
    obdJson["map_kpa"]      = obd.intakeMap;
    obdJson["timing"]       = round(obd.timing       * 10)  / 10.0f;
    obdJson["rail_press"]   = round(obd.railPressure * 10)  / 10.0f;
    obdJson["mil"]          = obd.mil;

    // --------------------------------------------------------
    //  Serialize — buffer matches document size (FIX [1] & [2])
    // --------------------------------------------------------
    char   payload[768];
    size_t payloadLen = serializeJson(doc, payload, sizeof(payload));

    // Safety check: if serialization was truncated, warn and skip
    if (payloadLen == 0 || payloadLen >= sizeof(payload) - 1) {
        Serial.println("[MQTT] ERROR: JSON truncated — increase buffer size!");
        return;
    }

    // --- Publish ---
    if (mqtt.connected()) {
        bool ok = mqtt.publish(MQTT_TOPIC, (uint8_t*)payload, payloadLen, false);
        Serial.println("[MQTT] " + String(payloadLen) + " bytes → " +
                       String(ok ? "OK" : "FAILED (buffer too small for broker?)"));
        Serial.println("       " + String(payload));
    } else {
        Serial.println("[MQTT] Not connected — publish skipped.");
    }
}

// ============================================================
//  SERIAL DEBUG SNAPSHOT
// ============================================================
void printOBDToSerial() {
    Serial.println("\n======= OBD2 SNAPSHOT =======");
    Serial.printf("  RPM          : %.0f RPM\n",  obd.rpm);
    Serial.printf("  Speed        : %d km/h\n",   obd.speed);
    Serial.printf("  Engine Load  : %.1f %%\n",   obd.engineLoad);
    Serial.printf("  Coolant Temp : %d C\n",      obd.coolantTemp);
    Serial.printf("  Intake Temp  : %d C\n",      obd.intakeTemp);
    Serial.printf("  Throttle     : %.1f %%\n",   obd.throttle);
    Serial.printf("  Fuel Level   : %.1f %%\n",   obd.fuelLevel);
    Serial.printf("  Run Time     : %u sec\n",    obd.runTime);
    Serial.printf("  Voltage      : %.2f V\n",    obd.voltage);
    Serial.printf("  MAF          : %.2f g/s\n",  obd.maf);
    Serial.printf("  Fuel Press   : %d kPa\n",    obd.fuelPressure);
    Serial.printf("  MAP          : %d kPa\n",    obd.intakeMap);
    Serial.printf("  Timing Adv   : %.1f deg\n",  obd.timing);
    Serial.printf("  Rail Press   : %.1f kPa\n",  obd.railPressure);
    Serial.printf("  MIL (CEL)    : %s\n",        obd.mil ? "ON" : "OFF");
    Serial.println("==============================");
}
