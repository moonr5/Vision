-- SGU Logistics & Telemetry Database Schema
-- SQLite Database for orders, telemetry, events, and system configuration

-- Enable foreign keys
PRAGMA foreign_keys = ON;

-- ============================================
-- CORE TABLES
-- ============================================

-- Drivers/Vehicles table
CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    license_number TEXT,
    vehicle_id TEXT,
    vehicle_model TEXT,
    vehicle_plate TEXT,
    vehicle_weight INTEGER,
    avatar TEXT, -- Base64 encoded image
    status TEXT DEFAULT 'active', -- active, inactive, suspended
    safety_score INTEGER, -- Driver safety score (0-100)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Devices table (IoT tracking devices)
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT,
    mqtt_topic TEXT,
    device_type TEXT DEFAULT 'gps_tracker', -- gps_tracker, obd_adapter, sensor_node
    status TEXT DEFAULT 'offline', -- online, offline, maintenance
    last_seen DATETIME,
    assigned_driver_id TEXT,
    assigned_vehicle_id TEXT,
    settings TEXT, -- JSON configuration
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (assigned_driver_id) REFERENCES drivers(id) ON DELETE SET NULL
);

-- Customers table
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    company TEXT,
    address TEXT,
    city TEXT,
    country TEXT DEFAULT 'ID',
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Orders/Shipments table
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL, -- Human-readable order number
    type TEXT DEFAULT 'domestic', -- domestic, transit, international
    status TEXT DEFAULT 'pickup', -- pickup, transit, delivery, completed, cancelled
    
    -- Origin location
    origin_city TEXT,
    origin_address TEXT,
    origin_lat REAL,
    origin_lng REAL,
    
    -- Destination location
    destination_city TEXT,
    destination_address TEXT,
    destination_lat REAL,
    destination_lng REAL,
    
    -- Customer info
    customer_id TEXT,
    customer_name TEXT,
    customer_phone TEXT,
    
    -- Assignment
    driver_id TEXT,
    vehicle_id TEXT,
    device_id TEXT,
    
    -- Dates
    pickup_date DATE,
    delivery_date DATE,
    actual_pickup_at DATETIME,
    actual_delivery_at DATETIME,
    
    -- Tracking
    current_lat REAL,
    current_lng REAL,
    current_speed REAL,
    last_update DATETIME,
    
    -- Metadata
    cargo_description TEXT,
    cargo_weight REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
    FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL
);

-- ============================================
-- TELEMETRY & SENSOR DATA
-- ============================================

-- Telemetry snapshots (from IoT devices)
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    order_id TEXT,
    
    -- Timestamp
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- GPS Data
    lat REAL,
    lng REAL,
    altitude REAL,
    speed REAL,
    heading REAL,
    accuracy REAL,
    satellites INTEGER,
    gps_fix INTEGER, -- 0 = no fix, 1 = fix acquired
    
    -- Sensors (S1, S2 limit switches; MAG1, MAG2 magnetic sensors)
    sensor_s1 INTEGER, -- 0 = open/triggered, 1 = closed/normal
    sensor_s2 INTEGER,
    sensor_mag1 INTEGER,
    sensor_mag2 INTEGER,
    
    -- OBD-II Data
    obd_rpm INTEGER,
    obd_speed REAL, -- from OBD (may differ from GPS)
    obd_engine_load REAL,
    obd_coolant_temp REAL,
    obd_throttle REAL,
    obd_mil BOOLEAN, -- Check engine light
    obd_vin TEXT,
    
    -- Fuel System
    fuel_level REAL, -- percentage
    fuel_flow_in REAL,
    fuel_flow_out REAL,
    fuel_cap_open BOOLEAN,
    fuel_theft_detected BOOLEAN,
    
    -- Calculated metrics
    distance_km REAL,
    geofence_active BOOLEAN,
    geofence_inside BOOLEAN,
    
    -- Raw payload (for debugging/completeness)
    raw_payload TEXT,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

