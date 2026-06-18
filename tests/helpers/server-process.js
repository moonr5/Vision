/**
 * Spawns server.js as a child process for integration tests.
 * No changes to server.js required.
 */
const { spawn } = require('child_process');
const path = require('path');

function waitForHealth(port, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;

    return new Promise((resolve, reject) => {
        const poll = async () => {
            try {
                const res = await fetch(`http://127.0.0.1:${port}/health`);
                if (res.ok) return resolve();
            } catch (_) {
                // server still starting
            }

            if (Date.now() > deadline) {
                return reject(new Error(`Server on port ${port} did not become healthy in time`));
            }
            setTimeout(poll, 250);
        };
        poll();
    });
}

async function startTestServer() {
    const port = 48000 + Math.floor(Math.random() * 1000);
    const projectRoot = path.join(__dirname, '../..');

    const child = spawn(process.execPath, ['server.js'], {
        cwd: projectRoot,
        env: {
            ...process.env,
            PORT: String(port),
            DATABASE_URL: '',
            PYTHON_AI_URL: '',
            MQTT_BROKER_URL: 'mqtt://127.0.0.1:9',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
    });

    child.stdout.on('data', () => {});
    child.stderr.on('data', () => {});

    try {
        await waitForHealth(port);
    } catch (err) {
        child.kill();
        throw err;
    }

    return {
        port,
        baseUrl: `http://127.0.0.1:${port}`,
        stop: () =>
            new Promise((resolve) => {
                if (child.killed) return resolve();
                child.once('exit', () => resolve());
                child.kill('SIGTERM');
                setTimeout(() => {
                    if (!child.killed) child.kill('SIGKILL');
                }, 3000);
            }),
    };
}

module.exports = { startTestServer };
