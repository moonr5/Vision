/**
 * Smart AI Engine for Browser
 * Direct database access with instant answers
 */

class SmartAIEngine {
    constructor() {
        this.cache = null;
        this.cacheTime = null;
    }

    // Always reads the live SGUDatabase reference — never captured at construction
    get db() {
        return window.SGUDatabase;
    }

    isReady() {
        return this.db?.isReady?.() === true;
    }

    /**
     * Get fresh data from database
     */
    async getData() {
        if (this.cache && this.cacheTime && (Date.now() - this.cacheTime < 10000)) {
            return this.cache;
        }

        const data = {
            drivers: { total: 0, active: 0, list: [], avgScore: null },
            orders:  { total: 0, active: 0, list: [] },
            devices: { total: 0, online: 0 },
            events:  { total: 0, critical: 0, warnings: 0 },
            alerts: [],
            recentEvents: [],
            topDrivers: [],
            bottomDrivers: []
        };

        if (!this.isReady()) return data;

        try {
            // Drivers
            data.drivers.total  = this.db.query('SELECT COUNT(*) as c FROM drivers')[0]?.c || 0;
            data.drivers.active = this.db.query("SELECT COUNT(*) as c FROM drivers WHERE status = 'active'")[0]?.c || 0;
            data.drivers.list   = this.db.query(`
                SELECT id, name, safety_score, vehicle_plate, status, phone
                FROM drivers ORDER BY safety_score DESC
            `) || [];

            const scores = data.drivers.list.map(d => d.safety_score).filter(s => s != null);
            if (scores.length > 0) {
                data.drivers.avgScore = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
            }

            data.topDrivers    = data.drivers.list.filter(d => d.safety_score >= 85).slice(0, 5);
            data.bottomDrivers = data.drivers.list.filter(d => d.safety_score != null && d.safety_score < 70).slice(-5);

            // Orders
            data.orders.total  = this.db.query('SELECT COUNT(*) as c FROM orders')[0]?.c || 0;
            data.orders.active = this.db.query("SELECT COUNT(*) as c FROM orders WHERE status NOT IN ('completed','cancelled')")[0]?.c || 0;
            data.orders.list   = this.db.query(`
                SELECT order_id, customer_name, status, driver_id, created_at
                FROM orders ORDER BY created_at DESC LIMIT 10
            `) || [];

            // Devices
            data.devices.total  = this.db.query('SELECT COUNT(*) as c FROM devices')[0]?.c || 0;
            data.devices.online = this.db.query("SELECT COUNT(*) as c FROM devices WHERE status = 'online'")[0]?.c || 0;

            // Events
            data.events.total    = this.db.query('SELECT COUNT(*) as c FROM events')[0]?.c || 0;
            data.events.critical = this.db.query("SELECT COUNT(*) as c FROM events WHERE type = 'CRITICAL'")[0]?.c || 0;
            data.events.warnings = this.db.query("SELECT COUNT(*) as c FROM events WHERE type = 'WARNING'")[0]?.c || 0;

            // Active unacknowledged alerts
            data.alerts = this.db.query(`
                SELECT type, event, device_id, driver_id, created_at
                FROM events
                WHERE acknowledged = 0 AND type IN ('WARNING','CRITICAL')
                ORDER BY CASE type WHEN 'CRITICAL' THEN 1 ELSE 2 END, created_at DESC
                LIMIT 10
            `) || [];

            // Recent events log
            data.recentEvents = this.db.query(`
                SELECT type, event, details, created_at
                FROM events ORDER BY created_at DESC LIMIT 10
            `) || [];

            this.cache     = data;
            this.cacheTime = Date.now();

        } catch (e) {
            console.error('[AI Engine] Data fetch error:', e);
        }

        return data;
    }