-- Create indexes for telemetry queries
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_order ON telemetry(order_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON timestamp(timestamp);

-- ============================================
-- EVENTS & REPORTS
-- ============================================

-- System events and alerts
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, -- INFO, WARNING, CRITICAL, TIMEOUT
    category TEXT, -- TELEMETRY, GEOFENCE, SECURITY, FUEL, SYSTEM
    event TEXT NOT NULL,
    
    -- Related entities
    device_id TEXT,
    order_id TEXT,
    driver_id TEXT,
    
    -- Location (if applicable)
    lat REAL,
    lng REAL,
    location_text TEXT,
    
    -- Event details
    details TEXT,
    speed REAL,
    
    -- Sensor states at time of event
    sensor_s1 INTEGER,
    sensor_s2 INTEGER,
    sensor_mag1 INTEGER,
    sensor_mag2 INTEGER,
    gps_has_fix BOOLEAN,
    
    -- Timestamps
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- Alert management
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,
    acknowledged_at DATETIME,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
);

-- Create indexes for event queries
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);

-- ============================================
-- GEOFENCING
-- ============================================

-- Geofence definitions
CREATE TABLE IF NOT EXISTS geofences (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    
    -- Geometry type: circle, polygon, rectangle
    geometry_type TEXT DEFAULT 'circle',
    
    -- For circles: center + radius
    center_lat REAL,
    center_lng REAL,
    radius_meters REAL,
    
    -- For polygons/rectangles: GeoJSON polygon
    coordinates TEXT, -- JSON array of [lng, lat] pairs
    
    -- Associated entities
    assigned_device_id TEXT,
    assigned_order_id TEXT,
    
    -- Alert settings
    alert_on_enter BOOLEAN DEFAULT TRUE,
    alert_on_exit BOOLEAN DEFAULT TRUE,
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (assigned_device_id) REFERENCES devices(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_order_id) REFERENCES orders(id) ON DELETE SET NULL
);

-- Geofence event log
CREATE TABLE IF NOT EXISTS geofence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    geofence_id TEXT NOT NULL,
    device_id TEXT,
    order_id TEXT,
    event_type TEXT, -- ENTER, EXIT
    lat REAL,
    lng REAL,
    triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (geofence_id) REFERENCES geofences(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
);

-- ============================================
-- ANALYTICS & AGGREGATES
-- ============================================

-- Trip/drive sessions for analytics
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    order_id TEXT,
    driver_id TEXT,
    
    -- Time bounds
    started_at DATETIME,
    ended_at DATETIME,
    duration_seconds INTEGER,
    
    -- Distance
    start_lat REAL,
    start_lng REAL,
    end_lat REAL,
    end_lng REAL,
    total_distance_km REAL,
    
    -- Speed statistics
    max_speed REAL,
    avg_speed REAL,
    
    -- Fuel statistics
    fuel_consumed REAL,
    fuel_efficiency REAL, -- km per liter
    
    -- Event counts
    events_critical INTEGER DEFAULT 0,
    events_warning INTEGER DEFAULT 0,
    events_info INTEGER DEFAULT 0,
    
    -- Status
    status TEXT DEFAULT 'in_progress', -- in_progress, completed, interrupted
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
    FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
);

-- ============================================
-- SYSTEM CONFIGURATION
-- ============================================

-- Application settings (replaces localStorage for critical settings)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    value_type TEXT DEFAULT 'string', -- string, number, boolean, json
    description TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- API usage tracking
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name TEXT, -- gemini, telegram, tidb, etc.
    action TEXT,
    request_payload TEXT,
    response_status TEXT,
    meta TEXT, -- JSON metadata
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- VIEWS
-- ============================================

-- Active orders with current location
CREATE VIEW IF NOT EXISTS v_active_orders AS
SELECT 
    o.*,
    d.name as driver_name,
    d.phone as driver_phone,
    d.vehicle_plate,
    dev.status as device_status,
    dev.last_seen as device_last_seen
FROM orders o
LEFT JOIN drivers d ON o.driver_id = d.id
LEFT JOIN devices dev ON o.device_id = dev.id
WHERE o.status NOT IN ('completed', 'cancelled');

