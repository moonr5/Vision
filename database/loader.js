/**
 * SGU Logistics Database Loader
 * 
 * Loads all database modules in the correct order:
 * 1. db.js - Core database initialization (sql.js)
 * 2. dal.js - Data Access Layer
 * 3. migrate.js - Data migration from localStorage
 * 4. service.js - High-level service API
 * 5. integration.js - Application integration patches
 * 
 * Usage: Include this single file in your HTML:
 *   <script src="database/loader.js"></script>
 */

(function() {
    'use strict';

    const DB_MODULES = [
        'database/db.js',      // Core database
        'database/dal.js',     // Data Access Layer
        'database/ai-engine.js', // AI Engine
        'database/migrate.js', // Migration script
        'database/service.js', // Service layer
        'database/integration.js' // Integration patches
    ];

    const BASE_URL = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);

    /**
     * Load a script dynamically
     */
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = src;
            script.async = false; // Maintain load order
            script.onload = () => {
                console.log('[DB-LOADER] Loaded:', src);
                resolve();
            };
            script.onerror = () => {
                console.error('[DB-LOADER] Failed to load:', src);
                reject(new Error(`Failed to load ${src}`));
            };
            document.head.appendChild(script);
        });
    }

    /**
     * Load all database modules in sequence
     */
    async function loadAllModules() {
        console.log('[DB-LOADER] Starting database module loading...');
        
        try {
            for (const module of DB_MODULES) {
                await loadScript(BASE_URL + module);
            }
            console.log('[DB-LOADER] All database modules loaded successfully');
            
            // Dispatch event when database is ready
            window.dispatchEvent(new CustomEvent('sgu-db-ready', { 
                detail: { 
                    database: window.SGUDatabase,
                    dal: window.SGUDAL,
                    service: window.SGUService,
                    migration: window.SGUMigration
                }
            }));
            
        } catch (error) {
            console.error('[DB-LOADER] Module loading failed:', error);
            
            // Dispatch error event
            window.dispatchEvent(new CustomEvent('sgu-db-error', { 
                detail: { error }
            }));
        }
    }

    // Start loading
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', loadAllModules);
    } else {
        loadAllModules();
    }
})();
