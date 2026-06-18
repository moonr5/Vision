/** @type {import('jest').Config} */
module.exports = {
    testEnvironment: 'node',
    testMatch: ['<rootDir>/tests/**/*.test.js'],
    testTimeout: 30000,
    verbose: true,
    forceExit: true,
};
