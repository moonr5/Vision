const { loadDatabaseStack } = require('../helpers/browser-db-harness');

describe('database/dal.js', () => {
    let SGUDAL;

    beforeAll(async () => {
        ({ SGUDAL } = await loadDatabaseStack());
    });

    test('creates and retrieves a driver', () => {
        const driver = SGUDAL.Drivers.create({
            name: 'Alice Fleet',
            vehicle_plate: 'B 1234 XY',
            safety_score: 92,
            status: 'active',
        });

        expect(driver).toBeDefined();
        expect(driver.name).toBe('Alice Fleet');
        expect(driver.safety_score).toBe(92);

        const fetched = SGUDAL.Drivers.getById(driver.id);
        expect(fetched.vehicle_plate).toBe('B 1234 XY');
    });

    test('parses boolean settings correctly', () => {
        SGUDAL.Settings.set('test_flag', true, 'boolean');
        expect(SGUDAL.Settings.get('test_flag')).toBe(true);

        SGUDAL.Settings.set('test_flag', 'false', 'boolean');
        expect(SGUDAL.Settings.get('test_flag')).toBe(false);
    });

    test('logs and retrieves events', () => {
        SGUDAL.Devices.register({ device_id: 'device-01', name: 'Test Device' });

        const event = SGUDAL.Events.log({
            type: 'WARNING',
            event: 'Test Event',
            details: 'Harness check',
            speed: 45,
            device_id: 'device-01',
        });

        expect(event).toBeDefined();
        expect(event.type).toBe('WARNING');
        expect(event.event).toBe('Test Event');

        const rows = SGUDAL.Events.getAll();
        expect(rows.some((e) => e.event === 'Test Event')).toBe(true);
    });

    test('creates orders with generated order_id', () => {
        const order = SGUDAL.Orders.create({
            customer_name: 'ACME Logistics',
            status: 'transit',
            origin_city: 'Jakarta',
            destination_city: 'Bandung',
        });

        expect(order).toBeDefined();
        expect(order.order_id).toBeTruthy();
        expect(order.customer_name).toBe('ACME Logistics');
        expect(order.status).toBe('transit');
    });
});
