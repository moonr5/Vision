/**
 * Smart AI Engine for Browser
 * Direct database access with instant answers
 */

class SmartAIEngine {
    constructor() {
        this.db = window.SGUDatabase;
        this.cache = null;
        this.cacheTime = null;
    }

    isReady() {
        return this.db?.isReady();
    }

    /**
     * Get fresh data from database
     */
    async getData() {
        // Use cache if recent (< 10 seconds)
        if (this.cache && this.cacheTime && (Date.now() - this.cacheTime < 10000)) {
            return this.cache;
        }

        const data = {
            drivers: { total: 0, active: 0, list: [], avgScore: null },
            orders: { total: 0, active: 0 },
            devices: { total: 0, online: 0 },
            events: { total: 0, critical: 0, warnings: 0 },
            alerts: [],
            topDrivers: [],
            bottomDrivers: []
        };

        try {
            // Driver counts
            const driverTotal = this.db.query('SELECT COUNT(*) as c FROM drivers');
            data.drivers.total = driverTotal[0]?.c || 0;
            
            const driverActive = this.db.query("SELECT COUNT(*) as c FROM drivers WHERE status = 'active'");
            data.drivers.active = driverActive[0]?.c || 0;

            // Driver list with scores
            data.drivers.list = this.db.query(`
                SELECT id, name, safety_score, vehicle_plate, status
                FROM drivers
                ORDER BY safety_score DESC
            `) || [];

            // Calculate average
            const scores = data.drivers.list
                .map(d => d.safety_score)
                .filter(s => s !== null && s !== undefined);
            if (scores.length > 0) {
                data.drivers.avgScore = Math.round(
                    scores.reduce((a, b) => a + b, 0) / scores.length
                );
            }

            // Top and bottom drivers
            data.topDrivers = data.drivers.list
                .filter(d => d.safety_score >= 85)
                .slice(0, 3);
            
            data.bottomDrivers = data.drivers.list
                .filter(d => d.safety_score !== null && d.safety_score < 70)
                .slice(-3);

            // Orders
            const orderTotal = this.db.query('SELECT COUNT(*) as c FROM orders');
            data.orders.total = orderTotal[0]?.c || 0;
            
            const orderActive = this.db.query("SELECT COUNT(*) as c FROM orders WHERE status NOT IN ('completed', 'cancelled')");
            data.orders.active = orderActive[0]?.c || 0;

            // Devices
            const deviceTotal = this.db.query('SELECT COUNT(*) as c FROM devices');
            data.devices.total = deviceTotal[0]?.c || 0;
            
            const deviceOnline = this.db.query("SELECT COUNT(*) as c FROM devices WHERE status = 'online'");
            data.devices.online = deviceOnline[0]?.c || 0;

            // Events
            const eventTotal = this.db.query('SELECT COUNT(*) as c FROM events');
            data.events.total = eventTotal[0]?.c || 0;
            
            const eventCritical = this.db.query("SELECT COUNT(*) as c FROM events WHERE type = 'CRITICAL'");
            data.events.critical = eventCritical[0]?.c || 0;

            // Active alerts
            data.alerts = this.db.query(`
                SELECT type, event, device_id, driver_id, created_at
                FROM events
                WHERE acknowledged = 0
                AND type IN ('WARNING', 'CRITICAL')
                ORDER BY 
                    CASE type WHEN 'CRITICAL' THEN 1 ELSE 2 END,
                    created_at DESC
                LIMIT 5
            `) || [];

            this.cache = data;
            this.cacheTime = Date.now();

        } catch (e) {
            console.error('[AI Engine] Data fetch error:', e);
        }

        return data;
    }

