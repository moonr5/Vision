require('dotenv').config();
const express = require('express');
const path = require('path');
const { Pool } = require('pg');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;
const PYTHON_AI_URL = process.env.PYTHON_AI_URL || null;

// PostgreSQL connection pool
const pool = process.env.DATABASE_URL
    ? new Pool({
        connectionString: process.env.DATABASE_URL,
        ssl: { rejectUnauthorized: false }
    })
    : null;

async function runMigrations() {
    if (!pool) {
        console.warn('[DB] DATABASE_URL not set — skipping database setup');
        return;
    }
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

app.use(express.json());

// Proxy /api/ai/* to the Python AI backend when PYTHON_AI_URL is set
app.post('/api/ai/analyze', async (req, res) => {
    if (!PYTHON_AI_URL) {
        return res.status(503).json({ error: 'Python AI backend not configured (set PYTHON_AI_URL)' });
    }
    try {
        const upstream = await fetch(`${PYTHON_AI_URL}/api/ai/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(req.body),
            signal: AbortSignal.timeout(10000)
        });
        const data = await upstream.json();
        res.status(upstream.status).json(data);
    } catch (err) {
        res.status(503).json({ error: 'Python AI backend unreachable' });
    }
});

// Serve all project files as static assets (dotfiles excluded by default)
app.use(express.static(path.join(__dirname)));

app.get('/health', async (req, res) => {
    const dbOk = pool ? await pool.query('SELECT 1').then(() => true).catch(() => false) : false;
    res.json({ status: 'ok', db: pool ? (dbOk ? 'connected' : 'error') : 'not configured', timestamp: new Date().toISOString() });
});

// Serve login.html as the default entry point
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'login.html'));
});

async function start() {
    await runMigrations();
    app.listen(PORT, () => {
        console.log(`[Server] Running on port ${PORT}`);
    });
}

start();
