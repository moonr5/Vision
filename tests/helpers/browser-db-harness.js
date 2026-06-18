/**
 * Loads browser database modules in Node without modifying database/*.js.
 * Uses jsdom + sql.js + fake-indexeddb to mirror the browser runtime.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const initSqlJs = require('sql.js');

const DEFAULT_SETTINGS = [
    ['mqtt_broker', 'wss://broker.hivemq.com:8884/mqtt', 'string'],
    ['mqtt_topic', 'monztrack/device01/gps', 'string'],
    ['map_default_lat', '-6.2252', 'number'],
    ['map_default_lng', '106.6552', 'number'],
    ['map_default_zoom', '15', 'number'],
    ['geofence_alert_enabled', 'true', 'boolean'],
    ['fuel_theft_alert_enabled', 'true', 'boolean'],
    ['security_alert_enabled', 'true', 'boolean'],
    ['telemetry_retention_days', '90', 'number'],
    ['events_retention_days', '365', 'number'],
];

/**
 * Test-only alignment: apply full schema.sql because db.js uses db.run() which
 * only executes the first SQL statement. Does not modify operational files.
 */
function applyTestOnlySchemaPatches(SGUDatabase) {
    const schemaPath = path.join(__dirname, '../../database/schema.sql');
    const schemaSql = fs.readFileSync(schemaPath, 'utf8');

    try {
        SGUDatabase.getDB().exec(schemaSql);
    } catch (_) {
        // CREATE IF NOT EXISTS / views may partially exist on repeat exec
    }

    for (const [key, value, valueType] of DEFAULT_SETTINGS) {
        try {
            SGUDatabase.execute(
                'INSERT OR IGNORE INTO settings (key, value, value_type) VALUES (?, ?, ?)',
                [key, value, valueType]
            );
        } catch (_) {
            // settings table unavailable in minimal fallback schema
        }
    }
}

async function loadDatabaseStack() {
    require('fake-indexeddb/auto');

    const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>', {
        url: 'http://localhost/',
        pretendToBeVisual: true,
    });

    global.window = dom.window;
    global.document = dom.window.document;
    global.navigator = dom.window.navigator;
    global.Blob = dom.window.Blob;
    global.File = dom.window.File;
    global.FileReader = dom.window.FileReader;
    global.localStorage = dom.window.localStorage;

    const sqlDist = path.dirname(require.resolve('sql.js'));
    const SQL = await initSqlJs({
        locateFile: (file) => path.join(sqlDist, file),
    });
    dom.window.SQL = SQL;
    global.SQL = SQL;

    const dbDir = path.join(__dirname, '../../database');
    for (const file of ['db.js', 'dal.js', 'ai-engine.js']) {
        dom.window.eval(fs.readFileSync(path.join(dbDir, file), 'utf8'));
    }

    await dom.window.SGUDatabase.init();
    applyTestOnlySchemaPatches(dom.window.SGUDatabase);

    return {
        SGUDatabase: dom.window.SGUDatabase,
        SGUDAL: dom.window.SGUDAL,
        smartAI: dom.window.smartAI,
    };
}

module.exports = { loadDatabaseStack, applyTestOnlySchemaPatches };
