# SGU Logistics Database

> **Last updated: 2026-04-08**

A complete SQLite database solution for the SGU Logistics & Telemetry Dashboard, providing persistent storage for orders, telemetry data, events, and system configuration.

## Overview

This database module provides:

- **SQLite Database** running in the browser via sql.js (WebAssembly)
- **Persistent Storage** using IndexedDB
- **Full CRUD Operations** for all entities
- **Migration Support** from existing localStorage data
- **Backward Compatibility** with existing application code
- **Non-Breaking Integration** — existing logic continues to work
- **Database-First AI** — answers from local SQLite before calling Gemini (saves ~85% API costs)

## Architecture

```
database/
├── schema.sql        -- Database schema definition
├── db.js             -- Core database initialization (sql.js)
├── dal.js            -- Data Access Layer (CRUD operations)
├── ai-engine.js      -- Smart AI query engine (database-first answers)
├── migrate.js        -- Data migration from localStorage
├── service.js        -- High-level service API
├── integration.js    -- Application integration patches
├── loader.js         -- Module loader (loads all modules above; include only this in HTML)
└── README.md         -- This file
```

> **Note:** `loader.js` automatically loads `ai-engine.js` and all other modules in the correct order.
> Add only `<script src="database/loader.js"></script>` to your HTML — do not add the other scripts separately.

## Double Response AI System

Every AI reply in the chat contains two visually distinct parts shown together as one message:

**Part 1 — 📊 From your data**
- Instant answer pulled from local SQLite (free, no API call)
- Specific numbers and facts from the current session

**Part 2 — 💡 Monzer's insight**
- Gemini-powered interpretation and recommendation
- Warm, conversational, actionable advice

If the database has no data for the question, Part 1 gives an honest acknowledgment and Part 2 provides Gemini's best general advice. Both parts are always present — neither is skipped.

### AI Conversation Memory

The `AI_MEMORY` object tracks the last 5 exchanges in-session:
- Detects follow-up questions automatically (short messages, "why?", "and?", etc.)
- Routes follow-ups to the same topic without re-asking for context
- Proactive suggestion chips appear 8 seconds after any AI response

## Quick Start

### 1. Include the Database

Add this single line to your `index.html` before the closing `</body>` tag:

```html
<script src="database/loader.js"></script>
```

### 2. The Database Auto-Initializes

The database will automatically:
- Load sql.js from CDN
- Create/open the SQLite database
- Apply the schema
- Migrate existing localStorage data
- Patch existing functions for data persistence

### 3. Access the Database

After initialization (wait for the `sgu-db-ready` event):

```javascript
// Wait for database to be ready
window.addEventListener('sgu-db-ready', (e) => {
    console.log('Database ready!', e.detail);
});

// Or check if ready
if (window.SGUService?.isReady()) {
    // Use the database
}
```

## API Reference

### Core Database (`SGUDatabase`)

```javascript
// Query
const results = SGUDatabase.query('SELECT * FROM orders WHERE status = ?', ['transit']);

// Execute
SGUDatabase.execute('INSERT INTO orders (id, order_id) VALUES (?, ?)', [id, orderId]);

// Transaction
SGUDatabase.transaction((db) => {
    db.run('INSERT INTO ...');
    db.run('UPDATE ...');
});

// Export/Import
const blob = SGUDatabase.export(); // Download as .sqlite3 file
await SGUDatabase.import(file);    // Import from file

// Save to storage
await SGUDatabase.save();

// Statistics
const stats = SGUDatabase.stats(); // { orders: 10, telemetry: 5000, ... }
```

### Data Access Layer (`SGUDAL`)

