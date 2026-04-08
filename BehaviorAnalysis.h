/**
 * BehaviorAnalysis.h
 * 
 * Header file for the Driver Behavior Analysis module.
 * Part of the SGU Logistics & Telemetry System.
 * 
 * This module analyzes OBD-II telemetry data to detect:
 * - Harsh Braking (speed drop > 15 km/h in 3 seconds)
 * - Aggressive Launch (throttle > 90% at low speed)
 * - Cold Engine Abuse (high RPM with low coolant temp)
 * - Engine Lugging (high load at low RPM)
 * - Excessive Idling (speed = 0, RPM > 500 for > 180s)
 * - Speeding (speed > 110 km/h)
 * 
 * Safety-First Architecture:
 * This module is completely isolated from communication engines (MQTT, SD Card)
 * to ensure critical driving alerts are processed reliably.
 */

#ifndef BEHAVIOR_ANALYSIS_H
#define BEHAVIOR_ANALYSIS_H

#include <Arduino.h>

// Safety thresholds (configurable)
#define HARSH_BRAKING_THRESHOLD     15.0    // km/h drop in 3 seconds
#define AGGRESSIVE_LAUNCH_THROTTLE  90      // % throttle
#define AGGRESSIVE_LAUNCH_SPEED     30      // km/h max speed
#define COLD_ENGINE_RPM             3000    // RPM threshold
#define COLD_ENGINE_TEMP            70      // °C coolant temp
#define ENGINE_LUGGING_LOAD         85      // % engine load
#define ENGINE_LUGGING_RPM          1500    // RPM threshold
#define EXCESSIVE_IDLING_TIME       180     // seconds
#define EXCESSIVE_IDLING_RPM        500     // RPM threshold
#define SPEEDING_THRESHOLD          110     // km/h

// Behavior event types
enum BehaviorEvent {
    EVENT_NONE = 0,
    EVENT_HARSH_BRAKING,
    EVENT_AGGRESSIVE_LAUNCH,
    EVENT_COLD_ENGINE_ABUSE,
    EVENT_ENGINE_LUGGING,
    EVENT_EXCESSIVE_IDLING,
    EVENT_SPEEDING
};

// Event structure for logging
struct BehaviorEventLog {
    BehaviorEvent type;
    unsigned long timestamp;
    float value;
    float secondaryValue;
    int severity;  // 1=INFO, 2=WARNING, 3=CRITICAL
};

class BehaviorAnalysis {
public:
    // Constructor
    BehaviorAnalysis();
    
    // Initialize the behavior tracker
    void begin();
    
    // Process telemetry data - call this in your main loop
    BehaviorEvent processTelemetry(
        float speed,        // km/h
        int rpm,
        float throttle,     // %
        float coolantTemp,  // °C
        float engineLoad,   // %
        bool hasFix         // GPS fix status
    );
    
    // Get current safety score (0-100)
    int getSafetyScore();
    
    // Get total event count
    int getTotalEvents();
    
    // Get event counts by type
    int getEventCount(BehaviorEvent event);
    
    // Get current/recent event
    BehaviorEvent getCurrentEvent();
    const char* getCurrentEventName();
    
    // Reset all statistics
    void reset();
    
    // Get event log (circular buffer)
    bool getEventLog(int index, BehaviorEventLog &log);
    int getEventLogCount();
    
    // Check if driver is currently idling
    bool isIdling();
    unsigned long getIdleTime();
    
    // Configuration setters
    void setHarshBrakingThreshold(float threshold);
    void setSpeedingThreshold(float threshold);
    void setColdEngineThresholds(int rpm, int temp);
    
private:
    // Tracking variables (persist between loops)
    float _previousSpeed;
    float _previousThrottle;
    unsigned long _lastUpdateTime;
    unsigned long _idleStartTime;
    bool _isIdling;
    
    // Safety metrics
    int _safetyScore;
    int _eventCounts[7];  // Indexed by BehaviorEvent
    int _totalEvents;
    BehaviorEvent _currentEvent;
    
    // Event log (circular buffer)
    static const int LOG_SIZE = 20;
    BehaviorEventLog _eventLog[LOG_SIZE];
    int _logIndex;
    int _logCount;
    
    // Configuration
    float _harshBrakingThreshold;
    float _speedingThreshold;
    int _coldEngineRpm;
    int _coldEngineTemp;
    
    // Internal methods
    void _logEvent(BehaviorEvent event, float value, float secondaryValue, int severity);
    void _updateSafetyScore(BehaviorEvent event);
    const char* _getEventName(BehaviorEvent event);
    int _calculateEventSeverity(BehaviorEvent event);
};

#endif // BEHAVIOR_ANALYSIS_H
