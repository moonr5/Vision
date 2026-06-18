const fs = require('fs');
const path = require('path');
const { startTestServer } = require('./helpers/server-process');

describe('server.js integration', () => {
    let server;

    beforeAll(async () => {
        server = await startTestServer();
    }, 30000);

    afterAll(async () => {
        if (server) await server.stop();
    });

    test('GET /health returns ok without database', async () => {
        const res = await fetch(`${server.baseUrl}/health`);
        expect(res.status).toBe(200);

        const body = await res.json();
        expect(body.status).toBe('ok');
        expect(body.db).toBe('not configured');
        expect(body.mqtt_topic).toBe('monztrack/device01/gps');
        expect(typeof body.sse_clients).toBe('number');
        expect(body.timestamp).toBeDefined();
    });

    test('GET /api/telemetry/latest returns empty array without database', async () => {
        const res = await fetch(`${server.baseUrl}/api/telemetry/latest`);
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual([]);
    });

    test('GET /api/devices returns empty array without database', async () => {
        const res = await fetch(`${server.baseUrl}/api/devices`);
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual([]);
    });

    test('GET /api/events returns empty array without database', async () => {
        const res = await fetch(`${server.baseUrl}/api/events`);
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual([]);
    });

    test('GET /api/telemetry/:deviceId respects limit cap', async () => {
        const res = await fetch(`${server.baseUrl}/api/telemetry/device-01?limit=5000`);
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual([]);
    });

    test('POST /api/ai/analyze returns 503 when PYTHON_AI_URL is unset', async () => {
        const res = await fetch(`${server.baseUrl}/api/ai/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: 'How is the fleet?' }),
        });
        expect(res.status).toBe(503);

        const body = await res.json();
        expect(body.error).toMatch(/Python AI backend not configured/i);
    });

    test('GET / serves login.html', async () => {
        const res = await fetch(`${server.baseUrl}/`);
        expect(res.status).toBe(200);
        expect(res.headers.get('content-type')).toMatch(/html/i);

        const html = await res.text();
        expect(html).toContain('login');
    });

    test('pg-schema.sql exists for migrations', () => {
        const schemaPath = path.join(__dirname, '../database/pg-schema.sql');
        expect(fs.existsSync(schemaPath)).toBe(true);
        expect(fs.readFileSync(schemaPath, 'utf8')).toContain('CREATE TABLE');
    });
});
