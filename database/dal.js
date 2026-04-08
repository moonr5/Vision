/**
 * SGU Logistics Data Access Layer (DAL)
 * 
 * Provides convenient CRUD operations for all database entities.
 * Each entity has its own namespace with standardized methods.
 * 
 * @module database/dal
 */

(function() {
    'use strict';

    // ============================================
    // HELPER FUNCTIONS
    // ============================================

    /**
     * Generate a unique ID
     */
    function generateId(prefix = '') {
        return prefix + Date.now().toString(36) + Math.random().toString(36).substr(2, 9);
    }

    /**
     * Get current timestamp in ISO format
     */
    function now() {
        return new Date().toISOString();
    }

    /**
     * Execute query with error handling
     */
    function safeQuery(sql, params = []) {
        try {
            return window.SGUDatabase.query(sql, params);
        } catch (error) {
            console.error('[DAL] Query error:', error, { sql, params });
            throw error;
        }
    }

    /**
     * Execute statement with error handling
     */
    function safeExecute(sql, params = []) {
        try {
            return window.SGUDatabase.execute(sql, params);
        } catch (error) {
            console.error('[DAL] Execute error:', error, { sql, params });
            throw error;
        }
    }

    /**
     * Convert object to SQL INSERT/UPDATE fields
     */
    function objectToFields(obj, exclude = []) {
        const keys = Object.keys(obj).filter(k => !exclude.includes(k) && obj[k] !== undefined);
        const values = keys.map(k => obj[k]);
        return { keys, values };
    }

    // ============================================
    // ORDERS DAL
    // ============================================

    const Orders = {
        /**
         * Get all orders with optional filtering
         */
        getAll(filters = {}) {
            let sql = 'SELECT * FROM orders WHERE 1=1';
            const params = [];

            if (filters.status) {
                sql += ' AND status = ?';
                params.push(filters.status);
            }
            if (filters.type) {
                sql += ' AND type = ?';
                params.push(filters.type);
            }
            if (filters.device_id) {
                sql += ' AND device_id = ?';
                params.push(filters.device_id);
            }
            if (filters.driver_id) {
                sql += ' AND driver_id = ?';
                params.push(filters.driver_id);
            }
            if (filters.search) {
                sql += ` AND (
                    order_id LIKE ? OR 
                    customer_name LIKE ? OR 
                    origin_city LIKE ? OR 
                    destination_city LIKE ?
                )`;
                const searchTerm = `%${filters.search}%`;
                params.push(searchTerm, searchTerm, searchTerm, searchTerm);
            }

            sql += ' ORDER BY created_at DESC';

            if (filters.limit) {
                sql += ' LIMIT ?';
                params.push(filters.limit);
            }

            return safeQuery(sql, params);
        },

        /**
         * Get active orders (not completed or cancelled)
         */
        getActive() {
            return safeQuery(
                "SELECT * FROM orders WHERE status NOT IN ('completed', 'cancelled') ORDER BY created_at DESC"
            );
        },

        /**
         * Get a single order by ID
         */
        getById(id) {
            const results = safeQuery('SELECT * FROM orders WHERE id = ?', [id]);
            return results[0] || null;
        },

        /**
         * Get order by order_id (human-readable ID)
         */
        getByOrderId(orderId) {
            const results = safeQuery('SELECT * FROM orders WHERE order_id = ?', [orderId]);
            return results[0] || null;
        },

        /**
         * Create a new order
         */
        create(data) {
            const id = data.id || generateId('ord_');
            const orderData = {
                id,
                order_id: data.order_id || generateId('ORD-'),
                type: data.type || 'domestic',
                status: data.status || 'pickup',
                origin_city: data.origin_city || '',
                origin_address: data.origin_address || '',
                origin_lat: data.origin_lat || null,
                origin_lng: data.origin_lng || null,
                destination_city: data.destination_city || '',
                destination_address: data.destination_address || '',
                destination_lat: data.destination_lat || null,
                destination_lng: data.destination_lng || null,
                customer_id: data.customer_id || null,
                customer_name: data.customer_name || '',
                customer_phone: data.customer_phone || null,
                driver_id: data.driver_id || null,
                vehicle_id: data.vehicle_id || null,
                device_id: data.device_id || null,
                pickup_date: data.pickup_date || null,
                delivery_date: data.delivery_date || null,
                cargo_description: data.cargo_description || null,
                cargo_weight: data.cargo_weight || null,
                notes: data.notes || null,
                created_at: now(),
                updated_at: now()
            };

            const { keys, values } = objectToFields(orderData);
            const placeholders = keys.map(() => '?').join(',');

            safeExecute(
                `INSERT INTO orders (${keys.join(',')}) VALUES (${placeholders})`,
                values
            );

            return this.getById(id);
        },

        /**
         * Update an existing order
         */
        update(id, data) {
            const updateData = {
                ...data,
                updated_at: now()
            };
            delete updateData.id; // Don't update ID

            const { keys, values } = objectToFields(updateData);
            const setClause = keys.map(k => `${k} = ?`).join(',');

            safeExecute(
                `UPDATE orders SET ${setClause} WHERE id = ?`,
                [...values, id]
            );

            return this.getById(id);
        },

        /**
         * Update order location from telemetry
         */
        updateLocation(id, lat, lng, speed) {
            safeExecute(
                `UPDATE orders SET 
                    current_lat = ?, 
                    current_lng = ?, 
                    current_speed = ?,
                    last_update = ?
                WHERE id = ?`,
                [lat, lng, speed, now(), id]
            );
        },

        /**
         * Delete an order
         */
        delete(id) {
            safeExecute('DELETE FROM orders WHERE id = ?', [id]);
            return { deleted: true, id };
        },

        /**
         * Get order statistics
         */
        getStats() {
            return safeQuery(`
                SELECT 
                    status,
                    COUNT(*) as count
                FROM orders
                GROUP BY status
            `);
        }
    };

    // ============================================
    // TELEMETRY DAL
    // ============================================

    const Telemetry = {
        /**
         * Store telemetry snapshot
         */
        save(data) {
            const telemetryData = {
                device_id: data.device_id,
                order_id: data.order_id || null,
                timestamp: data.timestamp || now(),
                received_at: now(),
                lat: data.lat !== undefined ? data.lat : null,
                lng: data.lng !== undefined ? data.lng : null,
                altitude: data.altitude || null,
                speed: data.speed !== undefined ? data.speed : null,
                heading: data.heading || null,
                accuracy: data.accuracy || null,
                satellites: data.satellites || data.sats || null,
                gps_fix: data.gps_fix !== undefined ? data.gps_fix : (data.loc !== undefined ? data.loc : null),
                sensor_s1: data.sensor_s1 !== undefined ? data.sensor_s1 : (data.s1 !== undefined ? data.s1 : null),
                sensor_s2: data.sensor_s2 !== undefined ? data.sensor_s2 : (data.s2 !== undefined ? data.s2 : null),
                sensor_mag1: data.sensor_mag1 !== undefined ? data.sensor_mag1 : (data.mag1 !== undefined ? data.mag1 : null),
                sensor_mag2: data.sensor_mag2 !== undefined ? data.sensor_mag2 : (data.mag2 !== undefined ? data.mag2 : null),
                obd_rpm: data.obd_rpm !== undefined ? data.obd_rpm : (data.rpm !== undefined ? data.rpm : null),
                obd_speed: data.obd_speed !== undefined ? data.obd_speed : null,
                obd_engine_load: data.obd_engine_load !== undefined ? data.obd_engine_load : (data.engine_load !== undefined ? data.engine_load : null),
                obd_coolant_temp: data.obd_coolant_temp !== undefined ? data.obd_coolant_temp : null,
                obd_throttle: data.obd_throttle !== undefined ? data.obd_throttle : null,
                obd_mil: data.obd_mil !== undefined ? data.obd_mil : null,
                fuel_level: data.fuel_level !== undefined ? data.fuel_level : null,
                fuel_flow_in: data.fuel_flow_in !== undefined ? data.fuel_flow_in : null,
                fuel_flow_out: data.fuel_flow_out !== undefined ? data.fuel_flow_out : null,
                fuel_cap_open: data.fuel_cap_open !== undefined ? data.fuel_cap_open : null,
                fuel_theft_detected: data.fuel_theft_detected !== undefined ? data.fuel_theft_detected : null,
                distance_km: data.distance_km || null,
                geofence_active: data.geofence_active !== undefined ? data.geofence_active : null,
                geofence_inside: data.geofence_inside !== undefined ? data.geofence_inside : null,
                raw_payload: data.raw_payload ? JSON.stringify(data.raw_payload) : null
            };

            const { keys, values } = objectToFields(telemetryData);
            const placeholders = keys.map(() => '?').join(',');

            const result = safeExecute(
                `INSERT INTO telemetry (${keys.join(',')}) VALUES (${placeholders})`,
                values
            );

            return { id: result.lastInsertRowid, ...telemetryData };
        },

        /**
         * Get telemetry for a device
         */
        getByDevice(deviceId, options = {}) {
            let sql = 'SELECT * FROM telemetry WHERE device_id = ?';
            const params = [deviceId];

            if (options.since) {
                sql += ' AND timestamp >= ?';
                params.push(options.since);
            }
            if (options.until) {
                sql += ' AND timestamp <= ?';
                params.push(options.until);
            }

            sql += ' ORDER BY timestamp DESC';

            if (options.limit) {
                sql += ' LIMIT ?';
                params.push(options.limit);
            }

            return safeQuery(sql, params);
        },

        /**
         * Get latest telemetry for a device
         */
        getLatest(deviceId) {
            const results = safeQuery(
                'SELECT * FROM telemetry WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1',
                [deviceId]
            );
            return results[0] || null;
        },

        /**
         * Get telemetry history for route drawing
         */
        getRoute(deviceId, hours = 24) {
            const since = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
            return safeQuery(
                `SELECT lat, lng, speed, timestamp 
                FROM telemetry 
                WHERE device_id = ? AND lat IS NOT NULL AND lng IS NOT NULL
                AND timestamp >= ?
                ORDER BY timestamp ASC`,
                [deviceId, since]
            );
        },

        /**
         * Get aggregated statistics for a device
         */
        getStats(deviceId, hours = 24) {
            const since = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
            const results = safeQuery(
                `SELECT 
                    COUNT(*) as point_count,
                    MAX(speed) as max_speed,
                    AVG(speed) as avg_speed,
                    MIN(timestamp) as first_seen,
                    MAX(timestamp) as last_seen
                FROM telemetry 
                WHERE device_id = ? AND timestamp >= ?`,
                [deviceId, since]
            );
            return results[0];
        },

        /**
         * Clean old telemetry data
         */
        cleanup(retentionDays = 90) {
            const cutoff = new Date(Date.now() - retentionDays * 24 * 60 * 60 * 1000).toISOString();
            const result = safeExecute(
                'DELETE FROM telemetry WHERE timestamp < ?',
                [cutoff]
            );
            return { deleted: result.changes };
        }
    };

    // ============================================
    // EVENTS DAL
    // ============================================

    const Events = {
        /**
         * Log a new event
         */
        log(data) {
            const eventData = {
                type: data.type || 'INFO',
                category: data.category || 'SYSTEM',
                event: data.event,
                device_id: data.device_id || null,
                order_id: data.order_id || null,
                driver_id: data.driver_id || null,
                lat: data.lat !== undefined ? data.lat : null,
                lng: data.lng !== undefined ? data.lng : null,
                location_text: data.location_text || null,
                details: data.details || null,
                speed: data.speed !== undefined ? data.speed : null,
                sensor_s1: data.sensor_s1 !== undefined ? data.sensor_s1 : null,
                sensor_s2: data.sensor_s2 !== undefined ? data.sensor_s2 : null,
                sensor_mag1: data.sensor_mag1 !== undefined ? data.sensor_mag1 : null,
                sensor_mag2: data.sensor_mag2 !== undefined ? data.sensor_mag2 : null,
                gps_has_fix: data.gps_has_fix !== undefined ? data.gps_has_fix : null,
                created_at: now(),
                event_time: data.event_time || now()
            };

            const { keys, values } = objectToFields(eventData);
            const placeholders = keys.map(() => '?').join(',');

            const result = safeExecute(
                `INSERT INTO events (${keys.join(',')}) VALUES (${placeholders})`,
                values
            );

            return { id: result.lastInsertRowid, ...eventData };
        },

        /**
         * Get events with filtering
         */
        getAll(options = {}) {
            let sql = 'SELECT * FROM events WHERE 1=1';
            const params = [];

            if (options.type) {
                sql += ' AND type = ?';
                params.push(options.type);
            }
            if (options.category) {
                sql += ' AND category = ?';
                params.push(options.category);
            }
            if (options.device_id) {
                sql += ' AND device_id = ?';
                params.push(options.device_id);
            }
            if (options.order_id) {
                sql += ' AND order_id = ?';
                params.push(options.order_id);
            }
            if (options.since) {
                sql += ' AND created_at >= ?';
                params.push(options.since);
            }
            if (options.acknowledged !== undefined) {
                sql += ' AND acknowledged = ?';
                params.push(options.acknowledged ? 1 : 0);
            }

            sql += ' ORDER BY created_at DESC';

            if (options.limit) {
                sql += ' LIMIT ?';
                params.push(options.limit);
            }

            return safeQuery(sql, params);
        },

        /**
         * Get recent events
         */
        getRecent(limit = 100) {
            return this.getAll({ limit });
        },

        /**
         * Get unacknowledged alerts
         */
        getAlerts() {
            return this.getAll({ 
                type: 'CRITICAL',
                acknowledged: false,
                limit: 50
            });
        },

        /**
         * Acknowledge an event
         */
        acknowledge(id, user) {
            safeExecute(
                `UPDATE events SET 
                    acknowledged = 1, 
                    acknowledged_by = ?,
                    acknowledged_at = ?
                WHERE id = ?`,
                [user, now(), id]
            );
            return { acknowledged: true, id };
        },

        /**
         * Get event statistics
         */
        getStats(hours = 24) {
            const since = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
            return safeQuery(
                `SELECT 
                    type,
                    category,
                    COUNT(*) as count
                FROM events
                WHERE created_at >= ?
                GROUP BY type, category`,
                [since]
            );
        },

        /**
         * Clean old events
         */
        cleanup(retentionDays = 365) {
            const cutoff = new Date(Date.now() - retentionDays * 24 * 60 * 60 * 1000).toISOString();
            const result = safeExecute(
                'DELETE FROM events WHERE created_at < ?',
                [cutoff]
            );
            return { deleted: result.changes };
        },

        /**
         * Clear all events (use with caution)
         */
        clearAll() {
            safeExecute('DELETE FROM events');
            return { cleared: true };
        }
    };

    // ============================================
    // DEVICES DAL
    // ============================================

    const Devices = {
        /**
         * Get all devices
         */
        getAll() {
            return safeQuery('SELECT * FROM devices ORDER BY created_at DESC');
        },

        /**
         * Get device by ID
         */
        getById(id) {
            const results = safeQuery('SELECT * FROM devices WHERE id = ?', [id]);
            return results[0] || null;
        },

        /**
         * Create/update device from telemetry
         */
        register(data) {
            const existing = this.getById(data.device_id);
            
            if (existing) {
                // Update last_seen
                safeExecute(
                    'UPDATE devices SET last_seen = ?, status = ? WHERE id = ?',
                    [now(), 'online', data.device_id]
                );
                return this.getById(data.device_id);
            }

            // Create new device
            safeExecute(
                `INSERT INTO devices (id, name, mqtt_topic, status, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)`,
                [
                    data.device_id,
                    data.name || data.device_id,
                    data.mqtt_topic || null,
                    'online',
                    now(),
                    now(),
                    now()
                ]
            );

            return this.getById(data.device_id);
        },

        /**
         * Update device status
         */
        updateStatus(id, status) {
            safeExecute(
                'UPDATE devices SET status = ?, updated_at = ? WHERE id = ?',
                [status, now(), id]
            );
        },

        /**
         * Mark device as offline if not seen for a while
         */
        checkOffline(timeoutMinutes = 5) {
            const cutoff = new Date(Date.now() - timeoutMinutes * 60 * 1000).toISOString();
            safeExecute(
                `UPDATE devices 
                SET status = 'offline' 
                WHERE last_seen < ? AND status = 'online'`,
                [cutoff]
            );
        }
    };

    // ============================================
    // CUSTOMERS DAL
    // ============================================

    const Customers = {
        /**
         * Get all customers
         */
        getAll() {
            return safeQuery('SELECT * FROM customers ORDER BY name');
        },

        /**
         * Get customer by ID
         */
        getById(id) {
            const results = safeQuery('SELECT * FROM customers WHERE id = ?', [id]);
            return results[0] || null;
        },

        /**
         * Create customer
         */
        create(data) {
            const id = data.id || generateId('cust_');
            safeExecute(
                `INSERT INTO customers (id, name, phone, email, company, address, city, country, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    id,
                    data.name,
                    data.phone || null,
                    data.email || null,
                    data.company || null,
                    data.address || null,
                    data.city || null,
                    data.country || 'UA',
                    data.notes || null,
                    now(),
                    now()
                ]
            );
            return this.getById(id);
        },

        /**
         * Find or create customer by name/phone
         */
        findOrCreate(data) {
            // Try to find existing customer
            let customer = null;
            
            if (data.phone) {
                const results = safeQuery('SELECT * FROM customers WHERE phone = ?', [data.phone]);
                if (results.length > 0) customer = results[0];
            }
            
            if (!customer && data.name) {
                const results = safeQuery('SELECT * FROM customers WHERE name = ?', [data.name]);
                if (results.length > 0) customer = results[0];
            }

            if (customer) {
                return customer;
            }

            // Create new customer
            return this.create(data);
        }
    };

    // ============================================
    // DRIVERS DAL
    // ============================================

    const Drivers = {
        /**
         * Get all drivers
         */
        getAll() {
            return safeQuery('SELECT * FROM drivers ORDER BY name');
        },

        /**
         * Get driver by ID
         */
        getById(id) {
            const results = safeQuery('SELECT * FROM drivers WHERE id = ?', [id]);
            return results[0] || null;
        },

        /**
         * Create driver
         */
        create(data) {
            const id = data.id || generateId('drv_');
            safeExecute(
                `INSERT INTO drivers (id, name, phone, email, license_number, vehicle_id, vehicle_model, vehicle_plate, vehicle_weight, avatar, status, safety_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    id,
                    data.name,
                    data.phone || null,
                    data.email || null,
                    data.license_number || null,
                    data.vehicle_id || null,
                    data.vehicle_model || null,
                    data.vehicle_plate || null,
                    data.vehicle_weight || null,
                    data.avatar || null,  // Base64 encoded image
                    data.status || 'active',
                    data.safety_score || null,
                    now(),
                    now()
                ]
            );
            return this.getById(id);
        },

        /**
         * Update driver
         */
        update(id, data) {
            const updateData = {
                ...data,
                updated_at: now()
            };
            
            const fields = [];
            const values = [];
            
            // Build dynamic update query
            if (updateData.name !== undefined) { fields.push('name = ?'); values.push(updateData.name); }
            if (updateData.phone !== undefined) { fields.push('phone = ?'); values.push(updateData.phone); }
            if (updateData.email !== undefined) { fields.push('email = ?'); values.push(updateData.email); }
            if (updateData.license_number !== undefined) { fields.push('license_number = ?'); values.push(updateData.license_number); }
            if (updateData.vehicle_id !== undefined) { fields.push('vehicle_id = ?'); values.push(updateData.vehicle_id); }
            if (updateData.vehicle_model !== undefined) { fields.push('vehicle_model = ?'); values.push(updateData.vehicle_model); }
            if (updateData.vehicle_plate !== undefined) { fields.push('vehicle_plate = ?'); values.push(updateData.vehicle_plate); }
            if (updateData.vehicle_weight !== undefined) { fields.push('vehicle_weight = ?'); values.push(updateData.vehicle_weight); }
            if (updateData.avatar !== undefined) { fields.push('avatar = ?'); values.push(updateData.avatar); }
            if (updateData.status !== undefined) { fields.push('status = ?'); values.push(updateData.status); }
            if (updateData.safety_score !== undefined) { fields.push('safety_score = ?'); values.push(updateData.safety_score); }
            
            fields.push('updated_at = ?');
            values.push(now());
            values.push(id);
            
            if (fields.length > 0) {
                safeExecute(
                    `UPDATE drivers SET ${fields.join(', ')} WHERE id = ?`,
                    values
                );
            }
            return this.getById(id);
        }
    };

    // ============================================
    // SETTINGS DAL
    // ============================================

    const Settings = {
        /**
         * Get a setting value
         */
        get(key, defaultValue = null) {
            const results = safeQuery('SELECT value, value_type FROM settings WHERE key = ?', [key]);
            if (results.length === 0) return defaultValue;
            
            const { value, value_type } = results[0];
            
            // Parse based on type
            switch (value_type) {
                case 'number':
                    return parseFloat(value);
                case 'boolean':
                    return value === 'true';
                case 'json':
                    try { return JSON.parse(value); } catch { return defaultValue; }
                default:
                    return value;
            }
        },

        /**
         * Set a setting value
         */
        set(key, value, type = null) {
            const valueType = type || typeof value;
            let storedValue = value;
            
            if (valueType === 'object') {
                storedValue = JSON.stringify(value);
            } else {
                storedValue = String(value);
            }

            safeExecute(
                `INSERT OR REPLACE INTO settings (key, value, value_type, updated_at)
                VALUES (?, ?, ?, ?)`,
                [key, storedValue, valueType, now()]
            );
        },

        /**
         * Get all settings
         */
        getAll() {
            return safeQuery('SELECT * FROM settings ORDER BY key');
        },

        /**
         * Delete a setting
         */
        delete(key) {
            safeExecute('DELETE FROM settings WHERE key = ?', [key]);
        }
    };

    // ============================================
    // GEOFENCES DAL
    // ============================================

    const Geofences = {
        /**
         * Get all geofences
         */
        getAll(activeOnly = true) {
            let sql = 'SELECT * FROM geofences';
            if (activeOnly) {
                sql += ' WHERE is_active = 1';
            }
            sql += ' ORDER BY created_at DESC';
            return safeQuery(sql);
        },

        /**
         * Get geofence by ID
         */
        getById(id) {
            const results = safeQuery('SELECT * FROM geofences WHERE id = ?', [id]);
            return results[0] || null;
        },

        /**
         * Create geofence
         */
        create(data) {
            const id = data.id || generateId('geo_');
            safeExecute(
                `INSERT INTO geofences (id, name, description, geometry_type, center_lat, center_lng, radius_meters, coordinates, assigned_device_id, assigned_order_id, alert_on_enter, alert_on_exit, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                [
                    id,
                    data.name,
                    data.description || null,
                    data.geometry_type || 'circle',
                    data.center_lat || null,
                    data.center_lng || null,
                    data.radius_meters || null,
                    data.coordinates ? JSON.stringify(data.coordinates) : null,
                    data.assigned_device_id || null,
                    data.assigned_order_id || null,
                    data.alert_on_enter !== false ? 1 : 0,
                    data.alert_on_exit !== false ? 1 : 0,
                    data.is_active !== false ? 1 : 0,
                    now(),
                    now()
                ]
            );
            return this.getById(id);
        },

        /**
         * Log geofence event
         */
        logEvent(data) {
            safeExecute(
                `INSERT INTO geofence_events (geofence_id, device_id, order_id, event_type, lat, lng, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)`,
                [
                    data.geofence_id,
                    data.device_id || null,
                    data.order_id || null,
                    data.event_type,
                    data.lat || null,
                    data.lng || null,
                    now()
                ]
            );
        }
    };

    // ============================================
    // EXPORT PUBLIC API
    // ============================================

    window.SGUDAL = {
        Orders,
        Telemetry,
        Events,
        Devices,
        Customers,
        Drivers,
        Settings,
        Geofences,
        
        // Utilities
        utils: {
            generateId,
            now
        }
    };

    console.log('[DAL] Data Access Layer loaded');
})();
