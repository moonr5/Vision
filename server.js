// =============================================================================
//  SGU Logistics & Telemetry Dashboard — Application Server
//  Made by Monzer · github.com/moonr5/Vision
// =============================================================================
require('dotenv').config();
const express = require('express');
const path = require('path');
const { Pool } = require('pg');
const fs = require('fs');
const mqtt = require('mqtt');

// ── Integration connector (route_engine + scale_engine bridges) ──────────
const connector = (() => {
    try { return require('./integration/connector'); }
    catch (e) { console.warn('[Server] Integration connector not found — advanced engines disabled'); return null; }
})();

const app = express();
const PORT = process.env.PORT || 3000;
const PYTHON_AI_URL = process.env.PYTHON_AI_URL || null;

const MQTT_BROKER_URL = process.env.MQTT_BROKER_URL || 'mqtt://broker.hivemq.com:1883';
const MQTT_TOPIC     = process.env.MQTT_TOPIC      || 'monztrack/device01/gps';

// PostgreSQL connection pool
const pool = process.env.DATABASE_URL
    ? new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } })
    : null;

// SSE clients waiting for live telemetry
const sseClients = new Set();

// ─── DB helpers ──────────────────────────────────────────────────────────────

async function runMigrations() {
    if (!pool) { console.warn('[DB] DATABASE_URL not set — skipping database setup'); return; }
    const client = await pool.connect();
    try {
        const schema = fs.readFileSync(path.join(__dirname, 'database', 'pg-schema.sql'), 'utf8');
        await client.query(schema);
        console.log('[DB] Schema applied successfully');
    } catch (err) {
        console.error('[DB] Migration error:', err.message);
    } finally {
        client.release();
    }
}

async function saveTelemetry(payload, topic) {
    if (!pool) return;
    const deviceId = payload.device_id || 'device-01';
    try {
        await pool.query(
            `INSERT INTO devices (id, name, mqtt_topic, status, last_seen, updated_at)
             VALUES ($1, $1, $2, 'online', NOW(), NOW())
             ON CONFLICT (id) DO UPDATE SET status='online', last_seen=NOW(), updated_at=NOW()`,
            [deviceId, topic]
        );
        await pool.query(
            `INSERT INTO telemetry (
                device_id, lat, lng, speed, satellites, gps_fix,
                sensor_s1, sensor_s2, sensor_mag1, sensor_mag2,
                obd_rpm, obd_speed, obd_engine_load, obd_coolant_temp, obd_throttle,
                fuel_theft_detected, raw_payload
             ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)`,
            [
                deviceId,
                payload.lat   ?? null,
                payload.lng   ?? null,
                payload.speed ?? payload.obd?.speed ?? null,
                payload.sats  ?? null,
                payload.loc   ?? null,
                payload.s1    ?? null,
                payload.s2    ?? null,
                payload.mag1  ?? null,
                payload.mag2  ?? null,
                payload.obd?.rpm         ?? payload.rpm          ?? null,
                payload.obd?.speed       ?? payload.speed        ?? null,
                payload.obd?.engine_load ?? payload.engine_load  ?? null,
                payload.obd?.coolant_temp ?? payload.coolant_temp ?? null,
                payload.obd?.throttle    ?? payload.throttle     ?? null,
                payload.fuel?.theft_detected ?? false,
                JSON.stringify(payload),
            ]
        );
    } catch (err) {
        console.error('[DB] saveTelemetry error:', err.message);
    }
}

// ─── MQTT subscriber ─────────────────────────────────────────────────────────

