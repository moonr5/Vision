-- SGU Logistics PostgreSQL Schema
-- Idempotent: safe to run on every deploy
-- Made by Monzer · github.com/moonr5/Vision

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
    avatar TEXT,
    status TEXT DEFAULT 'active',
    safety_score INTEGER DEFAULT 100,
    behavior_events_json TEXT,
    behavior_metrics_json TEXT,
    last_behavior_event TEXT,
    last_behavior_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT,
    mqtt_topic TEXT,
    device_type TEXT DEFAULT 'gps_tracker',
    status TEXT DEFAULT 'offline',
    last_seen TIMESTAMP,
    assigned_driver_id TEXT REFERENCES drivers(id) ON DELETE SET NULL,
    assigned_vehicle_id TEXT,
    settings TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    order_id TEXT UNIQUE NOT NULL,
    type TEXT DEFAULT 'domestic',
    status TEXT DEFAULT 'pickup',
    origin_city TEXT,
    origin_address TEXT,
    origin_lat DOUBLE PRECISION,
    origin_lng DOUBLE PRECISION,
    destination_city TEXT,
    destination_address TEXT,
    destination_lat DOUBLE PRECISION,
    destination_lng DOUBLE PRECISION,
    customer_id TEXT REFERENCES customers(id) ON DELETE SET NULL,
    customer_name TEXT,
    customer_phone TEXT,
    driver_id TEXT REFERENCES drivers(id) ON DELETE SET NULL,
    vehicle_id TEXT,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    pickup_date DATE,
    delivery_date DATE,
    actual_pickup_at TIMESTAMP,
    actual_delivery_at TIMESTAMP,
    current_lat DOUBLE PRECISION,
    current_lng DOUBLE PRECISION,
    current_speed DOUBLE PRECISION,
    last_update TIMESTAMP,
    cargo_description TEXT,
    cargo_weight DOUBLE PRECISION,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telemetry (
    id SERIAL PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    altitude DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    satellites INTEGER,
    gps_fix INTEGER,
    sensor_s1 INTEGER,
    sensor_s2 INTEGER,
    sensor_mag1 INTEGER,
    sensor_mag2 INTEGER,
    obd_rpm INTEGER,
    obd_speed DOUBLE PRECISION,
    obd_engine_load DOUBLE PRECISION,
    obd_coolant_temp DOUBLE PRECISION,
    obd_throttle DOUBLE PRECISION,
    obd_mil BOOLEAN,
    obd_vin TEXT,
    fuel_level DOUBLE PRECISION,
    fuel_flow_in DOUBLE PRECISION,
    fuel_flow_out DOUBLE PRECISION,
    fuel_cap_open BOOLEAN,
    fuel_theft_detected BOOLEAN,
    distance_km DOUBLE PRECISION,
    geofence_active BOOLEAN,
    geofence_inside BOOLEAN,
    raw_payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_order ON telemetry(order_id);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    category TEXT,
    event TEXT NOT NULL,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    driver_id TEXT REFERENCES drivers(id) ON DELETE SET NULL,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    location_text TEXT,
    details TEXT,
    speed DOUBLE PRECISION,
    sensor_s1 INTEGER,
    sensor_s2 INTEGER,
    sensor_mag1 INTEGER,
    sensor_mag2 INTEGER,
    gps_has_fix BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,
    acknowledged_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);

