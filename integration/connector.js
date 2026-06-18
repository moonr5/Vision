/**
 * SGU Logistics — Integration Connector
 * Made by Monzer · github.com/moonr5/Vision
 *
 * Bridges the existing Node.js backend (server.js) with the new backend engines
 * (route_engine and scale_engine) through HTTP API calls.
 *
 * DESIGN PRINCIPLES:
 *   1. Zero disruption — every call wraps existing functionality, never replaces it.
 *   2. Graceful degradation — if a downstream service is unavailable, the caller
 *      gets a clean error response but the main system continues operating.
 *   3. Fire-and-forget for telemetry — MQTT data is forwarded to the scale engine
 *      asynchronously; failures are logged but never block the main MQTT pipeline.
 *   4. All engine URLs are configurable via environment variables.
 *
 * SERVICES:
 *   ROUTE_ENGINE_URL  — Route optimization & behaviour analysis  (Python/FastAPI)
 *   SCALE_ENGINE_URL  — 28-engine intelligence platform          (Python/FastAPI)
 *   PYTHON_AI_URL     — Existing Gemini AI + Telegram bot        (Python/FastAPI)
 */

const ROUTE_ENGINE_URL = process.env.ROUTE_ENGINE_URL || null;
const SCALE_ENGINE_URL = process.env.SCALE_ENGINE_URL || null;

// Default timeout for upstream service calls
const UPSTREAM_TIMEOUT_MS = 15000;

// Track connection health
const health = {
  routeEngine: { reachable: false, lastCheck: null },
  scaleEngine: { reachable: false, lastCheck: null },
};

// ── HTTP helper ─────────────────────────────────────────────────────────────

async function upstreamFetch(baseUrl, path, options = {}) {
  if (!baseUrl) return null;

  const url = `${baseUrl.replace(/\/+$/, '')}${path}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || UPSTREAM_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Upstream ${res.status}: ${text.slice(0, 200)}`);
    }

    return await res.json();
  } catch (err) {
    if (err.name === 'AbortError') {
      console.warn(`[Connector] Timeout: ${url}`);
    } else if (err.code === 'ECONNREFUSED' || err.code === 'ECONNRESET') {
      // Connection refused — service not running (expected during dev)
    } else {
      console.warn(`[Connector] Upstream error (${url}): ${err.message}`);
    }
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

// ── Health checks ───────────────────────────────────────────────────────────

async function checkHealth() {
  if (ROUTE_ENGINE_URL) {
    const result = await upstreamFetch(ROUTE_ENGINE_URL, '/health', { timeout: 3000 });
    health.routeEngine.reachable = result !== null;
    health.routeEngine.lastCheck = new Date().toISOString();
  }

  if (SCALE_ENGINE_URL) {
    const result = await upstreamFetch(SCALE_ENGINE_URL, '/health', { timeout: 3000 });
    health.scaleEngine.reachable = result !== null;
    health.scaleEngine.lastCheck = new Date().toISOString();
  }

  return health;
}

// ── Route Engine proxy ─────────────────────────────────────────────────────

/**
 * Proxy a request to the Route Engine.
 * Used by Express route handlers in server.js.
 */
async function proxyToRouteEngine(req, res, path, method = 'POST') {
  if (!ROUTE_ENGINE_URL) {
    return res.status(503).json({ error: 'Route engine not configured (set ROUTE_ENGINE_URL)' });
  }

  try {
    const result = await upstreamFetch(ROUTE_ENGINE_URL, path, {
      method,
      body: JSON.stringify(req.body),
      timeout: 20000,
    });

    if (result === null) {
      return res.status(503).json({ error: 'Route engine unreachable' });
    }

    return res.json(result);
  } catch (err) {
    return res.status(503).json({ error: 'Route engine error: ' + err.message });
  }
}

// ── Scale Engine proxy ─────────────────────────────────────────────────────

/**
 * Proxy a request to the Scale Engine.
 */
async function proxyToScaleEngine(req, res, path, method = 'POST') {
  if (!SCALE_ENGINE_URL) {
    return res.status(503).json({ error: 'Scale engine not configured (set SCALE_ENGINE_URL)' });
  }

  try {
    const result = await upstreamFetch(SCALE_ENGINE_URL, path, {
      method,
      body: JSON.stringify(req.body),
      timeout: 20000,
    });

    if (result === null) {
      return res.status(503).json({ error: 'Scale engine unreachable' });
    }

    return res.json(result);
  } catch (err) {
    return res.status(503).json({ error: 'Scale engine error: ' + err.message });
  }
}

// ── GET proxy helpers ──────────────────────────────────────────────────────

async function proxyGetToRouteEngine(req, res, path) {
  if (!ROUTE_ENGINE_URL) {
    return res.status(503).json({ error: 'Route engine not configured' });
  }

  try {
    // Forward query params
    const query = new URLSearchParams(req.query).toString();
    const fullPath = query ? `${path}?${query}` : path;

    const result = await upstreamFetch(ROUTE_ENGINE_URL, fullPath, {
      method: 'GET',
      timeout: 15000,
    });

    if (result === null) {
      return res.status(503).json({ error: 'Route engine unreachable' });
    }

    return res.json(result);
  } catch (err) {
    return res.status(503).json({ error: 'Route engine error: ' + err.message });
  }
}

async function proxyGetToScaleEngine(req, res, path) {
  if (!SCALE_ENGINE_URL) {
    return res.status(503).json({ error: 'Scale engine not configured' });
  }

  try {
    const query = new URLSearchParams(req.query).toString();
    const fullPath = query ? `${path}?${query}` : path;

    const result = await upstreamFetch(SCALE_ENGINE_URL, fullPath, {
      method: 'GET',
      timeout: 15000,
    });

    if (result === null) {
      return res.status(503).json({ error: 'Scale engine unreachable' });
    }

    return res.json(result);
  } catch (err) {
    return res.status(503).json({ error: 'Scale engine error: ' + err.message });
  }
}

// ── Telemetry forwarding ────────────────────────────────────────────────────

/**
 * Forward MQTT telemetry to the Scale Engine's stream bus.
 * This is fire-and-forget — failures are logged but never block the MQTT pipeline.
 * Called from within the existing MQTT on('message') handler in server.js.
 */
function forwardTelemetryToScaleEngine(payload, topic) {
  if (!SCALE_ENGINE_URL) return;

  // Fire and forget — don't await
  upstreamFetch(SCALE_ENGINE_URL, '/api/stream/publish', {
    method: 'POST',
    body: JSON.stringify({
      device_id: payload.device_id || 'device-01',
      ...payload,
      _forwarded_from: 'server.js',
      _mqtt_topic: topic,
      _forwarded_at: new Date().toISOString(),
    }),
    timeout: 3000,
  }).catch(() => {
    // Silent — scale engine may be offline during development
  });
}

// ── Periodic health check ──────────────────────────────────────────────────

// Check upstream health every 60 seconds
setInterval(checkHealth, 60000);
// Initial check after 5 seconds (wait for services to start)
setTimeout(checkHealth, 5000);

// ── Export ──────────────────────────────────────────────────────────────────

module.exports = {
  // URLs (read-only)
  ROUTE_ENGINE_URL,
  SCALE_ENGINE_URL,

  // Health
  health,
  checkHealth,

  // Proxy functions (used by server.js route handlers)
  proxyToRouteEngine,
  proxyToScaleEngine,
  proxyGetToRouteEngine,
  proxyGetToScaleEngine,

  // Telemetry forwarding (used by MQTT message handler)
  forwardTelemetryToScaleEngine,

  // Raw upstream fetch (for custom calls)
  upstreamFetch,
};
