require('dotenv').config();
const express = require('express');
const path = require('path');
const { Pool } = require('pg');
const fs = require('fs');
const mqtt = require('mqtt');

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

        // Buffered (SD card) records are forwarded to the browser for display
        // in the Analyze page but must NOT be written to the live telemetry table —
        // they are historical readings with their own original_timestamp.
        if (payload.type !== 'buffered') {
            await saveTelemetry(payload, topic);
        }

        // Push to all connected dashboard SSE clients (frontend handles routing)
        broadcastSSE({ type: 'telemetry', topic, data: payload });
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
    res.json({
        status: 'ok',
        db: pool ? (dbOk ? 'connected' : 'error') : 'not configured',
        mqtt_topic: MQTT_TOPIC,
        sse_clients: sseClients.size,
        timestamp: new Date().toISOString(),
    });
});

app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'login.html')));

// ─── Start ────────────────────────────────────────────────────────────────────

async function start() {
    await runMigrations();
    connectToHiveMQ();
    app.listen(PORT, () => console.log(`[Server] Running on port ${PORT}`));
}

start();