CREATE TABLE IF NOT EXISTS geofences (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    geometry_type TEXT DEFAULT 'circle',
    center_lat DOUBLE PRECISION,
    center_lng DOUBLE PRECISION,
    radius_meters DOUBLE PRECISION,
    coordinates TEXT,
    assigned_device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    assigned_order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    alert_on_enter BOOLEAN DEFAULT TRUE,
    alert_on_exit BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS geofence_events (
    id SERIAL PRIMARY KEY,
    geofence_id TEXT NOT NULL REFERENCES geofences(id) ON DELETE CASCADE,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    event_type TEXT,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    order_id TEXT REFERENCES orders(id) ON DELETE SET NULL,
    driver_id TEXT REFERENCES drivers(id) ON DELETE SET NULL,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER,
    start_lat DOUBLE PRECISION,
    start_lng DOUBLE PRECISION,
    end_lat DOUBLE PRECISION,
    end_lng DOUBLE PRECISION,
    total_distance_km DOUBLE PRECISION,
    max_speed DOUBLE PRECISION,
    avg_speed DOUBLE PRECISION,
    fuel_consumed DOUBLE PRECISION,
    fuel_efficiency DOUBLE PRECISION,
    events_critical INTEGER DEFAULT 0,
    events_warning INTEGER DEFAULT 0,
    events_info INTEGER DEFAULT 0,
    status TEXT DEFAULT 'in_progress',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    value_type TEXT DEFAULT 'string',
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_usage (
    id SERIAL PRIMARY KEY,
    api_name TEXT,
    action TEXT,
    request_payload TEXT,
    response_status TEXT,
    meta TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS driver_behavior_history (
    id SERIAL PRIMARY KEY,
    driver_id TEXT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    safety_score INTEGER,
    event_name TEXT,
    event_type TEXT,
    event_details TEXT,
    metrics_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_behavior_driver_time ON driver_behavior_history(driver_id, timestamp);

-- Views
CREATE OR REPLACE VIEW v_active_orders AS
SELECT
    o.*,
    d.name AS driver_name,
    d.phone AS driver_phone,
    d.vehicle_plate,
    dev.status AS device_status,
    dev.last_seen AS device_last_seen
FROM orders o
LEFT JOIN drivers d ON o.driver_id = d.id
LEFT JOIN devices dev ON o.device_id = dev.id
WHERE o.status NOT IN ('completed', 'cancelled');

CREATE OR REPLACE VIEW v_device_latest_telemetry AS
SELECT t.*
FROM telemetry t
INNER JOIN (
    SELECT device_id, MAX(timestamp) AS max_ts
    FROM telemetry
    GROUP BY device_id
) latest ON t.device_id = latest.device_id AND t.timestamp = latest.max_ts;

CREATE OR REPLACE VIEW v_event_summary_24h AS
SELECT type, category, COUNT(*) AS count
FROM events
WHERE created_at >= NOW() - INTERVAL '1 day'
GROUP BY type, category;

CREATE OR REPLACE VIEW v_order_stats AS
SELECT status, type, COUNT(*) AS count, MIN(created_at) AS oldest_order, MAX(updated_at) AS latest_update
FROM orders
GROUP BY status, type;

-- Default settings (no-op if already present)
INSERT INTO settings (key, value, value_type, description) VALUES
('app_creator',                    'Made by Monzer · github.com/moonr5/Vision', 'string',  'Attribution — do not remove'),
('mqtt_broker',                    'wss://broker.hivemq.com:8884/mqtt', 'string',  'MQTT broker URL'),
('mqtt_topic',                     'monztrack/device01/gps',            'string',  'Default MQTT topic'),
('mqtt_topic_telemetry',           'sgu/vehicle001/telemetry',          'string',  'ESP32 OBD-II telemetry topic'),
('mqtt_topic_behavior',            'sgu/vehicle001/behavior',           'string',  'ESP32 behavior events topic'),
('mqtt_topic_alerts',              'sgu/vehicle001/alerts',             'string',  'ESP32 behavior alerts topic'),
('map_default_lat',                '-6.2088',                           'number',  'Default map center latitude'),
('map_default_lng',                '106.8456',                          'number',  'Default map center longitude'),
('map_default_zoom',               '15',                                'number',  'Default map zoom level'),
('geofence_alert_enabled',         'true',                              'boolean', 'Enable geofence breach alerts'),
('fuel_theft_alert_enabled',       'true',                              'boolean', 'Enable fuel theft alerts'),
('security_alert_enabled',         'true',                              'boolean', 'Enable security breach alerts'),
('behavior_alerts_enabled',        'true',                              'boolean', 'Enable driver behavior alerts'),
('behavior_alerts_min_severity',   'WARNING',                           'string',  'Minimum severity for behavior alerts'),
('telemetry_retention_days',       '90',                                'number',  'Days to keep telemetry data'),
('events_retention_days',          '365',                               'number',  'Days to keep event logs'),
('behavior_history_retention_days','180',                               'number',  'Days to keep driver behavior history')
ON CONFLICT (key) DO NOTHING;
