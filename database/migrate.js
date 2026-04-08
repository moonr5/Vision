/**
 * SGU Logistics Database Migration Script
 * 
 * Migrates existing localStorage data to the SQLite database.
 * Run this once when upgrading to the database version.
 * 
 * @module database/migrate
 */

(function() {
    'use strict';

    const Migration = {
        version: 1,
        migrated: false,

        /**
         * Check if migration is needed
         */
        isNeeded() {
            const marker = localStorage.getItem('sgu_db_migrated');
            return marker !== String(this.version);
        },

        /**
         * Mark migration as complete
         */
        markComplete() {
            localStorage.setItem('sgu_db_migrated', String(this.version));
            this.migrated = true;
        },

        /**
         * Run full migration
         */
        async run(options = {}) {
            console.log('[MIGRATE] Starting migration...');
            
            const results = {
                events: 0,
                orders: 0,
                settings: 0,
                errors: []
            };

            try {
                // Ensure database is initialized
                await window.SGUDatabase.init();
                
                // Wait a moment for DAL to be ready
                await new Promise(r => setTimeout(r, 100));

                // Migrate events/reports
                results.events = await this.migrateEvents();

                // Migrate orders (if any were stored in localStorage)
                results.orders = await this.migrateOrders();

                // Migrate settings
                results.settings = await this.migrateSettings();

                this.markComplete();
                
                console.log('[MIGRATE] Migration completed:', results);
                
                if (options.clearLocalStorage) {
                    this.clearMigratedData();
                }

                return results;
                
            } catch (error) {
                console.error('[MIGRATE] Migration failed:', error);
                results.errors.push(error.message);
                throw error;
            }
        },

        /**
         * Migrate events from localStorage
         */
        async migrateEvents() {
            const history = JSON.parse(localStorage.getItem('sensor_reports') || '[]');
            if (history.length === 0) {
                console.log('[MIGRATE] No events to migrate');
                return 0;
            }

            console.log(`[MIGRATE] Migrating ${history.length} events...`);

            let migrated = 0;
            
            for (const item of history) {
                try {
                    // Parse location string if available
                    let lat = null, lng = null;
                    if (item.loc && item.loc !== 'Waiting...' && item.loc.includes(',')) {
                        const parts = item.loc.split(',').map(s => parseFloat(s.trim()));
                        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
                            lat = parts[0];
                            lng = parts[1];
                        }
                    }

                    // Parse speed
                    const speed = item.speed !== undefined ? parseFloat(item.speed) : null;

                    // Get sensor states from the item or its nested sensors object
                    const sensors = item.sensors || {};
                    const s1 = sensors.s1 !== undefined ? sensors.s1 : null;
                    const s2 = sensors.s2 !== undefined ? sensors.s2 : null;
                    const mag1 = sensors.mag1 !== undefined ? sensors.mag1 : null;
                    const mag2 = sensors.mag2 !== undefined ? sensors.mag2 : null;

                    // Build event data
                    const eventData = {
                        type: item.type || 'INFO',
                        category: this.inferCategory(item.event),
                        event: item.event || 'Unknown Event',
                        location_text: item.loc || null,
                        lat,
                        lng,
                        speed,
                        details: item.details || null,
                        sensor_s1: s1,
                        sensor_s2: s2,
                        sensor_mag1: mag1,
                        sensor_mag2: mag2,
                        gps_has_fix: lat !== null && lng !== null,
                        created_at: this.parseDateTime(item.date, item.time),
                        event_time: this.parseDateTime(item.date, item.time)
                    };

                    window.SGUDAL.Events.log(eventData);
                    migrated++;
                    
                } catch (error) {
                    console.warn('[MIGRATE] Failed to migrate event:', item, error);
                }
            }

            console.log(`[MIGRATE] Migrated ${migrated}/${history.length} events`);
            return migrated;
        },

        /**
         * Migrate orders from localStorage (if they were stored there)
         */
        async migrateOrders() {
            // Check for any orders stored in localStorage
            const orders = JSON.parse(localStorage.getItem('sgu_orders') || '[]');
            if (orders.length === 0) {
                console.log('[MIGRATE] No orders to migrate');
                return 0;
            }

            console.log(`[MIGRATE] Migrating ${orders.length} orders...`);

            let migrated = 0;
            
            for (const order of orders) {
                try {
                    // Check if order already exists
                    const existing = window.SGUDAL.Orders.getById(order.id);
                    if (existing) {
                        console.log(`[MIGRATE] Order ${order.id} already exists, skipping`);
                        continue;
                    }

                    window.SGUDAL.Orders.create({
                        id: order.id,
                        order_id: order.order_id,
                        type: order.type || 'domestic',
                        status: order.status || 'pickup',
                        origin_city: order.origin_city,
                        origin_address: order.origin_address,
                        destination_city: order.destination_city,
                        destination_address: order.destination_address,
                        customer_name: order.customer_name,
                        customer_phone: order.customer_phone,
                        driver_id: order.driver_id,
                        vehicle_id: order.vehicle_id,
                        device_id: order.device_id,
                        pickup_date: order.pickup_date,
                        delivery_date: order.delivery_date,
                        notes: order.notes,
                        created_at: order.created_at
                    });
                    
                    migrated++;
                    
                } catch (error) {
                    console.warn('[MIGRATE] Failed to migrate order:', order, error);
                }
            }

            console.log(`[MIGRATE] Migrated ${migrated}/${orders.length} orders`);
            return migrated;
        },

        /**
         * Migrate settings from localStorage
         */
        async migrateSettings() {
            const settings = JSON.parse(localStorage.getItem('sgu_settings_v1') || '{}');
            let migrated = 0;

            // Map old settings to new database settings
            const settingsMap = {
                'geminiKey': { key: 'gemini_api_key', type: 'string' },
                'geminiEnabled': { key: 'gemini_enabled', type: 'boolean' },
                'telegramToken': { key: 'telegram_bot_token', type: 'string' },
                'telegramChatId': { key: 'telegram_chat_id', type: 'string' },
                'telegramEnabled': { key: 'telegram_enabled', type: 'boolean' }
            };

            for (const [oldKey, config] of Object.entries(settingsMap)) {
                if (settings[oldKey] !== undefined) {
                    try {
                        window.SGUDAL.Settings.set(config.key, settings[oldKey], config.type);
                        migrated++;
                    } catch (error) {
                        console.warn('[MIGRATE] Failed to migrate setting:', oldKey, error);
                    }
                }
            }

            console.log(`[MIGRATE] Migrated ${migrated} settings`);
            return migrated;
        },

        /**
         * Infer event category from event name
         */
        inferCategory(eventName) {
            if (!eventName) return 'SYSTEM';
            
            const name = eventName.toUpperCase();
            
            if (name.includes('GEOFENCE')) return 'GEOFENCE';
            if (name.includes('FUEL') || name.includes('THEFT') || name.includes('CAP')) return 'FUEL';
            if (name.includes('SECURITY') || name.includes('MAG') || name.includes('S1') || name.includes('S2')) return 'SECURITY';
            if (name.includes('GPS') || name.includes('LOC')) return 'GPS';
            if (name.includes('MQTT') || name.includes('CONNECT')) return 'CONNECTIVITY';
            if (name.includes('ORDER')) return 'ORDERS';
            
            return 'TELEMETRY';
        },

        /**
         * Parse date and time strings to ISO format
         */
        parseDateTime(dateStr, timeStr) {
            try {
                if (!dateStr && !timeStr) return now();
                
                // Try to parse as ISO first
                if (dateStr && dateStr.includes('T')) {
                    return dateStr;
                }

                // Combine date and time
                const datePart = dateStr || new Date().toLocaleDateString();
                const timePart = timeStr || '00:00:00';
                const combined = `${datePart} ${timePart}`;
                
                const date = new Date(combined);
                if (isNaN(date.getTime())) {
                    return now();
                }
                
                return date.toISOString();
                
            } catch {
                return now();
            }
        },

        /**
         * Clear migrated data from localStorage
         */
        clearMigratedData() {
            // Keep settings in localStorage as fallback
            // Only clear reports if migration succeeded
            console.log('[MIGRATE] Clearing migrated data from localStorage...');
            
            // Optionally clear: localStorage.removeItem('sensor_reports');
            // We keep it as backup for now
        },

        /**
         * Reset migration marker (for testing)
         */
        reset() {
            localStorage.removeItem('sgu_db_migrated');
            this.migrated = false;
            console.log('[MIGRATE] Migration marker reset');
        },

        /**
         * Get migration status
         */
        getStatus() {
            return {
                version: this.version,
                isMigrated: localStorage.getItem('sgu_db_migrated') === String(this.version),
                isNeeded: this.isNeeded(),
                localStorageItems: {
                    reports: JSON.parse(localStorage.getItem('sensor_reports') || '[]').length,
                    orders: JSON.parse(localStorage.getItem('sgu_orders') || '[]').length,
                    settings: Object.keys(JSON.parse(localStorage.getItem('sgu_settings_v1') || '{}')).length
                }
            };
        }
    };

    // Helper function
    function now() {
        return new Date().toISOString();
    }

    // Export
    window.SGUMigration = Migration;

    console.log('[MIGRATE] Migration module loaded');
})();