#### Orders
```javascript
// Get all orders
const orders = SGUDAL.Orders.getAll({ status: 'transit', limit: 10 });

// Get by ID
const order = SGUDAL.Orders.getById('ord_123');

// Create
const newOrder = SGUDAL.Orders.create({
    order_id: 'ORD-001',
    type: 'domestic',
    status: 'pickup',
    origin_city: 'Kyiv',
    destination_city: 'Lviv',
    customer_name: 'John Doe'
});

// Update
SGUDAL.Orders.update('ord_123', { status: 'transit' });

// Delete
SGUDAL.Orders.delete('ord_123');
```

#### Telemetry
```javascript
// Store telemetry snapshot
SGUDAL.Telemetry.save({
    device_id: 'device-01',
    lat: 50.4501,
    lng: 30.5234,
    speed: 60,
    sensor_s1: 1,
    sensor_s2: 0
});

// Get device history
const history = SGUDAL.Telemetry.getByDevice('device-01', { 
    since: '2024-01-01T00:00:00Z',
    limit: 100 
});

// Get latest
const latest = SGUDAL.Telemetry.getLatest('device-01');

// Get route for map drawing
const route = SGUDAL.Telemetry.getRoute('device-01', 24); // Last 24 hours
```

#### Events
```javascript
// Log event
SGUDAL.Events.log({
    type: 'WARNING',
    category: 'SECURITY',
    event: 'GEOFENCE BREAK',
    details: 'Vehicle exited allowed zone',
    device_id: 'device-01',
    lat: 50.45,
    lng: 30.52
});

// Get recent events
const events = SGUDAL.Events.getRecent(50);

// Get unacknowledged alerts
const alerts = SGUDAL.Events.getAlerts();

// Acknowledge
SGUDAL.Events.acknowledge(123, 'admin');
```

#### Settings
```javascript
// Get setting
const broker = SGUDAL.Settings.get('mqtt_broker', 'wss://default');

// Set setting
SGUDAL.Settings.set('mqtt_broker', 'wss://broker.hivemq.com:8884/mqtt');
SGUDAL.Settings.set('max_speed_limit', 120, 'number');
SGUDAL.Settings.set('notifications', true, 'boolean');
```

### Service Layer (`SGUService`)

Higher-level API that integrates with the existing application:

```javascript
// Orders (syncs with existing window.orders array)
SGUService.Orders.getAll();
SGUService.Orders.create(orderData);
SGUService.Orders.update(id, updates);
SGUService.Orders.delete(id);

// Telemetry (stores from MQTT messages)
SGUService.Telemetry.store(deviceId, payload);
SGUService.Telemetry.getHistory(deviceId, { hours: 24 });
SGUService.Telemetry.getRoute(deviceId, 24);

// Events (compatible with existing logReport)
SGUService.Events.log(type, event, details, speed, extra);
SGUService.Events.getRecent(100);
SGUService.Events.clear();

// Settings (syncs with localStorage)
SGUService.Settings.get(key, defaultValue);
SGUService.Settings.set(key, value, type);
```

## Database Schema

### Tables

#### `orders` - Shipment orders
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Unique order ID |
| order_id | TEXT | Human-readable order number |
| type | TEXT | domestic, transit, international |
| status | TEXT | pickup, transit, delivery, completed, cancelled |
| origin_city | TEXT | Origin city |
| origin_address | TEXT | Origin street address |
| destination_city | TEXT | Destination city |
| destination_address | TEXT | Destination street address |
| customer_name | TEXT | Customer name |
| customer_phone | TEXT | Customer phone |
| driver_id | TEXT FK | Assigned driver |
| device_id | TEXT FK | Tracking device |
| pickup_date | DATE | Scheduled pickup |
| delivery_date | DATE | Scheduled delivery |
| current_lat/lng | REAL | Current location |
| created_at | DATETIME | Creation timestamp |

#### `telemetry` - IoT device data
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| device_id | TEXT FK | Device identifier |
| timestamp | DATETIME | Data timestamp |
| lat/lng | REAL | GPS coordinates |
| speed | REAL | Speed km/h |
| sensor_s1/s2 | INTEGER | Limit switches (1=closed, 0=open) |
| sensor_mag1/mag2 | INTEGER | Magnetic sensors |
| obd_rpm/load/temp | REAL | OBD-II data |
| fuel_level | REAL | Fuel percentage |
| fuel_theft_detected | BOOLEAN | Theft alert |
| raw_payload | TEXT | Original JSON |

