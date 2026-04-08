/**
 * SGU Logistics Database Integration
 * 
 * Patches existing application functions to use the database
 * without modifying the original logic. This provides:
 * 
 * - Automatic database initialization on page load
 * - Interception of existing functions for data persistence
 * - Backward compatibility with localStorage
 * - Graceful fallback if database fails
 * 
 * Usage: Include this file after db.js, dal.js, migrate.js, and service.js
 * 
 * @module database/integration
 */

(function() {
    'use strict';

    // Wait for DOM and scripts to be ready
    function initIntegration() {
        console.log('[DBI] Initializing database integration...');

        // Check if database modules are loaded
        if (!window.SGUDatabase || !window.SGUDAL || !window.SGUService) {
            console.error('[DBI] Database modules not loaded. Make sure to include db.js, dal.js, and service.js first.');
            return;
        }

        // Initialize database service
        window.SGUService.init({ autoMigrate: true })
            .then(success => {
                if (success) {
                    console.log('[DBI] Database integration active');
                    patchExistingFunctions();
                    initializeSampleData();
                    initializeSmartAI();
                } else {
                    console.warn('[DBI] Database not available, running in localStorage mode');
                }
            })
            .catch(error => {
                console.error('[DBI] Integration failed:', error);
            });
    }

    /**
     * Patch existing functions to add database persistence
     */
    function patchExistingFunctions() {
        // ============================================
        // 1. PATCH EVENT LOGGING
        // ============================================

        // Store original logReport function
        const originalLogReport = window.logReport;
        
        // Replace with database-aware version
        window.logReport = function(type, event, details, speed = 0) {
            // Call original function first (maintains existing behavior)
            if (typeof originalLogReport === 'function') {
                try {
                    originalLogReport(type, event, details, speed);
                } catch (e) {
                    console.error('[DBI] Original logReport error:', e);
                }
            }

            // Also log to database
            try {
                window.SGUService.Events.log(type, event, details, speed, {
                    sensors: window.latestSensorStates ? { ...window.latestSensorStates } : null,
                    loc: window.currentLat && window.currentLng ? 
                        `${window.currentLat.toFixed(4)}, ${window.currentLng.toFixed(4)}` : 'Waiting...'
                });
            } catch (error) {
                console.error('[DBI] Database log error:', error);
            }
        };

        console.log('[DBI] Patched logReport');

        // ============================================
        // 2. PATCH TELEMETRY PROCESSING
        // ============================================

        // Intercept MQTT message processing
        const originalOnMessageArrived = window.onMessageArrived;
        
        window.onMessageArrived = function(message) {
            // Parse the message to get device ID and data
            let deviceId = 'esp32-device-01';
            let payload = null;
            
            try {
                payload = JSON.parse(message.payloadString);
                deviceId = payload.device_id || deviceId;
                
                // Store telemetry in database
                window.SGUService.Telemetry.store(deviceId, payload);
                
            } catch (e) {
                console.error('[DBI] Error storing telemetry:', e);
            }

            // Call original function
            if (typeof originalOnMessageArrived === 'function') {
                return originalOnMessageArrived(message);
            }
        };

        console.log('[DBI] Patched onMessageArrived');

        // ============================================
        // 3. PATCH ORDER MANAGEMENT
        // ============================================

        // Patch order creation
        const originalSaveOrder = window.saveOrder;
        
        window.saveOrder = function(event) {
            event.preventDefault();
            
            const isEdit = !!window.currentOrderId;
            
            // Build order data from form
            const orderData = {
                order_id: document.getElementById('order-order-id')?.value,
                type: document.getElementById('order-type')?.value,
                status: document.getElementById('order-status')?.value,
                origin_city: document.getElementById('order-origin-city')?.value,
                origin_address: document.getElementById('order-origin-address')?.value,
                destination_city: document.getElementById('order-destination-city')?.value,
                destination_address: document.getElementById('order-destination-address')?.value,
                customer_name: document.getElementById('order-customer-name')?.value,
                customer_phone: document.getElementById('order-customer-phone')?.value,
                pickup_date: document.getElementById('order-pickup-date')?.value,
                delivery_date: document.getElementById('order-delivery-date')?.value,
                driver_id: null,
                vehicle_id: null
            };

            if (isEdit) {
                // Update existing order
                window.SGUService.Orders.update(window.currentOrderId, orderData);
                
                // Update in-memory array for compatibility
                const index = window.orders?.findIndex(o => o.id === window.currentOrderId);
                if (index !== -1 && window.orders) {
                    window.orders[index] = { ...window.orders[index], ...orderData };
                }
                
                window.logReport('INFO', 'Order Updated', `Order ${orderData.order_id} updated`);
            } else {
                // Create new order
                const newOrder = window.SGUService.Orders.create(orderData);
                
                // Add to in-memory array if not already there
                if (window.orders && !window.orders.find(o => o.id === newOrder.id)) {
                    window.orders.unshift(newOrder);
                }
                
                window.logReport('INFO', 'Order Created', `New order ${orderData.order_id} created`);
            }

            // Refresh UI
            if (typeof window.renderOrders === 'function') {
                window.renderOrders();
            }
            if (typeof window.closeOrderModal === 'function') {
                window.closeOrderModal();
            }
        };

        // Patch order deletion
        const originalDeleteOrder = window.deleteOrder;
        
        window.deleteOrder = function(orderId) {
            if (!confirm('Are you sure you want to delete this order?')) return;
            
            // Delete from database
            window.SGUService.Orders.delete(orderId);
            
            // Remove from in-memory array
            if (window.orders) {
                window.orders = window.orders.filter(o => o.id !== orderId);
            }
            
            // Refresh UI
            if (typeof window.renderOrders === 'function') {
                window.renderOrders();
            }
            
            window.logReport('INFO', 'Order Deleted', `Order ${orderId} deleted`);
        };

        // Patch order initialization to load from database
        const originalInitOrders = window.initOrders;
        
        window.initOrders = function() {
            // Load orders from database into in-memory array
            try {
                const dbOrders = window.SGUService.Orders.getAll();
                window.orders = dbOrders;
                console.log(`[DBI] Loaded ${dbOrders.length} orders from database`);
            } catch (error) {
                console.error('[DBI] Error loading orders:', error);
                if (!window.orders) window.orders = [];
            }
            
            // Call original function (which will use the updated window.orders)
            if (typeof originalInitOrders === 'function') {
                return originalInitOrders();
            } else {
                // Fallback if original doesn't exist
                if (typeof window.renderOrders === 'function') {
                    window.renderOrders();
                }
            }
        };

        console.log('[DBI] Patched order management functions');

        // ============================================
        // 4. PATCH REPORT LOADING
        // ============================================

        const originalLoadReports = window.loadReports;
        
        window.loadReports = function() {
            // Try to load from database first
            try {
                const dbEvents = window.SGUService.Events.getRecent(100);
                
                // If we have database events, render them
                if (dbEvents && dbEvents.length > 0) {
                    renderEventsFromDB(dbEvents);
                    return;
                }
            } catch (error) {
                console.error('[DBI] Error loading reports from DB:', error);
            }
            
            // Fall back to original function
            if (typeof originalLoadReports === 'function') {
                return originalLoadReports();
            }
        };

        console.log('[DBI] Patched loadReports');

        // ============================================
        // 5. PATCH CLEAR REPORTS
        // ============================================

        const originalClearReports = window.clearReports;
        
        window.clearReports = function() {
            if (confirm("Delete all history?")) {
                // Clear from database
                try {
                    window.SGUService.Events.clear();
                } catch (error) {
                    console.error('[DBI] Error clearing database events:', error);
                }
                
                // Clear from localStorage
                localStorage.removeItem('sensor_reports');
                
                // Reset event counts
                if (typeof window.eventCounts === 'object') {
                    window.eventCounts = { info: 0, warning: 0, critical: 0 };
                }
                
                // Update UI
                const countInfo = document.getElementById('count-info');
                const countWarn = document.getElementById('count-warn');
                const countCrit = document.getElementById('count-crit');
                if (countInfo) countInfo.innerText = '0';
                if (countWarn) countWarn.innerText = '0';
                if (countCrit) countCrit.innerText = '0';
                
                if (typeof window.loadReports === 'function') {
                    window.loadReports();
                }
            }
        };

        console.log('[DBI] Patched clearReports');

        // ============================================
        // 6. PATCH DEVICE ORDER CREATION
        // ============================================

        const originalUpdateOrCreateDeviceOrder = window.updateOrCreateDeviceOrder;
        
        window.updateOrCreateDeviceOrder = function(deviceId, telemetry) {
            // Call original function first
            let result;
            if (typeof originalUpdateOrCreateDeviceOrder === 'function') {
                result = originalUpdateOrCreateDeviceOrder(deviceId, telemetry);
            }
            
            // Also ensure device is registered in database
            try {
                window.SGUDAL.Devices.register({ device_id: deviceId });
                
                // Store telemetry
                window.SGUService.Telemetry.store(deviceId, telemetry);
            } catch (error) {
                console.error('[DBI] Error in device order DB operations:', error);
            }
            
            return result;
        };

        console.log('[DBI] Patched updateOrCreateDeviceOrder');

        // ============================================
        // 7. SETUP PERIODIC SYNC
        // ============================================

        // Periodic device status check
        setInterval(() => {
            try {
                window.SGUDAL.Devices.checkOffline(5);
            } catch (error) {
                // Ignore errors in periodic sync
            }
        }, 60000); // Every minute

        console.log('[DBI] Setup periodic sync');
    }

    /**
     * Render events from database to the reports table
     */
    function renderEventsFromDB(events) {
        const elReportBody = document.getElementById('report-table-body');
        const elEmptyState = document.getElementById('empty-state');
        
        if (!elReportBody) return;

        elReportBody.innerHTML = '';
        
        if (events.length === 0) {
            if (elEmptyState) elEmptyState.classList.remove('hidden');
            return;
        }
        
        if (elEmptyState) elEmptyState.classList.add('hidden');

        events.forEach(r => {
            let badgeClass = r.type === 'CRITICAL' || r.type === 'TIMEOUT' 
                ? 'bg-red-100 text-red-600' 
                : r.type === 'WARNING' || r.type === 'ALERT' 
                    ? 'bg-amber-100 text-amber-600' 
                    : 'bg-blue-100 text-blue-600';

            // Sensor indicators
            const s1Class = r.sensor_s1 === 1 ? 'text-emerald-500 font-bold' : (r.sensor_s1 === 0 ? 'text-red-500 font-bold' : 'text-gray-400');
            const s2Class = r.sensor_s2 === 1 ? 'text-emerald-500 font-bold' : (r.sensor_s2 === 0 ? 'text-red-500 font-bold' : 'text-gray-400');
            const mag1Class = r.sensor_mag1 === 1 ? 'text-emerald-500 font-bold' : (r.sensor_mag1 === 0 ? 'text-red-500 font-bold' : 'text-gray-400');
            const mag2Class = r.sensor_mag2 === 1 ? 'text-emerald-500 font-bold' : (r.sensor_mag2 === 0 ? 'text-red-500 font-bold' : 'text-gray-400');

            const sensorDots = `
                <span class="${s1Class}">S1</span>
                <span class="${s2Class}">S2</span>
                <span class="${mag1Class}">MAG1</span>
                <span class="${mag2Class}">MAG2</span>
            `;

            const dateObj = new Date(r.created_at || r.event_time);
            const dateStr = dateObj.toLocaleDateString();
            const timeStr = dateObj.toLocaleTimeString();

            elReportBody.innerHTML += `
                <tr class="hover:bg-gray-50 transition-colors">
                    <td class="px-4 py-3 font-mono text-xs text-gray-500">${dateStr} ${timeStr}</td>
                    <td class="px-4 py-3">
                        <span class="px-2 py-1 rounded-lg text-[10px] font-semibold ${badgeClass}">${r.type}</span>
                    </td>
                    <td class="px-4 py-3 font-medium text-gray-800">${r.event}</td>
                    <td class="px-4 py-3 font-mono text-xs text-gray-500">${r.location_text || 'N/A'}</td>
                    <td class="px-4 py-3 text-xs truncate max-w-[250px]">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-gray-600">${r.details || 'No details'}</span>
                            <span class="text-gray-300">|</span>
                            <span class="font-mono text-[10px] flex gap-1">${sensorDots}</span>
                        </div>
                    </td>
                </tr>`;
        });
    }

    // ============================================
    // SAMPLE DATA INITIALIZATION
    // ============================================

    function initializeSampleData() {
        // Check if we already have drivers
        const existingDrivers = window.SGUDAL?.Drivers?.getAll() || [];
        if (existingDrivers.length > 0) {
            console.log('[DBI] Sample data already exists, skipping initialization');
            return;
        }

        console.log('[DBI] Initializing sample data...');

        // Sample drivers
        const sampleDrivers = [
            {
                id: 'drv_1',
                name: 'Ahmad Rahman',
                phone: '+62 812-3456-7890',
                email: 'ahmad.rahman@sgu-logistics.com',
                license_number: 'B-1234-XYZ',
                vehicle_model: 'Toyota HiAce',
                vehicle_plate: 'B 1234 ABC',
                status: 'active',
                safety_score: 92
            },
            {
                id: 'drv_2',
                name: 'Siti Nurhaliza',
                phone: '+62 811-9876-5432',
                email: 'siti.nurhaliza@sgu-logistics.com',
                license_number: 'B-5678-UVW',
                vehicle_model: 'Mitsubishi L300',
                vehicle_plate: 'B 5678 DEF',
                status: 'active',
                safety_score: 88
            },
            {
                id: 'drv_3',
                name: 'Budi Santoso',
                phone: '+62 813-2468-1357',
                email: 'budi.santoso@sgu-logistics.com',
                license_number: 'B-9012-RST',
                vehicle_model: 'Isuzu Elf',
                vehicle_plate: 'B 9012 GHI',
                status: 'inactive',
                safety_score: 75
            },
            {
                id: 'drv_4',
                name: 'Maya Sari',
                phone: '+62 814-3698-2468',
                email: 'maya.sari@sgu-logistics.com',
                license_number: 'B-3456-OPQ',
                vehicle_model: 'Suzuki Carry',
                vehicle_plate: 'B 3456 JKL',
                status: 'active',
                safety_score: 95
            },
            {
                id: 'drv_5',
                name: 'Rudi Hartono',
                phone: '+62 815-1478-9632',
                email: 'rudi.hartono@sgu-logistics.com',
                license_number: 'B-7890-MNO',
                vehicle_model: 'Daihatsu Gran Max',
                vehicle_plate: 'B 7890 MNO',
                status: 'active',
                safety_score: 82
            }
        ];

        // Insert sample drivers
        sampleDrivers.forEach(driver => {
            try {
                window.SGUDAL.Drivers.create(driver);
                console.log(`[DBI] Created driver: ${driver.name}`);
            } catch (e) {
                console.error(`[DBI] Failed to create driver ${driver.name}:`, e);
            }
        });

        // Sample devices
        const sampleDevices = [
            { id: 'esp32-device-01', name: 'Truck GPS-01', status: 'online' },
            { id: 'esp32-device-02', name: 'Truck GPS-02', status: 'offline' },
            { id: 'esp32-device-03', name: 'Truck GPS-03', status: 'online' }
        ];

        sampleDevices.forEach(device => {
            try {
                window.SGUDAL.Devices.register({
                    device_id: device.id,
                    name: device.name,
                    mqtt_topic: `monztrack/${device.id}/gps`,
                    status: device.status
                });
                console.log(`[DBI] Created device: ${device.name}`);
            } catch (e) {
                console.error(`[DBI] Failed to create device ${device.name}:`, e);
            }
        });

        console.log('[DBI] Sample data initialization complete');
    }

    function initializeSmartAI() {
        if (typeof SmartAIEngine !== 'undefined') {
            window.smartAI = new SmartAIEngine();
            console.log('[DBI] SmartAI initialized');
        } else {
            console.warn('[DBI] SmartAIEngine not available, AI features disabled');
        }
    }

    // ============================================
    // INITIALIZE
    // ============================================

    // Wait for page to be fully loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initIntegration);
    } else {
        // Page already loaded, wait a bit for other scripts
        setTimeout(initIntegration, 500);
    }

    // Also initialize on window load as backup
    window.addEventListener('load', () => {
        if (!window.SGUService?.isReady()) {
            initIntegration();
        }
    });

    console.log('[DBI] Integration module loaded, waiting for initialization...');
})();
