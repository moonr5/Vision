/**
 * BehaviorAnalysis.cpp
 *
 * Implementation of the Driver Behavior Analysis module.
 * Made by Monzer · github.com/moonr5/Vision
 *
 * "Pizza Shop Theory" Architecture:
 * This module acts as the "chef" that processes raw OBD-II ingredients
 * into actionable safety alerts. It operates independently of the
 * "delivery drivers" (MQTT, SD Card) to ensure reliability.
 */

#include "BehaviorAnalysis.h"

// Constructor
BehaviorAnalysis::BehaviorAnalysis() 
    : _previousSpeed(0.0f),
      _previousThrottle(0.0f),
      _lastUpdateTime(0),
      _idleStartTime(0),
      _isIdling(false),
      _safetyScore(100),
      _totalEvents(0),
      _currentEvent(EVENT_NONE),
      _logIndex(0),
      _logCount(0),
      _harshBrakingThreshold(HARSH_BRAKING_THRESHOLD),
      _speedingThreshold(SPEEDING_THRESHOLD),
      _coldEngineRpm(COLD_ENGINE_RPM),
      _coldEngineTemp(COLD_ENGINE_TEMP) {
    
    // Initialize event counts
    for (int i = 0; i < 7; i++) {
        _eventCounts[i] = 0;
    }
}

// Initialize the behavior tracker
void BehaviorAnalysis::begin() {
    _lastUpdateTime = millis();
    _idleStartTime = 0;
    _isIdling = false;
    _safetyScore = 100;
    _totalEvents = 0;
    _currentEvent = EVENT_NONE;
    
    for (int i = 0; i < 7; i++) {
        _eventCounts[i] = 0;
    }
    
    _logIndex = 0;
    _logCount = 0;
    
    Serial.println("[BEHAVIOR] Analysis module initialized");
    Serial.println("[BEHAVIOR] Safety Score: 100/100");
}