#### `events` - System events & alerts
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| type | TEXT | INFO, WARNING, CRITICAL |
| category | TEXT | GEOFENCE, FUEL, SECURITY, etc. |
| event | TEXT | Event name |
| device_id | TEXT FK | Related device |
| order_id | TEXT FK | Related order |
| lat/lng | REAL | Location when event occurred |
| details | TEXT | Event description |
| acknowledged | BOOLEAN | Alert acknowledged |
| created_at | DATETIME | Event timestamp |

#### `devices` - IoT tracking devices
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Device ID |
| name | TEXT | Display name |
| mqtt_topic | TEXT | MQTT subscription topic |
| status | TEXT | online, offline, maintenance |
| last_seen | DATETIME | Last data received |
| assigned_driver_id | TEXT FK | Assigned driver |

#### `customers` - Customer directory
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Customer ID |
| name | TEXT | Full name |
| phone | TEXT | Phone number |
| email | TEXT | Email address |
| company | TEXT | Company name |
| address/city | TEXT | Address info |

#### `geofences` - Geographic zones
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Geofence ID |
| name | TEXT | Display name |
| geometry_type | TEXT | circle, polygon |
| center_lat/lng | REAL | Circle center |
| radius_meters | REAL | Circle radius |
| coordinates | TEXT | JSON polygon coordinates |
| is_active | BOOLEAN | Enabled/disabled |

#### `settings` - Application configuration
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PK | Setting name |
| value | TEXT | Setting value |
| value_type | TEXT | string, number, boolean, json |

## Migration

Data is automatically migrated from localStorage on first load:

- `sensor_reports` → `events` table
- `sgu_orders` → `orders` table
- `sgu_settings_v1` → `settings` table

Check migration status:
```javascript
const status = SGUMigration.getStatus();
console.log(status);
// { version: 1, isMigrated: true, isNeeded: false, ... }
```

Reset migration (for testing):
```javascript
SGUMigration.reset();
location.reload();
```

## Backup & Export

### Export Database
```javascript
// Get database as blob
const blob = SGUDatabase.export();

// Download as file
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = `sgu_backup_${new Date().toISOString().slice(0,10)}.sqlite3`;
a.click();
```

### Import Database
```javascript
const fileInput = document.getElementById('file-input');
const file = fileInput.files[0];
await SGUDatabase.import(file);
```

## Data Retention

Automatic cleanup of old data:

```javascript
// Default: 90 days for telemetry, 365 days for events
SGUDAL.Telemetry.cleanup(90);  // Remove telemetry older than 90 days
SGUDAL.Events.cleanup(365);    // Remove events older than 365 days
```

Configure retention in settings:
```javascript
SGUDAL.Settings.set('telemetry_retention_days', 90, 'number');
SGUDAL.Settings.set('events_retention_days', 365, 'number');
```

## Troubleshooting

### Database not loading
- Check browser console for errors
- Ensure you have a stable internet connection (sql.js loads from CDN)
- Check if IndexedDB is enabled in browser

### Data not persisting
- Database auto-saves every 30 seconds
- Force save: `await SGUDatabase.save()`
- Check browser storage quotas

### Migration issues
- Check `SGUMigration.getStatus()` for status
- Reset and re-run: `SGUMigration.reset()` then reload

### Clear all data
```javascript
// Clear database tables
SGUDatabase.clear();

// Clear localStorage
localStorage.clear();
```

## Browser Compatibility

- Chrome/Edge 80+
- Firefox 75+
- Safari 14+

Requires:
- WebAssembly support
- IndexedDB support
- ES6+ JavaScript

## License

Part of the SGU Logistics Dashboard.