    /**
     * Answer user question with real data
     */
    async answer(question) {
        const q = question.toLowerCase().trim();
        const data = await this.getData();

        // Driver count questions
        if (q.match(/how many driver|number of driver|driver count/)) {
            if (data.drivers.total === 0) return "No drivers in the system yet.";
            return `${data.drivers.active} active drivers (total: ${data.drivers.total})`;
        }

        if (q.match(/inactive driver|offline driver|not active/)) {
            const inactive = data.drivers.total - data.drivers.active;
            return `${inactive} inactive drivers`;
        }

        // Driver performance
        if (q.match(/best driver|top driver|highest score/)) {
            if (data.topDrivers.length === 0) {
                return data.drivers.list.length > 0 
                    ? "No drivers with high scores yet. Top scores need to be 85+."
                    : "No driver data available.";
            }
            return "Top drivers:\n" + data.topDrivers.map(d => 
                `• ${d.name}: ${d.safety_score}`
            ).join('\n');
        }

        if (q.match(/worst driver|bottom driver|lowest score/)) {
            if (data.bottomDrivers.length === 0) {
                return "No drivers with low scores (below 70).";
            }
            return "Drivers needing improvement:\n" + data.bottomDrivers.map(d => 
                `• ${d.name}: ${d.safety_score}`
            ).join('\n');
        }

        if (q.match(/average.*score|safety score average|mean score/)) {
            if (!data.drivers.avgScore) return "No safety score data yet.";
            return `Average safety score: ${data.drivers.avgScore} (${data.drivers.active} drivers)`;
        }

        // Order questions
        if (q.match(/how many order|order count|number of order/)) {
            return `${data.orders.active} active orders (total: ${data.orders.total})`;
        }

        if (q.match(/completed order|finished order/)) {
            const completed = data.orders.total - data.orders.active;
            return `${completed} completed orders`;
        }

        // Device questions
        if (q.match(/how many device|device count/)) {
            return `${data.devices.online} online devices (total: ${data.devices.total})`;
        }

        if (q.match(/offline device|disconnected/)) {
            const offline = data.devices.total - data.devices.online;
            return `${offline} offline devices`;
        }

        // Event/Alert questions
        if (q.match(/how many event|event count|total event/)) {
            return `${data.events.total} total events, ${data.events.critical} critical`;
        }

        if (q.match(/any alert|active alert|warning/)) {
            if (data.alerts.length === 0) return "All clear! No active alerts. ✅";
            
            const critical = data.alerts.filter(a => a.type === 'CRITICAL');
            const warnings = data.alerts.filter(a => a.type === 'WARNING');
            
            let resp = "";
            if (critical.length > 0) {
                resp += `🔴 ${critical.length} CRITICAL:\n`;
                resp += critical.slice(0, 2).map(a => `• ${a.event}`).join('\n');
            }
            if (warnings.length > 0) {
                if (resp) resp += "\n\n";
                resp += `🟡 ${warnings.length} warnings`;
            }
            return resp;
        }

        if (q.match(/critical event|serious issue/)) {
            return `${data.events.critical} critical events recorded`;
        }

        // Status/overview
        if (q.match(/status|overview|summary|how.*system/)) {
            if (data.drivers.total === 0) {
                return "System is running but no data recorded yet.";
            }
            return `Fleet Status:\n` +
                   `• Drivers: ${data.drivers.active}/${data.drivers.total} active\n` +
                   `• Orders: ${data.orders.active}/${data.orders.total} active\n` +
                   `• Devices: ${data.devices.online}/${data.devices.total} online\n` +
                   `• Alerts: ${data.alerts.length} active\n` +
                   `• Avg Score: ${data.drivers.avgScore || 'N/A'}`;
        }

        // Not a data question - return null to use Gemini
        return null;
    }

    /**
     * Build context for Gemini (for complex questions)
     */
    async buildGeminiContext(question) {
        const data = await this.getData();
        
        return {
            useGemini: true,
            prompt: `You are a helpful fleet assistant with access to this data:

FLEET DATA:
Drivers: ${data.drivers.active}/${data.drivers.total} active (avg score: ${data.drivers.avgScore || 'N/A'})
Orders: ${data.orders.active}/${data.orders.total} active
Devices: ${data.devices.online}/${data.devices.total} online
Alerts: ${data.alerts.length} active (${data.alerts.filter(a => a.type === 'CRITICAL').length} critical)

TOP DRIVERS:
${data.topDrivers.map(d => `• ${d.name}: ${d.safety_score}`).join('\n') || 'None yet'}

${data.bottomDrivers.length > 0 ? `NEEDS ATTENTION:\n${data.bottomDrivers.map(d => `• ${d.name}: ${d.safety_score}`).join('\n')}` : ''}

RULES:
• Answer directly using the data above
• No introductions or "As an AI..."
• If you reference specific numbers, use the data provided
• Plain text only
• 1-3 sentences

USER QUESTION: "${question}"`
        };
    }
}

// Initialize
window.SmartAIEngine = SmartAIEngine;
window.smartAI = new SmartAIEngine();
console.log('[AI Engine] Initialized with database access');