// Process telemetry data and return detected event
BehaviorEvent BehaviorAnalysis::processTelemetry(
    float speed,
    int rpm,
    float throttle,
    float coolantTemp,
    float engineLoad,
    bool hasFix) {
    
    unsigned long now = millis();
    BehaviorEvent detectedEvent = EVENT_NONE;
    
    // Skip processing if no GPS fix and speed is invalid
    if (!hasFix && speed <= 0) {
        return EVENT_NONE;
    }
    
    // ============================================
    // 1. HARSH BRAKING DETECTION
    // Speed drop > 15 km/h in ~3 seconds
    // ============================================
    float speedDelta = _previousSpeed - speed;
    if (speedDelta > _harshBrakingThreshold && _previousSpeed > 20.0f) {
        detectedEvent = EVENT_HARSH_BRAKING;
        _eventCounts[EVENT_HARSH_BRAKING]++;
        _logEvent(EVENT_HARSH_BRAKING, speedDelta, speed, 2); // WARNING
        _updateSafetyScore(EVENT_HARSH_BRAKING);
        
        Serial.print("[BEHAVIOR] HARSH BRAKING: ");
        Serial.print(speedDelta, 1);
        Serial.print(" km/h drop (now at ");
        Serial.print(speed, 1);
        Serial.println(" km/h)");
    }
    
    // ============================================
    // 2. AGGRESSIVE LAUNCH DETECTION
    // Throttle > 90% while speed < 30 km/h
    // ============================================
    if (throttle > AGGRESSIVE_LAUNCH_THROTTLE && 
        speed < AGGRESSIVE_LAUNCH_SPEED && 
        speed > 0 &&
        _previousThrottle <= AGGRESSIVE_LAUNCH_THROTTLE) {
        
        detectedEvent = EVENT_AGGRESSIVE_LAUNCH;
        _eventCounts[EVENT_AGGRESSIVE_LAUNCH]++;
        _logEvent(EVENT_AGGRESSIVE_LAUNCH, throttle, speed, 2); // WARNING
        _updateSafetyScore(EVENT_AGGRESSIVE_LAUNCH);
        
        Serial.print("[BEHAVIOR] AGGRESSIVE LAUNCH: ");
        Serial.print(throttle, 1);
        Serial.print("% throttle at ");
        Serial.print(speed, 1);
        Serial.println(" km/h");
    }
    
    // ============================================
    // 3. COLD ENGINE ABUSE DETECTION
    // RPM > 3000 while coolant temp < 70°C
    // ============================================
    if (rpm > _coldEngineRpm && 
        coolantTemp > 0 && 
        coolantTemp < _coldEngineTemp) {
        
        detectedEvent = EVENT_COLD_ENGINE_ABUSE;
        _eventCounts[EVENT_COLD_ENGINE_ABUSE]++;
        _logEvent(EVENT_COLD_ENGINE_ABUSE, rpm, coolantTemp, 2); // WARNING
        _updateSafetyScore(EVENT_COLD_ENGINE_ABUSE);
        
        Serial.print("[BEHAVIOR] COLD ENGINE ABUSE: ");
        Serial.print(rpm);
        Serial.print(" RPM at ");
        Serial.print(coolantTemp, 1);
        Serial.println("°C");
    }
    
    // ============================================
    // 4. ENGINE LUGGING DETECTION
    // Load > 85% while RPM < 1500
    // ============================================
    if (engineLoad > ENGINE_LUGGING_LOAD && 
        rpm > 0 && 
        rpm < ENGINE_LUGGING_RPM) {
        
        detectedEvent = EVENT_ENGINE_LUGGING;
        _eventCounts[EVENT_ENGINE_LUGGING]++;
        _logEvent(EVENT_ENGINE_LUGGING, engineLoad, rpm, 2); // WARNING
        _updateSafetyScore(EVENT_ENGINE_LUGGING);
        
        Serial.print("[BEHAVIOR] ENGINE LUGGING: ");
        Serial.print(engineLoad, 1);
        Serial.print("% load at ");
        Serial.print(rpm);
        Serial.println(" RPM");
    }
    
    // ============================================
    // 5. EXCESSIVE IDLING DETECTION
    // Speed == 0 and RPM > 500 for > 180 seconds
    // ============================================
    if (speed == 0 && rpm > EXCESSIVE_IDLING_RPM) {
        if (!_isIdling) {
            _isIdling = true;
            _idleStartTime = now;
        } else {
            unsigned long idleDuration = (now - _idleStartTime) / 1000;
            if (idleDuration > EXCESSIVE_IDLING_TIME) {
                detectedEvent = EVENT_EXCESSIVE_IDLING;
                _eventCounts[EVENT_EXCESSIVE_IDLING]++;
                _logEvent(EVENT_EXCESSIVE_IDLING, idleDuration, rpm, 1); // INFO
                _updateSafetyScore(EVENT_EXCESSIVE_IDLING);
                
                Serial.print("[BEHAVIOR] EXCESSIVE IDLING: ");
                Serial.print(idleDuration);
                Serial.println(" seconds");
                
                // Reset to avoid multiple counts for same idling period
                _idleStartTime = now;
            }
        }
    } else {
        _isIdling = false;
        _idleStartTime = 0;
    }
    
    // ============================================
    // 6. SPEEDING DETECTION
    // Speed > 110 km/h
    // ============================================
    if (speed > _speedingThreshold) {
        detectedEvent = EVENT_SPEEDING;
        _eventCounts[EVENT_SPEEDING]++;
        _logEvent(EVENT_SPEEDING, speed, 0, 3); // CRITICAL
        _updateSafetyScore(EVENT_SPEEDING);
        
        Serial.print("[BEHAVIOR] SPEEDING: ");
        Serial.print(speed, 1);
        Serial.println(" km/h");
    }
    
    // Update tracking variables
    _previousSpeed = speed;
    _previousThrottle = throttle;
    _lastUpdateTime = now;
    
    // Update current event
    if (detectedEvent != EVENT_NONE) {
        _currentEvent = detectedEvent;
        _totalEvents++;
    } else if (_safetyScore >= 90) {
        _currentEvent = EVENT_NONE; // Normal driving
    }
    
    return detectedEvent;
}

// Get current safety score (0-100)
int BehaviorAnalysis::getSafetyScore() {
    return _safetyScore;
}

// Get total event count
int BehaviorAnalysis::getTotalEvents() {
    return _totalEvents;
}

// Get event count by type
int BehaviorAnalysis::getEventCount(BehaviorEvent event) {
    if (event >= 0 && event < 7) {
        return _eventCounts[event];
    }
    return 0;
}

// Get current/recent event
BehaviorEvent BehaviorAnalysis::getCurrentEvent() {
    return _currentEvent;
}

const char* BehaviorAnalysis::getCurrentEventName() {
    return _getEventName(_currentEvent);
}