    /**
     * Answer user question directly from database (no API call needed)
     * Returns null for questions that need Gemini
     */
    async answer(question) {
        if (!this.isReady()) return null;

        const q    = question.toLowerCase().trim();
        const data = await this.getData();

        // ── Drivers ──────────────────────────────────────────────────────────
        if (/how many driver|number of driver|driver count|total driver/.test(q)) {
            if (data.drivers.total === 0) return "No drivers in the system yet.";
            return `${data.drivers.total} drivers total — ${data.drivers.active} active, ${data.drivers.total - data.drivers.active} inactive.`;
        }

        if (/inactive|not active/.test(q) && /driver/.test(q)) {
            const inactive = data.drivers.total - data.drivers.active;
            return `${inactive} inactive driver${inactive !== 1 ? 's' : ''} out of ${data.drivers.total} total.`;
        }

        if (/list.*driver|all driver|show.*driver/.test(q)) {
            if (data.drivers.list.length === 0) return "No drivers in the system.";
            return "All drivers:\n" + data.drivers.list.map(d =>
                `• ${d.name} (${d.status}): score ${d.safety_score ?? 'N/A'}, plate ${d.vehicle_plate || 'N/A'}`
            ).join('\n');
        }

        if (/best driver|top driver|highest score|best performer/.test(q)) {
            if (data.topDrivers.length === 0) {
                return data.drivers.list.length > 0
                    ? `No drivers above 85 yet. Best: ${data.drivers.list[0]?.name} at ${data.drivers.list[0]?.safety_score}.`
                    : "No driver data available.";
            }
            return "Top drivers:\n" + data.topDrivers.map(d => `• ${d.name}: ${d.safety_score}`).join('\n');
        }

        if (/worst driver|needs? improvement|lowest score|poor driver|bad driver/.test(q)) {
            if (data.bottomDrivers.length === 0) return "No drivers with scores below 70. All performing well.";
            return "Drivers needing improvement:\n" + data.bottomDrivers.map(d => `• ${d.name}: ${d.safety_score}`).join('\n');
        }

        if (/average.*score|safety score.*average|avg.*score|mean.*score/.test(q)) {
            if (!data.drivers.avgScore) return "No safety score data yet.";
            return `Average safety score: ${data.drivers.avgScore} across ${data.drivers.total} driver${data.drivers.total !== 1 ? 's' : ''}.`;
        }

        // Named driver lookup
        const namedDriver = data.drivers.list.find(d =>
            d.name && d.name.split(' ').some(part => q.includes(part.toLowerCase()))
        );
        if (namedDriver) {
            return `${namedDriver.name}: safety score ${namedDriver.safety_score ?? 'N/A'}, status ${namedDriver.status}, plate ${namedDriver.vehicle_plate || 'N/A'}.`;
        }

        // ── Orders ───────────────────────────────────────────────────────────
        if (/how many order|order count|number of order|total order/.test(q)) {
            return `${data.orders.active} active order${data.orders.active !== 1 ? 's' : ''} (${data.orders.total} total).`;
        }

        if (/completed order|finished order/.test(q)) {
            const done = data.orders.total - data.orders.active;
            return `${done} completed order${done !== 1 ? 's' : ''} out of ${data.orders.total} total.`;
        }

        if (/list.*order|recent.*order|show.*order/.test(q)) {
            if (data.orders.list.length === 0) return "No orders in the system.";
            return "Recent orders:\n" + data.orders.list.slice(0, 5).map(o =>
                `• ${o.order_id}: ${o.customer_name || 'Unknown'} — ${o.status}`
            ).join('\n');
        }

        // ── Devices ──────────────────────────────────────────────────────────
        if (/how many device|device count|total device/.test(q)) {
            return `${data.devices.online} online device${data.devices.online !== 1 ? 's' : ''} out of ${data.devices.total} total.`;
        }

        if (/offline device|disconnected device/.test(q)) {
            const off = data.devices.total - data.devices.online;
            return `${off} offline device${off !== 1 ? 's' : ''}.`;
        }

        // ── Events / Alerts ──────────────────────────────────────────────────
        if (/how many event|event count|total event/.test(q)) {
            return `${data.events.total} total events — ${data.events.critical} critical, ${data.events.warnings} warnings.`;
        }

        if (/any alert|active alert|unread alert|pending alert|open alert/.test(q)) {
            if (data.alerts.length === 0) return "No active alerts. All clear.";
            const crit = data.alerts.filter(a => a.type === 'CRITICAL');
            const warn = data.alerts.filter(a => a.type === 'WARNING');
            let out = '';
            if (crit.length) out += `${crit.length} CRITICAL: ${crit.slice(0, 2).map(a => a.event).join(', ')}.`;
            if (warn.length) out += (out ? ' ' : '') + `${warn.length} warning${warn.length !== 1 ? 's' : ''}.`;
            return out;
        }

        if (/recent event|latest event|what happened|last event/.test(q)) {
            if (!data.recentEvents.length) return "No recent events logged.";
            return "Recent events:\n" + data.recentEvents.slice(0, 5).map(e =>
                `• [${e.type}] ${e.event}`
            ).join('\n');
        }

        // ── Overview ─────────────────────────────────────────────────────────
        if (/status|overview|summary|how.*system|fleet status|everything ok/.test(q)) {
            if (data.drivers.total === 0) return "System running but no fleet data recorded yet.";
            return `Fleet: ${data.drivers.active}/${data.drivers.total} drivers active | ` +
                   `${data.orders.active} active orders | ` +
                   `${data.devices.online}/${data.devices.total} devices online | ` +
                   `${data.alerts.length} active alert${data.alerts.length !== 1 ? 's' : ''} | ` +
                   `avg score: ${data.drivers.avgScore ?? 'N/A'}`;
        }

        // Not matched — hand off to Gemini
        return null;
    }

