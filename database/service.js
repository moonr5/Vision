/**
 * SGU Logistics Database Service
 * 
 * High-level service layer that integrates the database with the existing application.
 * Provides backward-compatible APIs while adding database persistence.
 * 
 * This service wraps the DAL and provides:
 * - Automatic database initialization
 * - Data synchronization between localStorage and database
 * - Event logging from MQTT messages
 * - Order management with persistence
 * - Telemetry storage and retrieval
 * 
 * @module database/service
 */

(function() {
    'use strict';

    // Service state
    const state = {
        initialized: false,
        initializing: false,
        queue: [],
        config: {
            autoMigrate: true,
            syncInterval: 5000,
            enableLogging: true,
            maxQueuedOperations: 100
        }
    };

    // ============================================
    // CORE SERVICE
    // ============================================

    const DBService = {
        /**
         * Initialize the database service
         * This should be called early in the application lifecycle
         */
        async init(config = {}) {
            if (state.initialized) return true;
            if (state.initializing) {
                // Wait for initialization to complete
                return new Promise((resolve) => {
                    const check = setInterval(() => {
                        if (state.initialized) {
                            clearInterval(check);
                            resolve(true);
                        }
                    }, 100);
                });
            }

            state.initializing = true;
            state.config = { ...state.config, ...config };

            try {
                console.log('[DBS] Initializing database service...');

                // Initialize database
                await window.SGUDatabase.init();
                
                // Wait for DAL to be ready
                await new Promise(r => setTimeout(r, 200));

                // Run migration if needed
                if (state.config.autoMigrate && window.SGUMigration?.isNeeded()) {
                    console.log('[DBS] Running data migration...');
                    await window.SGUMigration.run();
                }

                // Setup sync handlers
                this.setupSync();

                state.initialized = true;
                state.initializing = false;

                console.log('[DBS] Database service initialized');
                return true;

            } catch (error) {
                console.error('[DBS] Initialization failed:', error);
                state.initializing = false;
                
                // Fall back to localStorage mode
                console.warn('[DBS] Falling back to localStorage mode');
                return false;
            }
        },

        /**
         * Check if service is ready
         */
        isReady() {
            return state.initialized;
        },

        /**
         * Setup data synchronization
         */
        setupSync() {
            // Periodic cleanup of old data
            setInterval(() => {
                this.cleanup();
            }, 60 * 60 * 1000); // Every hour
        },

        /**
         * Cleanup old data based on retention settings
         */
        async cleanup() {
            try {
                const telemetryRetention = window.SGUDAL.Settings.get('telemetry_retention_days', 90);
                const eventsRetention = window.SGUDAL.Settings.get('events_retention_days', 365);

                window.SGUDAL.Telemetry.cleanup(telemetryRetention);
                window.SGUDAL.Events.cleanup(eventsRetention);

                console.log('[DBS] Cleanup completed');
            } catch (error) {
                console.error('[DBS] Cleanup error:', error);
            }
        }
    };

    // ============================================
    // ORDERS SERVICE
    // ============================================

    const OrdersService = {
        /**
         * Get all orders (combines database with in-memory array)
         * This maintains compatibility with existing orders array
         */
        getAll(filters = {}) {
            if (!state.initialized) {
                // Fall back to global orders array
                return window.orders || [];
            }

            try {
                const dbOrders = window.SGUDAL.Orders.getAll(filters);
                
                // Convert DB format to match existing order format
                return dbOrders.map(this.formatOrderFromDB);
            } catch (error) {
                console.error('[DBS] Error getting orders:', error);
                return window.orders || [];
            }
        },

        /**
         * Get active orders
         */
        getActive() {
            return this.getAll({ 
                status: ['pickup', 'transit', 'delivery'] 
            });
        },

        /**
         * Get order by ID
         */
        getById(id) {
            if (!state.initialized) {
                return (window.orders || []).find(o => o.id === id);
            }

            try {
                const order = window.SGUDAL.Orders.getById(id);
                return order ? this.formatOrderFromDB(order) : null;
            } catch (error) {
                return (window.orders || []).find(o => o.id === id);
            }
        },

        /**
         * Create a new order
         */
        create(orderData) {
            // Generate ID if not provided
            const id = orderData.id || `ord_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            const order = {
                id,
                ...orderData,
                created_at: orderData.created_at || new Date().toISOString(),
                updated_at: new Date().toISOString()
            };

            // Always add to in-memory array for compatibility
            if (!window.orders) window.orders = [];
            window.orders.unshift(order);

            // Also save to database if available
            if (state.initialized) {
                try {
                    window.SGUDAL.Orders.create({
                        id: order.id,
                        order_id: order.order_id,
                        type: order.type,
                        status: order.status,
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
                } catch (error) {
                    console.error('[DBS] Error saving order to DB:', error);
                }
            }

            // Log event
            this.logEvent('INFO', 'Order Created', `Order ${order.order_id} created`, order.id);

            return order;
        },

        /**
         * Update an order
         */
        update(id, updates) {
            // Update in-memory array
            if (window.orders) {
                const index = window.orders.findIndex(o => o.id === id);
                if (index !== -1) {
                    window.orders[index] = { 
                        ...window.orders[index], 
                        ...updates,
                        updated_at: new Date().toISOString()
                    };
                }
            }

            // Update in database
            if (state.initialized) {
                try {
                    window.SGUDAL.Orders.update(id, updates);
                } catch (error) {
                    console.error('[DBS] Error updating order in DB:', error);
                }
            }

            this.logEvent('INFO', 'Order Updated', `Order ${id} updated`, id);
            return this.getById(id);
        },

        /**
         * Delete an order
         */
        delete(id) {
            // Remove from in-memory array
            if (window.orders) {
                window.orders = window.orders.filter(o => o.id !== id);
            }

            // Delete from database
            if (state.initialized) {
                try {
                    window.SGUDAL.Orders.delete(id);
                } catch (error) {
                    console.error('[DBS] Error deleting order from DB:', error);
                }
            }

            this.logEvent('INFO', 'Order Deleted', `Order ${id} deleted`, id);
            return { deleted: true, id };
        },

        /**
         * Update order from device telemetry
         * Called when new MQTT data arrives
         */
        updateFromTelemetry(deviceId, telemetry) {
            if (!state.initialized) return;

            try {
                // Find order assigned to this device
                const orders = window.SGUDAL.Orders.getAll({ device_id: deviceId });
                
                for (const order of orders) {
                    // Update location
                    if (telemetry.lat !== undefined && telemetry.lng !== undefined) {
                        window.SGUDAL.Orders.updateLocation(
                            order.id,
                            telemetry.lat,
                            telemetry.lng,
                            telemetry.speed || 0
                        );
                    }

                    // Update status based on movement
                    const speed = parseFloat(telemetry.speed) || 0;
                    let newStatus = order.status;
                    
                    if (speed > 5 && order.status === 'pickup') {
                        newStatus = 'transit';
                    }

                    if (newStatus !== order.status) {
                        window.SGUDAL.Orders.update(order.id, { status: newStatus });
                    }
                }
            } catch (error) {
                console.error('[DBS] Error updating order from telemetry:', error);
            }
        },

        /**
         * Format order from database to match existing format
         */
        formatOrderFromDB(dbOrder) {
            return {
                id: dbOrder.id,
                order_id: dbOrder.order_id,
                type: dbOrder.type,
                status: dbOrder.status,
                origin_city: dbOrder.origin_city,
                origin_address: dbOrder.origin_address,
                destination_city: dbOrder.destination_city,
                destination_address: dbOrder.destination_address,
                customer_name: dbOrder.customer_name,
                customer_phone: dbOrder.customer_phone,
                driver_id: dbOrder.driver_id,
                vehicle_id: dbOrder.vehicle_id,
                device_id: dbOrder.device_id,
                pickup_date: dbOrder.pickup_date,
                delivery_date: dbOrder.delivery_date,
                notes: dbOrder.notes,
                created_at: dbOrder.created_at,
                updated_at: dbOrder.updated_at,
                // Additional computed fields
                lastUpdate: dbOrder.last_update,
                telemetry: null // Will be populated separately if needed
            };
        },

        /**
         * Log order-related event
         */
        logEvent(type, event, details, orderId) {
            if (!state.initialized) return;
            
            try {
                window.SGUDAL.Events.log({
                    type,
                    category: 'ORDERS',
                    event,
                    details,
                    order_id: orderId
                });
            } catch (error) {
                console.error('[DBS] Error logging event:', error);
            }
        }
    };

    // ============================================
    // TELEMETRY SERVICE
    // ============================================

    const TelemetryService = {
        /**
         * Store telemetry data from MQTT message
         * Called from onMessageArrived
         */
        store(deviceId, payload) {
            if (!state.initialized) return null;

            try {
                // Parse payload according to ESP32 structure
                const data = {
                    device_id: deviceId,
                    
                    // GPS data
                    lat: payload.lat !== undefined ? parseFloat(payload.lat) : null,
                    lng: payload.lng !== undefined ? parseFloat(payload.lng) : null,
                    speed: payload.speed !== undefined ? parseFloat(payload.speed) : null,
                    satellites: payload.sats || payload.gps?.sats,
                    gps_fix: payload.loc !== undefined ? payload.loc : (payload.gps?.loc),
                    
                    // Sensors
                    sensor_s1: payload.s1 !== undefined ? payload.s1 : payload.sensors?.s1,
                    sensor_s2: payload.s2 !== undefined ? payload.s2 : payload.sensors?.s2,
                    sensor_mag1: payload.mag1 !== undefined ? payload.mag1 : payload.sensors?.mag1,
                    sensor_mag2: payload.mag2 !== undefined ? payload.mag2 : payload.sensors?.mag2,
                    
                    // OBD-II
                    obd_rpm: payload.obd?.rpm,
                    obd_engine_load: payload.obd?.engine_load,
                    obd_coolant_temp: payload.obd?.coolant_temp,
                    obd_throttle: payload.obd?.throttle,
                    obd_mil: payload.obd?.mil,
                    
                    // Fuel
                    fuel_level: payload.fuel?.level_percent,
                    fuel_flow_in: payload.fuel?.flow_in,
                    fuel_flow_out: payload.fuel?.flow_out,
                    fuel_cap_open: payload.fuel?.cap_open,
                    fuel_theft_detected: payload.fuel?.theft_detected,
                    
                    // Raw payload for reference
                    raw_payload: payload
                };

                // Store telemetry
                const result = window.SGUDAL.Telemetry.save(data);

                // Update order location if device is assigned to an order
                OrdersService.updateFromTelemetry(deviceId, data);

                // Update device last_seen
                window.SGUDAL.Devices.register({ device_id: deviceId });

                return result;

            } catch (error) {
                console.error('[DBS] Error storing telemetry:', error);
                return null;
            }
        },

        /**
         * Get telemetry history for a device
         */
        getHistory(deviceId, options = {}) {
            if (!state.initialized) return [];
            
            try {
                return window.SGUDAL.Telemetry.getByDevice(deviceId, options);
            } catch (error) {
                console.error('[DBS] Error getting telemetry history:', error);
                return [];
            }
        },

        /**
         * Get route coordinates for map drawing
         */
        getRoute(deviceId, hours = 24) {
            if (!state.initialized) return [];
            
            try {
                const points = window.SGUDAL.Telemetry.getRoute(deviceId, hours);
                return points.map(p => [p.lat, p.lng]);
            } catch (error) {
                console.error('[DBS] Error getting route:', error);
                return [];
            }
        },

        /**
         * Get latest telemetry for a device
         */
        getLatest(deviceId) {
            if (!state.initialized) return null;
            
            try {
                return window.SGUDAL.Telemetry.getLatest(deviceId);
            } catch (error) {
                console.error('[DBS] Error getting latest telemetry:', error);
                return null;
            }
        }
    };

    // ============================================
    // EVENTS SERVICE
    // ============================================

    const EventsService = {
        /**
         * Log an event
         * Compatible with existing logReport function
         */
        log(type, event, details, speed = 0, extra = {}) {
            // Always log to localStorage for compatibility
            const history = JSON.parse(localStorage.getItem('sensor_reports') || '[]');
            const report = {
                id: Date.now(),
                time: new Date().toLocaleTimeString(),
                date: new Date().toLocaleDateString(),
                type,
                event,
                loc: extra.loc || `${window.currentLat?.toFixed(4) || 0}, ${window.currentLng?.toFixed(4) || 0}`,
                details,
                sensors: extra.sensors || { ...window.latestSensorStates },
                gps: { lat: window.currentLat, lng: window.currentLng, hasFix: window.gpsHasFix },
                speed
            };
            history.unshift(report);
            if (history.length > 100) history.pop();
            localStorage.setItem('sensor_reports', JSON.stringify(history));

            // Also log to database if available
            if (state.initialized) {
                try {
                    window.SGUDAL.Events.log({
                        type,
                        category: extra.category || this.inferCategory(event),
                        event,
                        details,
                        speed,
                        lat: window.currentLat,
                        lng: window.currentLng,
                        location_text: extra.loc || null,
                        device_id: extra.device_id || window.lastDeviceId,
                        order_id: extra.order_id || null,
                        sensor_s1: extra.sensors?.s1 ?? window.latestSensorStates?.s1,
                        sensor_s2: extra.sensors?.s2 ?? window.latestSensorStates?.s2,
                        sensor_mag1: extra.sensors?.mag1 ?? window.latestSensorStates?.mag1,
                        sensor_mag2: extra.sensors?.mag2 ?? window.latestSensorStates?.mag2,
                        gps_has_fix: window.gpsHasFix
                    });
                } catch (error) {
                    console.error('[DBS] Error logging to database:', error);
                }
            }

            // Update event counts
            if (typeof window.eventCounts === 'object') {
                if (type === 'INFO') window.eventCounts.info++;
                if (type === 'WARNING') window.eventCounts.warning++;
                if (type === 'CRITICAL' || type === 'TIMEOUT') window.eventCounts.critical++;
            }

            return report;
        },

        /**
         * Get recent events
         */
        getRecent(limit = 100) {
            if (!state.initialized) {
                return JSON.parse(localStorage.getItem('sensor_reports') || '[]').slice(0, limit);
            }
            
            try {
                return window.SGUDAL.Events.getRecent(limit);
            } catch (error) {
                return JSON.parse(localStorage.getItem('sensor_reports') || '[]').slice(0, limit);
            }
        },

        /**
         * Get event statistics
         */
        getStats(hours = 24) {
            if (!state.initialized) {
                return { info: 0, warning: 0, critical: 0 };
            }
            
            try {
                const stats = window.SGUDAL.Events.getStats(hours);
                return stats.reduce((acc, s) => {
                    acc[s.type.toLowerCase()] = s.count;
                    return acc;
                }, { info: 0, warning: 0, critical: 0 });
            } catch (error) {
                return { info: 0, warning: 0, critical: 0 };
            }
        },

        /**
         * Clear all events
         */
        clear() {
            localStorage.removeItem('sensor_reports');
            
            if (state.initialized) {
                try {
                    window.SGUDAL.Events.clearAll();
                } catch (error) {
                    console.error('[DBS] Error clearing events:', error);
                }
            }

            if (typeof window.eventCounts === 'object') {
                window.eventCounts = { info: 0, warning: 0, critical: 0 };
            }
        },

        /**
         * Infer event category from event name
         */
        inferCategory(eventName) {
            if (!eventName) return 'SYSTEM';
            const name = eventName.toUpperCase();
            if (name.includes('GEOFENCE')) return 'GEOFENCE';
            if (name.includes('FUEL') || name.includes('THEFT')) return 'FUEL';
            if (name.includes('SECURITY') || name.includes('MAG') || name.includes('S1') || name.includes('S2')) return 'SECURITY';
            if (name.includes('GPS')) return 'GPS';
            if (name.includes('MQTT') || name.includes('CONNECT')) return 'CONNECTIVITY';
            if (name.includes('ORDER')) return 'ORDERS';
            return 'TELEMETRY';
        }
    };

    // ============================================
    // SETTINGS SERVICE
    // ============================================

    const SettingsService = {
        /**
         * Get a setting value
         */
        get(key, defaultValue = null) {
            if (!state.initialized) {
                // Fall back to localStorage settings
                const settings = JSON.parse(localStorage.getItem('sgu_settings_v1') || '{}');
                return settings[key] !== undefined ? settings[key] : defaultValue;
            }

            try {
                return window.SGUDAL.Settings.get(key, defaultValue);
            } catch (error) {
                const settings = JSON.parse(localStorage.getItem('sgu_settings_v1') || '{}');
                return settings[key] !== undefined ? settings[key] : defaultValue;
            }
        },

        /**
         * Set a setting value
         */
        set(key, value, type = null) {
            // Always save to localStorage for compatibility
            const settings = JSON.parse(localStorage.getItem('sgu_settings_v1') || '{}');
            settings[key] = value;
            localStorage.setItem('sgu_settings_v1', JSON.stringify(settings));

            // Also save to database
            if (state.initialized) {
                try {
                    window.SGUDAL.Settings.set(key, value, type);
                } catch (error) {
                    console.error('[DBS] Error saving setting:', error);
                }
            }
        }
    };

    // ============================================
    // EXPORT PUBLIC API
    // ============================================

    window.SGUService = {
        // Core
        init: (...args) => DBService.init(...args),
        isReady: () => DBService.isReady(),
        
        // Services
        Orders: OrdersService,
        Telemetry: TelemetryService,
        Events: EventsService,
        Settings: SettingsService,
        
        // Legacy compatibility
        logReport: (type, event, details, speed, extra) => 
            EventsService.log(type, event, details, speed, extra),
        logEvent: (type, event, details, extra) => 
            EventsService.log(type, event, details, 0, extra)
    };

    console.log('[DBS] Database Service loaded');
})();
