/**
 * SGU Logistics Database Module
 * Made by Monzer · github.com/moonr5/Vision
 *
 * Uses sql.js (SQLite compiled to WebAssembly) for in-browser relational database.
 * This provides persistent storage via IndexedDB-backed files or exportable blobs.
 * 
 * @module database/db
 */

// Database configuration
const DB_CONFIG = {
    name: 'sgu_logistics_db',
    version: 1,
    exportInterval: 30000, // Auto-save every 30 seconds
};

// Global database instance
let db = null;
let isInitialized = false;
let initCallbacks = [];

/**
 * Initialize the database
 * Loads sql.js, creates/opens the database, and sets up the schema
 * @returns {Promise<SQL.Database>} The initialized database instance
 */
async function initDatabase() {
    if (isInitialized && db) {
        return db;
    }

    // Wait for sql.js to be loaded
    if (typeof SQL === 'undefined') {
        await loadSQLJS();
    }

    try {
        // Try to load existing database from IndexedDB
        const savedDb = await loadDatabaseFromStorage();
        
        if (savedDb) {
            db = new SQL.Database(savedDb);
            console.log('[DB] Loaded existing database from storage');
        } else {
            // Create new database
            db = new SQL.Database();
            console.log('[DB] Created new database');
        }

        // Apply schema
        await applySchema();
        
        // Setup auto-save
        setupAutoSave();
        
        isInitialized = true;
        
        // Execute pending callbacks
        initCallbacks.forEach(cb => {
            try { cb(db); } catch (e) { console.error('[DB] Init callback error:', e); }
        });
        initCallbacks = [];
        
        console.log('[DB] Database initialized successfully');
        return db;
        
    } catch (error) {
        console.error('[DB] Initialization error:', error);
        throw error;
    }
}

/**
 * Load sql.js library dynamically
 * @returns {Promise<void>}
 */
