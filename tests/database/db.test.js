const { loadDatabaseStack } = require('../helpers/browser-db-harness');

describe('database/db.js', () => {
    let SGUDatabase;

    beforeAll(async () => {
        ({ SGUDatabase } = await loadDatabaseStack());
    });

    test('initializes and reports ready', () => {
        expect(SGUDatabase.isReady()).toBe(true);
    });

    test('has default settings after test harness alignment', () => {
        const broker = SGUDatabase.query(
            'SELECT value FROM settings WHERE key = ?',
            ['mqtt_broker']
        );
        expect(broker.length).toBe(1);
        expect(broker[0].value).toContain('broker.hivemq.com');
    });

    test('runs parameterized queries safely', () => {
        SGUDatabase.execute(
            'INSERT INTO drivers (id, name, status, safety_score) VALUES (?, ?, ?, ?)',
            ['drv_test_1', 'Test Driver', 'active', 88]
        );

        const rows = SGUDatabase.query('SELECT name, safety_score FROM drivers WHERE id = ?', [
            'drv_test_1',
        ]);
        expect(rows).toHaveLength(1);
        expect(rows[0].name).toBe('Test Driver');
        expect(rows[0].safety_score).toBe(88);
    });

    test('returns table statistics', () => {
        const stats = SGUDatabase.stats();
        expect(stats).toBeDefined();
        expect(typeof stats.drivers).toBe('number');
        expect(stats.drivers).toBeGreaterThanOrEqual(1);
    });
});