// Reset all statistics
void BehaviorAnalysis::reset() {
    begin();
}

// Get event log entry
bool BehaviorAnalysis::getEventLog(int index, BehaviorEventLog &log) {
    if (index < 0 || index >= _logCount) {
        return false;
    }
    
    int actualIndex = (_logIndex - _logCount + index) % LOG_SIZE;
    if (actualIndex < 0) actualIndex += LOG_SIZE;
    
    log = _eventLog[actualIndex];
    return true;
}

// Get number of events in log
int BehaviorAnalysis::getEventLogCount() {
    return _logCount;
}

// Check if driver is currently idling
bool BehaviorAnalysis::isIdling() {
    return _isIdling;
}

// Get current idle time in seconds
unsigned long BehaviorAnalysis::getIdleTime() {
    if (!_isIdling) return 0;
    return (millis() - _idleStartTime) / 1000;
}

// Configuration setters
void BehaviorAnalysis::setHarshBrakingThreshold(float threshold) {
    _harshBrakingThreshold = threshold;
}

void BehaviorAnalysis::setSpeedingThreshold(float threshold) {
    _speedingThreshold = threshold;
}

void BehaviorAnalysis::setColdEngineThresholds(int rpm, int temp) {
    _coldEngineRpm = rpm;
    _coldEngineTemp = temp;
}

// Private: Log event to circular buffer
void BehaviorAnalysis::_logEvent(BehaviorEvent event, float value, float secondaryValue, int severity) {
    BehaviorEventLog log;
    log.type = event;
    log.timestamp = millis();
    log.value = value;
    log.secondaryValue = secondaryValue;
    log.severity = severity;
    
    _eventLog[_logIndex] = log;
    _logIndex = (_logIndex + 1) % LOG_SIZE;
    
    if (_logCount < LOG_SIZE) {
        _logCount++;
    }
}

// Private: Update safety score based on event
void BehaviorAnalysis::_updateSafetyScore(BehaviorEvent event) {
    switch (event) {
        case EVENT_HARSH_BRAKING:
            _safetyScore -= 5;
            break;
        case EVENT_AGGRESSIVE_LAUNCH:
            _safetyScore -= 4;
            break;
        case EVENT_COLD_ENGINE_ABUSE:
            _safetyScore -= 3;
            break;
        case EVENT_ENGINE_LUGGING:
            _safetyScore -= 4;
            break;
        case EVENT_EXCESSIVE_IDLING:
            _safetyScore -= 2;
            break;
        case EVENT_SPEEDING:
            _safetyScore -= 6;
            break;
        default:
            break;
    }
    
    // Clamp score to 0-100
    if (_safetyScore < 0) _safetyScore = 0;
    if (_safetyScore > 100) _safetyScore = 100;
    
    // Log score changes
    static int lastReportedScore = 100;
    if (abs(_safetyScore - lastReportedScore) >= 5) {
        Serial.print("[BEHAVIOR] Safety Score: ");
        Serial.print(_safetyScore);
        Serial.println("/100");
        lastReportedScore = _safetyScore;
    }
}

// Private: Get event name string
const char* BehaviorAnalysis::_getEventName(BehaviorEvent event) {
    switch (event) {
        case EVENT_NONE: return "Normal";
        case EVENT_HARSH_BRAKING: return "Harsh Braking";
        case EVENT_AGGRESSIVE_LAUNCH: return "Aggressive Launch";
        case EVENT_COLD_ENGINE_ABUSE: return "Cold Engine Abuse";
        case EVENT_ENGINE_LUGGING: return "Engine Lugging";
        case EVENT_EXCESSIVE_IDLING: return "Excessive Idling";
        case EVENT_SPEEDING: return "Speeding";
        default: return "Unknown";
    }
}

// Private: Calculate event severity
int BehaviorAnalysis::_calculateEventSeverity(BehaviorEvent event) {
    switch (event) {
        case EVENT_SPEEDING:
            return 3; // CRITICAL
        case EVENT_HARSH_BRAKING:
        case EVENT_AGGRESSIVE_LAUNCH:
        case EVENT_ENGINE_LUGGING:
            return 2; // WARNING
        case EVENT_COLD_ENGINE_ABUSE:
        case EVENT_EXCESSIVE_IDLING:
            return 1; // INFO
        default:
            return 0;
    }
}