function connectToHiveMQ() {
    const client = mqtt.connect(MQTT_BROKER_URL, {
        clientId: `sgu-backend-${Math.random().toString(16).slice(2, 8)}`,
        clean: true,
        reconnectPeriod: 5000,
    });

    client.on('connect', () => {
        console.log(`[MQTT] Connected to ${MQTT_BROKER_URL}`);
        client.subscribe(MQTT_TOPIC, err => {
            if (err) console.error('[MQTT] Subscribe error:', err.message);
            else console.log(`[MQTT] Subscribed to ${MQTT_TOPIC}`);
        });
    });

    client.on('message', async (topic, message) => {
        let payload;
        try { payload = JSON.parse(message.toString()); }
        catch { console.warn('[MQTT] Non-JSON message ignored'); return; }

        console.log('[MQTT DATA]', topic, JSON.stringify(payload));

        // Buffered (SD card) records are forwarded to the browser for display
        // in the Analyze page but must NOT be written to the live telemetry table —
        // they are historical readings with their own original_timestamp.
        if (payload.type !== 'buffered') {
            await saveTelemetry(payload, topic);
        }

        // Push to all connected dashboard SSE clients (frontend handles routing)
        broadcastSSE({ type: 'telemetry', topic, data: payload });

        // Forward to scale engine stream bus (fire-and-forget — never blocks MQTT)
        if (connector && payload.type !== 'buffered') {
            connector.forwardTelemetryToScaleEngine(payload, topic);
        }
    });

    client.on('error',      err => console.error('[MQTT] Error:', err.message));
    client.on('offline',    ()  => console.warn('[MQTT] Offline — will reconnect'));
    client.on('reconnect',  ()  => console.log('[MQTT] Reconnecting...'));
}

// ─── SSE broadcast ────────────────────────────────────────────────────────────

function broadcastSSE(payload) {
    const msg = `data: ${JSON.stringify(payload)}\n\n`;
    sseClients.forEach(res => res.write(msg));
}

// ─── Middleware ───────────────────────────────────────────────────────────────

app.use((req, res, next) => {
    res.setHeader('X-Creator', 'Made by Monzer · github.com/moonr5/Vision');
    next();
});
app.use(express.json());
app.use(express.static(path.join(__dirname)));

// ─── Routes ───────────────────────────────────────────────────────────────────

// Real-time SSE stream — browser connects once and receives live telemetry
app.get('/api/stream', (req, res) => {
    res.setHeader('Content-Type',  'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection',    'keep-alive');
    res.flushHeaders();

    sseClients.add(res);
    const heartbeat = setInterval(() => res.write(':ping\n\n'), 25000);
    req.on('close', () => { sseClients.delete(res); clearInterval(heartbeat); });
});

// Latest telemetry per device (from DB view)
app.get('/api/telemetry/latest', async (req, res) => {
    if (!pool) return res.json([]);
    try {
        const { rows } = await pool.query('SELECT * FROM v_device_latest_telemetry ORDER BY timestamp DESC');
        res.json(rows);
    } catch (err) { res.status(500).json({ error: err.message }); }
});

// Recent telemetry history for one device
app.get('/api/telemetry/:deviceId', async (req, res) => {
    if (!pool) return res.json([]);
    const limit = Math.min(parseInt(req.query.limit) || 100, 1000);
    try {
        const { rows } = await pool.query(
            'SELECT * FROM telemetry WHERE device_id=$1 ORDER BY timestamp DESC LIMIT $2',
            [req.params.deviceId, limit]
        );
        res.json(rows);
    } catch (err) { res.status(500).json({ error: err.message }); }
});

// Recent events
app.get('/api/events', async (req, res) => {
    if (!pool) return res.json([]);
    const limit = Math.min(parseInt(req.query.limit) || 50, 500);
    try {
        const { rows } = await pool.query(
            'SELECT * FROM events ORDER BY created_at DESC LIMIT $1', [limit]
        );
        res.json(rows);
    } catch (err) { res.status(500).json({ error: err.message }); }
});

// All devices with current status
app.get('/api/devices', async (req, res) => {
    if (!pool) return res.json([]);
    try {
        const { rows } = await pool.query('SELECT * FROM devices ORDER BY last_seen DESC NULLS LAST');
        res.json(rows);
    } catch (err) { res.status(500).json({ error: err.message }); }
});