function loadSQLJS() {
    return new Promise((resolve, reject) => {
        if (typeof SQL !== 'undefined') {
            resolve();
            return;
        }

        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/sql-wasm.js';
        script.onload = () => {
            // sql.js is loaded, now initialize it
            if (window.initSqlJs) {
                window.initSqlJs({
                    locateFile: file => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/${file}`
                }).then(SQL => {
                    window.SQL = SQL;
                    resolve();
                }).catch(reject);
            } else {
                resolve();
            }
        };
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

/**
 * Apply database schema
 */
async function applySchema() {
    const schemaSQL = `
        -- Enable foreign keys
        PRAGMA foreign_keys = ON;

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
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Devices table
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT,
            mqtt_topic TEXT,
            device_type TEXT DEFAULT 'gps_tracker',
            status TEXT DEFAULT 'offline',
            last_seen DATETIME,
            assigned_driver_id TEXT,
            assigned_vehicle_id TEXT,
            settings TEXT,
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
            country TEXT DEFAULT 'UA',
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Orders table
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            order_id TEXT UNIQUE NOT NULL,
            type TEXT DEFAULT 'domestic',
            status TEXT DEFAULT 'pickup',
            origin_city TEXT,
            origin_address TEXT,
            origin_lat REAL,
            origin_lng REAL,
            destination_city TEXT,
            destination_address TEXT,
            destination_lat REAL,
            destination_lng REAL,
            customer_id TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            driver_id TEXT,
            vehicle_id TEXT,
            device_id TEXT,
            pickup_date DATE,
            delivery_date DATE,
            actual_pickup_at DATETIME,
            actual_delivery_at DATETIME,
            current_lat REAL,
            current_lng REAL,
            current_speed REAL,
            last_update DATETIME,
            cargo_description TEXT,
            cargo_weight REAL,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL,
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL
        );

        -- Telemetry table
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            order_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            received_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            lat REAL,
            lng REAL,
            altitude REAL,
            speed REAL,
            heading REAL,
            accuracy REAL,
            satellites INTEGER,
            gps_fix INTEGER,
            sensor_s1 INTEGER,
            sensor_s2 INTEGER,
            sensor_mag1 INTEGER,
            sensor_mag2 INTEGER,
            obd_rpm INTEGER,
            obd_speed REAL,
            obd_engine_load REAL,
            obd_coolant_temp REAL,
            obd_throttle REAL,
            obd_mil BOOLEAN,
            obd_vin TEXT,
            fuel_level REAL,
            fuel_flow_in REAL,
            fuel_flow_out REAL,
            fuel_cap_open BOOLEAN,
            fuel_theft_detected BOOLEAN,
            distance_km REAL,
            geofence_active BOOLEAN,
            geofence_inside BOOLEAN,
            raw_payload TEXT,
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
        );

        -- Events table
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            category TEXT,
            event TEXT NOT NULL,
            device_id TEXT,
            order_id TEXT,
            driver_id TEXT,
            lat REAL,
            lng REAL,
            location_text TEXT,
            details TEXT,
            speed REAL,
            sensor_s1 INTEGER,
            sensor_s2 INTEGER,
            sensor_mag1 INTEGER,
            sensor_mag2 INTEGER,
            gps_has_fix BOOLEAN,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            event_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            acknowledged BOOLEAN DEFAULT FALSE,
            acknowledged_by TEXT,
            acknowledged_at DATETIME,
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
        );

        -- Geofences table
        CREATE TABLE IF NOT EXISTS geofences (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            geometry_type TEXT DEFAULT 'circle',
            center_lat REAL,
            center_lng REAL,
            radius_meters REAL,
            coordinates TEXT,
            assigned_device_id TEXT,
            assigned_order_id TEXT,
            alert_on_enter BOOLEAN DEFAULT TRUE,
            alert_on_exit BOOLEAN DEFAULT TRUE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (assigned_device_id) REFERENCES devices(id) ON DELETE SET NULL,
            FOREIGN KEY (assigned_order_id) REFERENCES orders(id) ON DELETE SET NULL
        );

        -- Geofence events table
        CREATE TABLE IF NOT EXISTS geofence_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            geofence_id TEXT NOT NULL,
            device_id TEXT,
            order_id TEXT,
            event_type TEXT,
            lat REAL,
            lng REAL,
            triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (geofence_id) REFERENCES geofences(id) ON DELETE CASCADE,
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
        );

        -- Trips table
        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            order_id TEXT,
            driver_id TEXT,
            started_at DATETIME,
            ended_at DATETIME,
            duration_seconds INTEGER,
            start_lat REAL,
            start_lng REAL,
            end_lat REAL,
            end_lng REAL,
            total_distance_km REAL,
            max_speed REAL,
            avg_speed REAL,
            fuel_consumed REAL,
            fuel_efficiency REAL,
            events_critical INTEGER DEFAULT 0,
            events_warning INTEGER DEFAULT 0,
            events_info INTEGER DEFAULT 0,
            status TEXT DEFAULT 'in_progress',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL,
            FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE SET NULL
        );

        -- Settings table
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            value_type TEXT DEFAULT 'string',
            description TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- API usage table
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT,
            action TEXT,
            request_payload TEXT,
            response_status TEXT,
            meta TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Create indexes
        CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_telemetry_order ON telemetry(order_id);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
        CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);

        -- Insert default settings
        INSERT OR IGNORE INTO settings (key, value, value_type, description) VALUES
        ('mqtt_broker', 'wss://broker.hivemq.com:8884/mqtt', 'string', 'MQTT broker URL'),
        ('mqtt_topic', 'monztrack/device01/gps', 'string', 'Default MQTT topic'),
        ('map_default_lat', '-6.2252', 'number', 'Default map center latitude'),
        ('map_default_lng', '106.6552', 'number', 'Default map center longitude'),
        ('map_default_zoom', '15', 'number', 'Default map zoom level'),
        ('geofence_alert_enabled', 'true', 'boolean', 'Enable geofence breach alerts'),
        ('fuel_theft_alert_enabled', 'true', 'boolean', 'Enable fuel theft alerts'),
        ('security_alert_enabled', 'true', 'boolean', 'Enable security breach alerts'),
        ('telemetry_retention_days', '90', 'number', 'Days to keep telemetry data'),
        ('events_retention_days', '365', 'number', 'Days to keep event logs');
    `;

    // Execute schema SQL
    db.run(schemaSQL);
    console.log('[DB] Schema applied');
}

/**
 * Setup auto-save to IndexedDB
 */
function setupAutoSave() {
    // Auto-save on interval
    setInterval(() => {
        saveDatabaseToStorage();
    }, DB_CONFIG.exportInterval);

    // Save on page unload
    window.addEventListener('beforeunload', () => {
        saveDatabaseToStorage();
    });
}

/**
 * Save database to IndexedDB storage
 */
async function saveDatabaseToStorage() {
    if (!db) return;
    
    try {
        const data = db.export();
        const blob = new Blob([data]);
        
        // Use localforage or IndexedDB directly
        await saveToIndexedDB(DB_CONFIG.name, blob);
        console.log('[DB] Database saved to storage');
    } catch (error) {
        console.error('[DB] Save error:', error);
    }
}

/**
 * Load database from IndexedDB storage
 * @returns {Promise<Uint8Array|null>}
 */
async function loadDatabaseFromStorage() {
    try {
        const blob = await loadFromIndexedDB(DB_CONFIG.name);
        if (!blob) return null;
        
        const arrayBuffer = await blob.arrayBuffer();
        return new Uint8Array(arrayBuffer);
    } catch (error) {
        console.error('[DB] Load error:', error);
        return null;
    }
}

/**
 * Save to IndexedDB
 */
function saveToIndexedDB(key, blob) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_CONFIG.name, DB_CONFIG.version);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const db = request.result;
            const tx = db.transaction(['database'], 'readwrite');
            const store = tx.objectStore('database');
            store.put(blob, key);
            tx.oncomplete = () => {
                db.close();
                resolve();
            };
            tx.onerror = () => reject(tx.error);
        };
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('database')) {
                db.createObjectStore('database');
            }
        };
    });
}

/**
 * Load from IndexedDB
 */
function loadFromIndexedDB(key) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_CONFIG.name, DB_CONFIG.version);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const db = request.result;
            
            if (!db.objectStoreNames.contains('database')) {
                db.close();
                resolve(null);
                return;
            }
            
            const tx = db.transaction(['database'], 'readonly');
            const store = tx.objectStore('database');
            const getRequest = store.get(key);
            
            getRequest.onsuccess = () => {
                db.close();
                resolve(getRequest.result);
            };
            getRequest.onerror = () => {
                db.close();
                reject(getRequest.error);
            };
        };
        
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('database')) {
                db.createObjectStore('database');
            }
        };
    });
}

/**
 * Get database instance (waits for initialization)
 * @returns {Promise<SQL.Database>}
 */
async function getDatabase() {
    if (isInitialized && db) {
        return db;
    }
    return initDatabase();
}

/**
 * Execute a SQL query
 * @param {string} sql - SQL statement
 * @param {Array} params - Query parameters
 * @returns {Array} Query results
 */
function query(sql, params = []) {
    if (!db) {
        throw new Error('Database not initialized');
    }
    
    const stmt = db.prepare(sql);
    const results = [];
    
    while (stmt.step()) {
        results.push(stmt.getAsObject());
    }
    
    stmt.free();
    return results;
}

/**
 * Execute a SQL statement (INSERT, UPDATE, DELETE)
 * @param {string} sql - SQL statement
 * @param {Array} params - Statement parameters
 * @returns {Object} Execution result
 */
function execute(sql, params = []) {
    if (!db) {
        throw new Error('Database not initialized');
    }
    
    db.run(sql, params);
    return {
        changes: db.getRowsModified(),
        lastInsertRowid: db.exec('SELECT last_insert_rowid()')[0]?.values[0]?.[0]
    };
}

/**
 * Run multiple statements in a transaction
 * @param {Function} callback - Function that receives db and performs operations
 */
function transaction(callback) {
    if (!db) {
        throw new Error('Database not initialized');
    }
    
    db.run('BEGIN TRANSACTION');
    try {
        callback(db);
        db.run('COMMIT');
    } catch (error) {
        db.run('ROLLBACK');
        throw error;
    }
}

/**
 * Export database as downloadable file
 * @returns {Blob} Database as SQLite blob
 */
function exportDatabase() {
    if (!db) {
        throw new Error('Database not initialized');
    }
    
    const data = db.export();
    return new Blob([data], { type: 'application/x-sqlite3' });
}

/**
 * Import database from file
 * @param {File|Blob} file - SQLite database file
 * @returns {Promise<void>}
 */
async function importDatabase(file) {
    const arrayBuffer = await file.arrayBuffer();
    const uint8Array = new Uint8Array(arrayBuffer);
    
    // Close existing database
    if (db) {
        db.close();
    }
    
    // Create new database from file
    db = new SQL.Database(uint8Array);
    isInitialized = true;
    
    // Save to storage
    await saveDatabaseToStorage();
    
    console.log('[DB] Database imported successfully');
}

/**
 * Clear all data (keeps schema)
 */
function clearAllData() {
    if (!db) {
        throw new Error('Database not initialized');
    }
    
    const tables = [
        'telemetry', 'events', 'geofence_events', 'trips', 
        'geofences', 'orders', 'customers', 'devices', 'drivers',
        'api_usage'
    ];
    
    tables.forEach(table => {
        try {
            db.run(`DELETE FROM ${table}`);
        } catch (e) {
            console.warn(`[DB] Could not clear table ${table}:`, e.message);
        }
    });
    
    console.log('[DB] All data cleared');
}

/**
 * Get database statistics
 * @returns {Object} Statistics
 */
function getStatistics() {
    if (!db) {
        return null;
    }
    
    const stats = {};
    const tables = ['orders', 'telemetry', 'events', 'devices', 'drivers', 'customers'];
    
    tables.forEach(table => {
        try {
            const result = query(`SELECT COUNT(*) as count FROM ${table}`);
            stats[table] = result[0]?.count || 0;
        } catch (e) {
            stats[table] = 0;
        }
    });
    
    return stats;
}

// ============================================
// PUBLIC API
// ============================================

window.SGUDatabase = {
    // Initialization
    init: initDatabase,
    getDB: getDatabase,
    isReady: () => isInitialized,
    onReady: (callback) => {
        if (isInitialized) {
            callback(db);
        } else {
            initCallbacks.push(callback);
        }
    },
    
    // Query execution
    query,
    execute,
    transaction,
    
    // Import/Export
    export: exportDatabase,
    import: importDatabase,
    save: saveDatabaseToStorage,
    
    // Maintenance
    clear: clearAllData,
    stats: getStatistics,
    
    // Raw access (use with caution)
    get raw() { return db; }
};

console.log('[DB] Database module loaded');