    /**
     * Build the fleet/database portion of the Gemini system prompt.
     * Live vehicle data (GPS, sensors) is added by getAiResponse() in index.html
     * because those variables live in a different script scope.
     */
    async buildDbContext() {
        const data = await this.getData();

        const driverLines = data.drivers.list.length
            ? data.drivers.list.map(d =>
                `  • ${d.name} (${d.status}): score ${d.safety_score ?? 'N/A'}, plate ${d.vehicle_plate || 'N/A'}`
              ).join('\n')
            : '  (none)';

        const orderLines = data.orders.list.length
            ? data.orders.list.slice(0, 5).map(o =>
                `  • ${o.order_id}: ${o.customer_name || 'Unknown'} — ${o.status}`
              ).join('\n')
            : '  (none)';

        const alertLines = data.alerts.length
            ? data.alerts.slice(0, 5).map(a => `  • [${a.type}] ${a.event}`).join('\n')
            : '  (none)';

        const topLines = data.topDrivers.length
            ? data.topDrivers.map(d => `  • ${d.name}: ${d.safety_score}`).join('\n')
            : '  (none above 85)';

        const lowLines = data.bottomDrivers.length
            ? data.bottomDrivers.map(d => `  • ${d.name}: ${d.safety_score}`).join('\n')
            : '  (none below 70)';

        return `FLEET DATABASE SNAPSHOT:
Drivers : ${data.drivers.active} active / ${data.drivers.total} total | avg safety score: ${data.drivers.avgScore ?? 'N/A'}
Orders  : ${data.orders.active} active / ${data.orders.total} total
Devices : ${data.devices.online} online / ${data.devices.total} total
Events  : ${data.events.total} total | ${data.events.critical} critical | ${data.events.warnings} warnings
Alerts  : ${data.alerts.length} active unacknowledged

ALL DRIVERS:
${driverLines}

TOP PERFORMERS (score ≥ 85):
${topLines}

NEEDS ATTENTION (score < 70):
${lowLines}

RECENT ORDERS:
${orderLines}

ACTIVE ALERTS:
${alertLines}`;
    }
}

// Initialize
window.SmartAIEngine = SmartAIEngine;
window.smartAI = new SmartAIEngine();
console.log('[AI Engine] Initialized');