// Proxy to Python AI backend
app.post('/api/ai/analyze', async (req, res) => {
    if (!PYTHON_AI_URL) return res.status(503).json({ error: 'Python AI backend not configured (set PYTHON_AI_URL)' });
    try {
        const upstream = await fetch(`${PYTHON_AI_URL}/api/ai/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(req.body),
            signal: AbortSignal.timeout(10000),
        });
        const data = await upstream.json();
        res.status(upstream.status).json(data);
    } catch { res.status(503).json({ error: 'Python AI backend unreachable' }); }
});

app.get('/health', async (req, res) => {
    const dbOk = pool ? await pool.query('SELECT 1').then(() => true).catch(() => false) : false;

    // Check upstream engine health (non-blocking — returns quickly even if engines are down)
    let routeEngine = null;
    let scaleEngine = null;
    if (connector) {
        try {
            routeEngine = await connector.upstreamFetch(
                connector.ROUTE_ENGINE_URL, '/health', { timeout: 2000 }
            );
        } catch {}
        try {
            scaleEngine = await connector.upstreamFetch(
                connector.SCALE_ENGINE_URL, '/health', { timeout: 2000 }
            );
        } catch {}
    }

    res.json({
        status: 'ok',
        db: pool ? (dbOk ? 'connected' : 'error') : 'not configured',
        mqtt_topic: MQTT_TOPIC,
        sse_clients: sseClients.size,
        engines: {
            ai_backend: PYTHON_AI_URL ? (connector ? (await connector.upstreamFetch(PYTHON_AI_URL, '/health', { timeout: 2000 }).catch(() => null)) : null) : 'not configured',
            route_engine: ROUTE_ENGINE_URL ? (routeEngine || 'unreachable') : 'not configured',
            scale_engine: SCALE_ENGINE_URL ? (scaleEngine || 'unreachable') : 'not configured',
        },
        timestamp: new Date().toISOString(),
    });
});

app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'login.html')));

// ─── Route Engine proxy (AI-driven route optimization + behaviour analysis) ───

app.post('/api/route/analyze',      (req, res) => connector?.proxyToRouteEngine(req, res, '/api/route/analyze'));
app.post('/api/route/compare',      (req, res) => connector?.proxyToRouteEngine(req, res, '/api/route/compare'));
app.post('/api/route/driver-match', (req, res) => connector?.proxyToRouteEngine(req, res, '/api/route/driver-match'));
app.post('/api/route/report',       (req, res) => connector?.proxyToRouteEngine(req, res, '/api/route/report'));
app.get('/api/route/driver/:id',    (req, res) => connector?.proxyGetToRouteEngine(req, res, `/api/route/driver/${req.params.id}`));
app.get('/api/route/drivers',       (req, res) => connector?.proxyGetToRouteEngine(req, res, '/api/route/drivers'));

// ─── Scale Engine proxy (28-engine intelligence platform) ────────────────────

// Data ingestion
app.post('/api/stream/publish',       (req, res) => connector?.proxyToScaleEngine(req, res, '/api/stream/publish'));
app.get('/api/stream/stats',          (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/stream/stats'));
app.get('/api/timeseries/device/:id', (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/timeseries/device/${req.params.id}?${new URLSearchParams(req.query)}`));
app.get('/api/timeseries/fleet',      (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/timeseries/fleet'));
app.get('/api/storage/stats',         (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/storage/stats'));
app.post('/api/schema/validate',     (req, res) => connector?.proxyToScaleEngine(req, res, '/api/schema/validate'));
app.get('/api/schema/violations',    (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/schema/violations'));
app.post('/api/normalize',           (req, res) => connector?.proxyToScaleEngine(req, res, '/api/normalize'));
app.post('/api/geo/point-in-fence',  (req, res) => connector?.proxyToScaleEngine(req, res, '/api/geo/point-in-fence'));
app.post('/api/geo/corridor',        (req, res) => connector?.proxyToScaleEngine(req, res, '/api/geo/corridor'));
app.get('/api/fleet/state',          (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/fleet/state'));
app.get('/api/fleet/state/device/:id',(req,res) => connector?.proxyGetToScaleEngine(req, res, `/api/fleet/state/device/${req.params.id}`));
app.get('/api/quality/check',        (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/quality/check'));
app.get('/api/quality/device/:id',   (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/quality/device/${req.params.id}`));
app.get('/api/quality/fleet',        (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/quality/fleet'));
app.post('/api/replay/start',        (req, res) => connector?.proxyToScaleEngine(req, res, '/api/replay/start'));
app.get('/api/replay/status',        (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/replay/status'));

// Smart systems
app.post('/api/cep/ingest',          (req, res) => connector?.proxyToScaleEngine(req, res, '/api/cep/ingest'));
app.get('/api/cep/alerts',           (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/cep/alerts'));
app.post('/api/anomaly/detect',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/anomaly/detect'));
app.get('/api/anomaly/baseline/:id', (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/anomaly/baseline/${req.params.id}`));
app.post('/api/twin/update',         (req, res) => connector?.proxyToScaleEngine(req, res, '/api/twin/update'));
app.get('/api/twin/:device_id',      (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/twin/${req.params.device_id}`));
app.get('/api/twin/fleet',           (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/twin/fleet'));
app.post('/api/maintenance/predict', (req, res) => connector?.proxyToScaleEngine(req, res, '/api/maintenance/predict'));
app.get('/api/maintenance/fleet',    (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/maintenance/fleet'));
app.post('/api/behavior/score',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/behavior/score'));
app.get('/api/behavior/compare/:id', (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/behavior/compare/${req.params.id}`));
app.post('/api/eta/compute',         (req, res) => connector?.proxyToScaleEngine(req, res, '/api/eta/compute'));
app.post('/api/optimize/driver-match',(req,res)=> connector?.proxyToScaleEngine(req, res, '/api/optimize/driver-match'));
app.post('/api/optimize/load-balance',(req,res)=> connector?.proxyToScaleEngine(req, res, '/api/optimize/load-balance'));
app.get('/api/optimize/kpis',        (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/optimize/kpis'));
app.post('/api/fusion/ingest',       (req, res) => connector?.proxyToScaleEngine(req, res, '/api/fusion/ingest'));
app.get('/api/fusion/decisions',     (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/fusion/decisions'));

// AI/ML
app.post('/api/features/compute',     (req, res) => connector?.proxyToScaleEngine(req, res, '/api/features/compute'));
app.get('/api/features/:type/:id',   (req, res) => connector?.proxyGetToScaleEngine(req, res, `/api/features/${req.params.type}/${req.params.id}`));
app.post('/api/rag/index',           (req, res) => connector?.proxyToScaleEngine(req, res, '/api/rag/index'));
app.post('/api/rag/search',          (req, res) => connector?.proxyToScaleEngine(req, res, '/api/rag/search'));
app.post('/api/models/train',        (req, res) => connector?.proxyToScaleEngine(req, res, '/api/models/train'));
app.post('/api/models/predict',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/models/predict'));
app.get('/api/models/list',          (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/models/list'));
app.get('/api/mlops/drift',          (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/mlops/drift'));
app.post('/api/mlops/rollback',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/mlops/rollback'));
app.post('/api/orchestrator/run',    (req, res) => connector?.proxyToScaleEngine(req, res, '/api/orchestrator/run'));
app.post('/api/forecast/fuel',       (req, res) => connector?.proxyToScaleEngine(req, res, '/api/forecast/fuel'));
app.post('/api/forecast/delay',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/forecast/delay'));
app.post('/api/graph/add-node',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/graph/add-node'));
app.get('/api/graph/related',        (req, res) => connector?.proxyGetToScaleEngine(req, res, '/api/graph/related'));

// Edge-cloud bridge
app.post('/api/edge/models/create',  (req, res) => connector?.proxyToScaleEngine(req, res, '/api/edge/models/create'));
app.post('/api/edge/models/rollout', (req, res) => connector?.proxyToScaleEngine(req, res, '/api/edge/models/rollout'));
app.post('/api/edge/models/confirm', (req, res) => connector?.proxyToScaleEngine(req, res, '/api/edge/models/confirm'));
app.post('/api/sync/push-to-edge',   (req, res) => connector?.proxyToScaleEngine(req, res, '/api/sync/push-to-edge'));
app.post('/api/sync/ingest-from-edge',(req,res)=> connector?.proxyToScaleEngine(req, res, '/api/sync/ingest-from-edge'));
app.post('/api/fl/start-round',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/fl/start-round'));
app.post('/api/fl/submit-update',    (req, res) => connector?.proxyToScaleEngine(req, res, '/api/fl/submit-update'));
app.post('/api/fl/aggregate',        (req, res) => connector?.proxyToScaleEngine(req, res, '/api/fl/aggregate'));

// System analyzer
app.post('/api/system/analyze',      (req, res) => connector?.proxyToScaleEngine(req, res, '/api/system/analyze'));

// ─── Start ────────────────────────────────────────────────────────────────────

async function start() {
    await runMigrations();
    connectToHiveMQ();
    app.listen(PORT, () => {
        console.log('');
        console.log('╔══════════════════════════════════════════════════════════╗');
        console.log('║  SGU Logistics & Telemetry Dashboard                   ║');
        console.log('║  Made by Monzer · github.com/moonr5/Vision             ║');
        console.log('╚══════════════════════════════════════════════════════════╝');
        console.log(`[Server] Running on port ${PORT}`);
    });
}

start();