-- Device telemetry summary (latest per device)
CREATE VIEW IF NOT EXISTS v_device_latest_telemetry AS
SELECT t.*
FROM telemetry t
INNER JOIN (
    SELECT device_id, MAX(timestamp) as max_ts
    FROM telemetry
    GROUP BY device_id
) latest ON t.device_id = latest.device_id AND t.timestamp = latest.max_ts;

-- Event summary by type (last 24 hours)
CREATE VIEW IF NOT EXISTS v_event_summary_24h AS
SELECT 
    type,
    category,
    COUNT(*) as count
FROM events
WHERE created_at >= datetime('now', '-1 day')
GROUP BY type, category;

-- Order statistics
CREATE VIEW IF NOT EXISTS v_order_stats AS
SELECT 
    status,
    type,
    COUNT(*) as count,
    MIN(created_at) as oldest_order,
    MAX(updated_at) as latest_update
FROM orders
GROUP BY status, type;

-- ============================================
-- DRIVER BEHAVIOR HISTORY
-- ============================================

-- Persistent history of driver behavior events (replaces localStorage-only storage)
CREATE TABLE IF NOT EXISTS driver_behavior_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id   TEXT    NOT NULL,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Snapshot at event time
    safety_score INTEGER,
    event_name   TEXT,   -- e.g. "Speeding", "Harsh Braking"
    event_type   TEXT,   -- CRITICAL | WARNING | INFO
    event_details TEXT,  -- human-readable description

    -- Raw OBD-II metrics JSON (speed, rpm, throttle, coolantTemp, engineLoad)
    metrics_json TEXT,

    FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_behavior_driver_time
    ON driver_behavior_history(driver_id, timestamp);

-- Extend drivers table with persistent behavior summary columns
-- (safe to run on existing DB — IF NOT EXISTS is handled by ALTER OR IGNORE pattern)
ALTER TABLE drivers ADD COLUMN safety_score       INTEGER DEFAULT 100;
ALTER TABLE drivers ADD COLUMN behavior_events_json TEXT;   -- serialised event counters
ALTER TABLE drivers ADD COLUMN behavior_metrics_json TEXT;  -- serialised liveMetrics
ALTER TABLE drivers ADD COLUMN last_behavior_event TEXT;
ALTER TABLE drivers ADD COLUMN last_behavior_time  DATETIME;

-- ============================================
-- INITIAL DATA
-- ============================================

-- Insert default settings
INSERT OR IGNORE INTO settings (key, value, value_type, description) VALUES
('mqtt_broker', 'wss://broker.hivemq.com:8884/mqtt', 'string', 'MQTT broker URL'),
('mqtt_topic', 'monztrack/device01/gps', 'string', 'Default MQTT topic'),
('mqtt_topic_telemetry', 'sgu/vehicle001/telemetry', 'string', 'ESP32 OBD-II telemetry topic'),
('mqtt_topic_behavior', 'sgu/vehicle001/behavior', 'string', 'ESP32 behavior events topic'),
('mqtt_topic_alerts', 'sgu/vehicle001/alerts', 'string', 'ESP32 behavior alerts topic'),
('map_default_lat', '-6.2088', 'number', 'Default map center latitude (Jakarta, Indonesia)'),
('map_default_lng', '106.8456', 'number', 'Default map center longitude (Jakarta, Indonesia)'),
('map_default_zoom', '15', 'number', 'Default map zoom level'),
('geofence_alert_enabled', 'true', 'boolean', 'Enable geofence breach alerts'),
('fuel_theft_alert_enabled', 'true', 'boolean', 'Enable fuel theft alerts'),
('security_alert_enabled', 'true', 'boolean', 'Enable security breach alerts'),
('behavior_alerts_enabled', 'true', 'boolean', 'Enable driver behavior Telegram alerts'),
('behavior_alerts_min_severity', 'WARNING', 'string', 'Minimum severity for behavior Telegram alerts (INFO|WARNING|CRITICAL)'),
('telemetry_retention_days', '90', 'number', 'Days to keep telemetry data'),
('events_retention_days', '365', 'number', 'Days to keep event logs'),
('behavior_history_retention_days', '180', 'number', 'Days to keep driver behavior history');
