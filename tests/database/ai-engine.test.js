const { loadDatabaseStack } = require('../helpers/browser-db-harness');

describe('database/ai-engine.js', () => {
    let SGUDAL;
    let smartAI;

    beforeAll(async () => {
        ({ SGUDAL, smartAI } = await loadDatabaseStack());

        SGUDAL.Drivers.create({ name: 'Top Driver', safety_score: 95, status: 'active' });
        SGUDAL.Drivers.create({ name: 'Low Driver', safety_score: 62, status: 'active' });
        SGUDAL.Orders.create({ customer_name: 'Beta Corp', status: 'transit' });
    });

    test('answers driver count from local database without Gemini', async () => {
        const answer = await smartAI.answer('how many drivers do I have?');
        expect(answer).toMatch(/driver/i);
        expect(answer).toMatch(/\d/);
    });

    test('answers order count from local database', async () => {
        const answer = await smartAI.answer('how many orders?');
        expect(answer).toMatch(/order/i);
        expect(answer).toMatch(/\d/);
    });

    test('returns null for open-ended questions that need Gemini', async () => {
        const answer = await smartAI.answer('why is fleet safety culturally important?');
        expect(answer).toBeNull();
    });

    test('builds fleet context prompt from database', async () => {
        const prompt = await smartAI.buildDbContext();
        expect(prompt).toContain('FLEET DATABASE SNAPSHOT');
        expect(prompt).toMatch(/driver|order/i);
    });
});
